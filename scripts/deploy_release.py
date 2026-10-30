"""Routine PigMusic release under native Unraid Docker Manager.

For account releases after the one-time first-run switch. Requires exact-image
Unraid acceptance. Dry-run preflight by default; ``--apply`` quiesces
transfers, takes a fresh stopped appdata/template backup, verifies migration on
a disposable copy, recreates the container from the reviewed template and keeps
the previous container for rollback. Any failure restores the complete stopped
state. ``--retention`` only reports old recovery material; it never deletes.
"""

import argparse
import json
from pathlib import Path
import re
import shlex
import subprocess
import time
from urllib.parse import urlsplit

import httpx

ROOT = "/mnt/user/appdata/pig-music-preview"
TEMPLATE = "/boot/config/plugins/dockerMan/templates-user/my-PigMusic.xml"
# Reserved example values, used only when a test passes them explicitly. A
# Release built without a config loads the real household values
# (scripts/household.py) and fails before any remote command if one is missing.
EXAMPLE = {
    "origin": "https://pigmusic.example-tailnet.ts.net:10000",
    "tailnet_address": "100.64.0.1",
    "server_name": "Example",
    "lan_url": "http://10.0.0.3:8765",
    "networks": {"br0": ("10.0.0.3", "-1"), "pig-music-preview": ("172.25.0.2", "0")},
}
# The live container must keep a memory limit; release gates still test under 1 GiB.
MEMORY_LIMITS = range(1 << 30, (8 << 30) + 1)
# What the baseline and migration checks copy from the backup onto their own
# tmpfs (scripts/verify_state_migration.py). The tmpfs counts against each
# check's memory limit, so the limit is sized from this state plus 1 GiB.
MIGRATION_STATE = (
    "pig-music.sqlite3",
    "pig-music.sqlite3-wal",
    "pig-music.sqlite3-shm",
    "library-snapshot.json",
    "media-settings.json",
    "covers",
)
# Host memory the checks may not use: a container limit reserves nothing, so
# the sized limit must fit in what the host has free, less this reserve.
HOST_RESERVE_MIB = 4096
ACTIVE = ("searching", "downloading", "validating", "importing")
IMAGE = re.compile(r"ghcr.io/maroge1990/pig-music@sha256:[0-9a-f]{64}")
DATABASE = "/data/pig-music.sqlite3"

# Reads/writes the live download setting and queue through SQLite inside the
# running container: the HTTP API now requires an account session.
STATE = (
    "import json,sqlite3,sys; c=sqlite3.connect('" + DATABASE + "',timeout=10); "
    "row=c.execute(\"SELECT payload FROM documents WHERE name='downloads'\").fetchone(); "
    "settings=json.loads(row[0]) if row else {}; "
    "change=sys.stdin.read().strip(); "
    "new=json.loads(change) if change else None; "
    "new is not None and c.execute(\"INSERT INTO documents VALUES ('downloads',?) ON CONFLICT(name) DO UPDATE SET payload=excluded.payload\",(json.dumps(new),)); "
    "c.commit(); "
    "settings=new if new is not None else settings; "
    'active=c.execute("SELECT count(*) FROM queue WHERE status IN '
    + str(ACTIVE)
    + '").fetchone()[0]; '
    "print(json.dumps({'settings':settings,'active':active}))"
)
# Baseline from the stopped backup, computed by the image that wrote it.
BASELINE = (
    "import json,shutil; from pathlib import Path; from server.store import Store; "
    "d=Path('/tmp/baseline'); d.mkdir(); "
    "[shutil.copyfile(Path('/backup')/n,d/n) for n in "
    "('pig-music.sqlite3','pig-music.sqlite3-wal','pig-music.sqlite3-shm',"
    "'library-snapshot.json','media-settings.json') if (Path('/backup')/n).exists()]; "
    "s=Store(d); "
    "print(json.dumps({'tracks':s.summary()['stats']['tracks'],'album_ids':[a['id'] for a in s.library['albums']]}))"
)


def household(key):
    """A real household value; imported lazily so tests need no .private/."""
    try:
        from scripts import household as values
    except ImportError:
        import household as values
    return values.get(key)


def load_household():
    """The live household's release values; raises if any is missing."""
    lan = household("PIG_MUSIC_LAN_ADDRESS")
    return {
        "origin": household("PIG_MUSIC_TAILNET_ORIGIN"),
        "tailnet_address": household("PIG_MUSIC_TAILNET_ADDRESS"),
        "server_name": household("PIG_MUSIC_UNRAID_NAME"),
        "lan_url": household("PIG_MUSIC_LAN_URL"),
        "networks": {"br0": (lan, "-1"), "pig-music-preview": ("172.25.0.2", "0")},
    }


class Unraid:
    """Thin SSH runner; tests substitute a recording fake."""

    def __init__(self):
        self.ssh = [
            "ssh",
            "-i",
            str(Path.home() / ".ssh/pig_music_unraid"),
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            household("PIG_MUSIC_UNRAID_SSH"),
        ]

    def run(self, command, payload=None):
        return subprocess.check_output(
            [*self.ssh, command], text=True, input=payload
        ).strip()


class Release:
    def __init__(
        self, unraid, image, revision, version, http=None, sleep=time.sleep, config=None
    ):
        self.unraid, self.image, self.revision, self.version = (
            unraid,
            image,
            revision,
            version,
        )
        # Fail closed: every method uses these, never module-level examples.
        config = config or load_household()
        self.origin = config["origin"]
        self.tailnet_address = config["tailnet_address"]
        self.server_name = config["server_name"]
        self.networks = config["networks"]
        self.http = http or httpx.Client(base_url=config["lan_url"], timeout=20)
        self.sleep = sleep
        self.backup = f"{ROOT}/backups/pre-{version}"
        self.previous = f"PigMusic-before-{version}"
        self.failed = f"PigMusic-failed-{version}"
        self.stage = f"{ROOT}/releases/{version}"
        self.reviewed = f"/tmp/PigMusic-{version}.xml"
        self.tag = f"ghcr.io/maroge1990/pig-music:sha-{revision}"

    def remote(self, command, payload=None):
        return self.unraid.run(command, payload)

    def inspect(self, name):
        return json.loads(self.remote("docker inspect " + shlex.quote(name)))[0]

    def state(self, change=None):
        return json.loads(
            self.remote(
                "docker exec -i PigMusic python -c " + shlex.quote(STATE),
                json.dumps(change) if change is not None else "",
            )
        )

    def private(self, path):
        origin = urlsplit(self.origin)
        return json.loads(
            self.remote(
                "curl --max-time 15 --fail --silent --show-error --resolve "
                + shlex.quote(
                    f"{origin.hostname}:{origin.port or 443}:{self.tailnet_address}"
                )
                + " "
                + shlex.quote(self.origin + path)
            )
        )

    def php(self, code):
        return self.remote("php -d short_open_tag=1 -r " + shlex.quote(code))

    def preflight(self):
        live = self.inspect("PigMusic")
        candidate = json.loads(
            self.remote("docker image inspect " + shlex.quote(self.image))
        )[0]
        labels = candidate["Config"]["Labels"]
        assert labels["org.opencontainers.image.revision"] == self.revision
        assert (
            labels["org.opencontainers.image.source"]
            == "https://github.com/maroge1990/Pig-Music"
        )
        assert live["Image"] != candidate["Id"], "Candidate is already running"
        assert live["Config"]["Labels"]["net.unraid.docker.managed"] == "dockerman"
        assert live["HostConfig"]["ReadonlyRootfs"]
        assert live["HostConfig"]["Memory"] in MEMORY_LIMITS
        assert live["Config"]["User"] == "99:100"
        networks = live["NetworkSettings"]["Networks"]
        for network, (address, _) in self.networks.items():
            assert networks[network]["IPAddress"] == address
        self.remote(
            "test ! -e "
            + shlex.quote(self.backup)
            + " && test -f "
            + shlex.quote(self.stage + "/source.tar.gz")
        )
        self.remote(
            "! docker inspect " + shlex.quote(self.previous) + " >/dev/null 2>&1"
        )
        health = self.private("/api/health")
        assert health["storage_available"] and health["https"]
        assert self.private("/api/v1/setup/owner") == {
            "setup_required": False
        }, "Create the owner in the WebUI before routine releases"
        state = self.state()
        if state["active"]:
            raise RuntimeError("Active transfers; release deferred")
        return live, candidate, state["settings"]

    def wait_ready(self):
        for _ in range(40):
            try:
                health = self.http.get("/api/health").raise_for_status().json()
                if health.get("status") == "ok" and health.get("storage_available"):
                    return
            except httpx.HTTPError:
                pass
            self.sleep(1)
        raise RuntimeError("Readiness/storage check failed")

    def apply(self, live, candidate, original):
        flags = dict.fromkeys(
            (
                "paused",
                "stopped",
                "backed_up",
                "migrated",
                "renamed",
                "created",
                "template",
            ),
            False,
        )
        try:
            self.state({**original, "enabled": False})
            flags["paused"] = True
            if self.state()["active"]:
                raise RuntimeError("Transfer started; release deferred")
            self.remote("docker stop --timeout 30 PigMusic >/dev/null")
            flags["stopped"] = True
            self.remote(
                "set -e\nmkdir -m 700 "
                + shlex.quote(self.backup)
                + f"\ncp -a {ROOT}/data "
                + shlex.quote(self.backup + "/data")
                + f"\ncp {TEMPLATE} "
                + shlex.quote(self.backup + "/PigMusic.xml")
                + "\ncp /var/lib/docker/unraid-autostart "
                + shlex.quote(self.backup + "/unraid-autostart")
            )
            flags["backed_up"] = True
            # A fixed 1 GB limit killed the check once artwork reached 758 MB.
            copied = int(
                self.remote(
                    "du -scb "
                    + " ".join(
                        shlex.quote(f"{self.backup}/data/{name}")
                        for name in MIGRATION_STATE
                    )
                    + " 2>/dev/null | tail -1 | cut -f1"
                )
            )
            needed = (copied >> 20) + 1024
            available = int(
                self.remote("awk '/^MemAvailable:/ {print $2}' /proc/meminfo")
            )
            if needed > (available >> 10) - HOST_RESERVE_MIB:
                raise RuntimeError(
                    f"Migration checks need {needed} MiB; host memory has "
                    f"{available >> 10} MiB available and keeps {HOST_RESERVE_MIB} MiB"
                )
            memory = f"--memory={needed}m "
            baseline = self.remote(
                "docker run --rm --network none " + memory + "--user 99:100 "
                "--tmpfs /tmp:rw,uid=99,gid=100 -e PYTHONDONTWRITEBYTECODE=1 -v "
                + shlex.quote(self.backup + "/data:/backup:ro")
                + " --entrypoint python "
                + shlex.quote(live["Image"])
                + " -c "
                + shlex.quote(BASELINE)
            )
            self.remote("cat > " + shlex.quote(self.stage + "/expected.json"), baseline)
            self.remote(
                "docker run --rm --network none " + memory + "--user 99:100 "
                "--tmpfs /data:rw,uid=99,gid=100 -e PYTHONDONTWRITEBYTECODE=1 "
                "-e PYTHONPATH=/app -v "
                + shlex.quote(self.backup + "/data:/backup:ro")
                + " -v "
                + shlex.quote(self.stage + ":/release:ro")
                + " --entrypoint python "
                + shlex.quote(self.image)
                + " /app/scripts/verify_state_migration.py --source /backup"
                " --destination /data/migration --expected /release/expected.json"
            )
            # Live data is migrated by the new image on start; restore the
            # original download setting first so the release resumes as before.
            flags["migrated"] = True
            # Registry verification pulls by digest; fetch the template tag too.
            self.remote("docker pull --quiet " + shlex.quote(self.tag) + " >/dev/null")
            assert (
                json.loads(
                    self.remote("docker image inspect " + shlex.quote(self.tag))
                )[0]["Id"]
                == candidate["Id"]
            )
            self.php(
                "$x=simplexml_load_file("
                + json.dumps(TEMPLATE)
                + "); $x->Repository="
                + json.dumps(self.tag)
                + "; if ($x->asXML("
                + json.dumps(self.reviewed)
                + ")===false) exit(1);"
            )
            generator = (
                '$docroot="/usr/local/emhttp"; $var=["timeZone"=>"Australia/Melbourne","NAME"=>'
                + json.dumps(self.server_name)
                + "]; "
                '$subnet=["pig-music-preview"=>"172.25.0.0/16"]; $driver=["pig-music-preview"=>"bridge"]; '
                'require "/usr/local/emhttp/plugins/dynamix.docker.manager/include/Helpers.php"; '
                "echo json_encode(xmlToCommand(file_get_contents("
                + json.dumps(self.reviewed)
                + ")));"
            )
            command, name, repository = json.loads(self.php(generator))
            assert name == "PigMusic" and repository == self.tag
            self.remote("docker rename PigMusic " + self.previous)
            flags["renamed"] = True
            self.remote(
                "set -e\n"
                + "\n".join(
                    f"docker network disconnect {n} {self.previous}"
                    for n in self.networks
                )
            )
            self.remote(command)
            flags["created"] = True
            self.remote("cp " + shlex.quote(self.reviewed) + " " + TEMPLATE)
            flags["template"] = True
            self.remote("docker start PigMusic >/dev/null")
            self.wait_ready()
            self.state(original)
            health = self.private("/api/health")
            assert health["https"] is True and health["storage_available"]
            assert self.private("/api/v1/setup/owner") == {"setup_required": False}
            assert self.http.get("/api/v1/admin/library").status_code in {401, 403}
            self.verify_container(live, candidate)
            print("Release is live:", self.version, "; backup:", self.backup)
            print("Rollback container:", self.previous, flush=True)
        except Exception:
            print("Release failed; restoring the complete stopped state.", flush=True)
            self.rollback(flags, original)
            raise

    def verify_container(self, live, candidate):
        after = self.inspect("PigMusic")
        assert after["Image"] == candidate["Id"]
        assert after["Config"]["Labels"]["net.unraid.docker.managed"] == "dockerman"
        assert after["Config"]["User"] == "99:100"
        for key in ("ReadonlyRootfs", "Memory", "NanoCpus", "CapDrop", "SecurityOpt"):
            assert after["HostConfig"][key] == live["HostConfig"][key], key
        mounts = lambda c: {
            (m["Source"], m["Destination"], m["RW"]) for m in c["Mounts"]
        }
        assert mounts(after) == mounts(live)
        for network, (address, _) in self.networks.items():
            assert after["NetworkSettings"]["Networks"][network]["IPAddress"] == address

    def rollback(self, flags, original):
        if flags["renamed"]:
            present = self.remote(
                "if docker inspect PigMusic >/dev/null 2>&1; then echo present; fi"
            )
            if flags["created"] or present == "present":
                self.remote("docker stop --timeout 30 PigMusic >/dev/null 2>&1 || true")
                for network in self.networks:
                    self.remote(
                        f"docker network disconnect {network} PigMusic >/dev/null 2>&1 || true"
                    )
                self.remote("docker rename PigMusic " + self.failed)
        if flags["created"] and flags["backed_up"]:
            # The new image may have migrated live data on start.
            self.remote(
                f"set -e\nmv {ROOT}/data {ROOT}/data.failed-{self.version}\ncp -a "
                + shlex.quote(self.backup + "/data")
                + f" {ROOT}/data"
            )
        if flags["template"]:
            self.remote(
                "cp " + shlex.quote(self.backup + "/PigMusic.xml") + " " + TEMPLATE
            )
        if flags["renamed"]:
            self.remote("docker rename " + self.previous + " PigMusic")
            current = self.inspect("PigMusic")["NetworkSettings"]["Networks"]
            for network, (address, priority) in self.networks.items():
                if network not in current:
                    self.remote(
                        f"docker network connect --ip {address} --gw-priority {priority} {network} PigMusic"
                    )
        if flags["stopped"]:
            self.remote("docker start PigMusic >/dev/null")
            self.wait_ready()
        if flags["paused"]:
            self.state(original)


def retention(unraid, keep):
    """Report recovery material beyond the newest ``keep`` releases; never deletes."""
    backups = unraid.run(f"ls -1t {ROOT}/backups 2>/dev/null || true").split()
    containers = unraid.run(
        "docker ps -a --format '{{.Names}}' | grep -E -- '-(before|failed)-' || true"
    ).split()
    # The pre-account baseline and legacy manager material are always kept.
    protected = {"legacy-phase1a-20261006", "pre-first-run-20261007-v1"}
    kept = set(backups[:keep]) | (protected & set(backups))
    versions = {b.removeprefix("pre-") for b in kept}
    report = {
        "kept": sorted(kept),
        "backups": [b for b in backups if b not in kept],
        "containers": [
            c
            for c in containers
            if re.split(r"-(?:before|failed)-", c)[-1] not in versions
        ],
    }
    print(json.dumps(report, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image")
    parser.add_argument("--revision")
    parser.add_argument("--version")
    parser.add_argument("--verified-log", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--retention", type=int, metavar="KEEP")
    args = parser.parse_args()
    unraid = Unraid()
    if args.retention is not None:
        retention(unraid, args.retention)
        return
    if not args.image or not IMAGE.fullmatch(args.image):
        parser.error("Use the immutable PigMusic registry digest")
    if not re.fullmatch(r"[0-9a-f]{40}", args.revision or "") or not re.fullmatch(
        r"[a-z0-9-]{6,70}", args.version or ""
    ):
        parser.error("Invalid revision/release name")
    evidence = args.verified_log.read_text() if args.verified_log else ""
    if (
        "Candidate passed isolated Linux tests, artwork and HTTP delivery scale gates: "
        + args.image
        not in evidence
    ):
        parser.error("Exact-image Unraid acceptance has not completed")
    release = Release(unraid, args.image, args.revision, args.version)
    live, candidate, original = release.preflight()
    print("Preflight passed: exact image, native manager, owner ready, no transfers.")
    if args.apply:
        release.apply(live, candidate, original)


if __name__ == "__main__":
    main()
