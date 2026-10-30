"""Routine release orchestration against a recorded fake Unraid host."""

import json
import importlib.util
import sys

import pytest

from source import SOURCE

spec = importlib.util.spec_from_file_location(
    "deploy_release",
    SOURCE / "scripts/deploy_release.py",
)
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)

IMAGE = "ghcr.io/maroge1990/pig-music@sha256:" + "a" * 64
REVISION = "b" * 40
SETTINGS = {"enabled": True, "profile": "lossless"}


def container(image):
    return {
        "Image": image,
        "Config": {
            "Labels": {"net.unraid.docker.managed": "dockerman"},
            "User": "99:100",
        },
        "HostConfig": {
            "ReadonlyRootfs": True,
            "Memory": 1073741824,
            "NanoCpus": 2000000000,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges"],
        },
        "Mounts": [{"Source": "/mnt/user/appdata", "Destination": "/data", "RW": True}],
        "NetworkSettings": {
            "Networks": {
                name: {"IPAddress": address}
                for name, (address, _) in deploy.EXAMPLE["networks"].items()
            }
        },
    }


class FakeUnraid:
    def __init__(
        self,
        owner=True,
        active=0,
        fail_on=None,
        state_bytes=100 << 20,
        available_kib=24 << 20,
    ):
        self.commands, self.settings = [], dict(SETTINGS)
        self.owner, self.active, self.fail_on = owner, active, fail_on
        self.running = "sha256:old"
        self.state_bytes, self.available_kib = state_bytes, available_kib

    def run(self, command, payload=None):
        self.commands.append(command)
        if self.fail_on and self.fail_on in command:
            raise RuntimeError("simulated failure: " + self.fail_on)
        if command.startswith("docker inspect PigMusic"):
            return json.dumps([container(self.running)])
        if command.startswith("du -scb"):
            return str(self.state_bytes)
        if "MemAvailable" in command:
            return str(self.available_kib)
        if command.startswith("docker image inspect"):
            return json.dumps(
                [
                    {
                        "Id": "sha256:new",
                        "Config": {
                            "Labels": {
                                "org.opencontainers.image.revision": REVISION,
                                "org.opencontainers.image.source": "https://github.com/maroge1990/Pig-Music",
                            }
                        },
                    }
                ]
            )
        if command.startswith("docker exec -i PigMusic"):
            if payload:
                self.settings = json.loads(payload)
            return json.dumps({"settings": self.settings, "active": self.active})
        if "curl" in command:
            if command.endswith("/api/v1/setup/owner'") or command.endswith(
                "/api/v1/setup/owner"
            ):
                return json.dumps({"setup_required": not self.owner})
            return json.dumps({"https": True, "storage_available": True})
        if "xmlToCommand" in command:
            tag = "ghcr.io/maroge1990/pig-music:sha-" + REVISION
            return json.dumps(["docker create --name PigMusic " + tag, "PigMusic", tag])
        if command.startswith("docker create"):
            self.running = "sha256:new"
        if "server.store import Store" in command:
            return json.dumps({"tracks": 3, "album_ids": ["a"]})
        if command.startswith("docker rename PigMusic-before-"):
            self.running = "sha256:old"
        return ""


class FakeResponse:
    def __init__(self, status, body=None):
        self.status_code, self.body = status, body or {}

    def raise_for_status(self):
        return self

    def json(self):
        return self.body


class FakeHTTP:
    def get(self, path):
        if path == "/api/health":
            return FakeResponse(200, {"status": "ok", "storage_available": True})
        return FakeResponse(401)


def release(unraid):
    return deploy.Release(
        unraid,
        IMAGE,
        REVISION,
        "test-release-v1",
        http=FakeHTTP(),
        sleep=lambda _: None,
        config=deploy.EXAMPLE,
    )


def index(commands, fragment):
    return next(i for i, c in enumerate(commands) if fragment in c)


def test_release_backs_up_verifies_and_restores_downloads():
    unraid = FakeUnraid()
    run = release(unraid)
    run.apply(*run.preflight())
    commands = unraid.commands
    stop = index(commands, "docker stop")
    backup = index(commands, "mkdir -m 700")
    verify = index(commands, "verify_state_migration.py")
    create = index(commands, "docker create")
    assert stop < backup < verify < create
    assert "--bootstrap-owner-fixture" not in commands[verify]
    # Downloads were paused for the switch and restored afterwards.
    assert unraid.settings == SETTINGS


def test_the_migration_checks_get_memory_for_the_state_they_copy():
    """30 Oct live release: the migration check copies the database and 758 MB
    of artwork onto a tmpfs counted against its fixed 1 GB limit and was
    killed (exit 137). Its limit is now the copied state plus 1 GiB."""
    unraid = FakeUnraid(state_bytes=900 << 20)
    run = release(unraid)
    run.apply(*run.preflight())
    commands = unraid.commands
    measured = commands[index(commands, "du -scb")]
    for name in deploy.MIGRATION_STATE:
        assert f"/backups/pre-test-release-v1/data/{name}" in measured
    for step in ("server.store import Store", "verify_state_migration.py"):
        command = commands[index(commands, step)]
        assert "--memory=1924m " in command and "--memory=1g" not in command
    assert index(commands, "du -scb") < index(commands, "verify_state_migration.py")


def test_state_too_big_for_the_hosts_free_memory_is_refused_before_the_checks():
    """Codex round 1 on 19cc2a9: the sized limit has no ceiling, and a
    container limit reserves nothing. The checks must fit in the host's
    available memory less a reserve, or the release stops and rolls back."""
    # 20 GiB of state needs 21 GiB; 24 GiB available less 4 GiB reserved is 20.
    unraid = FakeUnraid(state_bytes=20 << 30)
    run = release(unraid)
    with pytest.raises(RuntimeError, match="host memory"):
        run.apply(*run.preflight())
    commands = unraid.commands
    assert not any("docker run" in c for c in commands)
    assert index(commands, "MemAvailable") > index(commands, "du -scb")
    assert "docker start PigMusic >/dev/null" in commands
    assert unraid.settings == SETTINGS
    # Just inside the budget, the release goes ahead.
    unraid = FakeUnraid(state_bytes=19 << 30)
    run = release(unraid)
    run.apply(*run.preflight())
    assert any("verify_state_migration.py" in c for c in unraid.commands)


def test_migration_state_matches_what_the_check_copies():
    source = (SOURCE / "scripts/verify_state_migration.py").read_text()
    for name in deploy.MIGRATION_STATE:
        assert f'"{name}"' in source


def test_release_requires_an_owner_and_idle_transfers():
    with pytest.raises(AssertionError, match="Create the owner"):
        release(FakeUnraid(owner=False)).preflight()
    with pytest.raises(RuntimeError, match="Active transfers"):
        release(FakeUnraid(active=1)).preflight()


def test_failure_after_create_restores_data_container_and_settings():
    unraid = FakeUnraid(fail_on="cp /tmp/PigMusic-test-release-v1.xml")
    run = release(unraid)
    with pytest.raises(RuntimeError, match="simulated failure"):
        run.apply(*run.preflight())
    commands = unraid.commands
    failed = index(commands, "docker rename PigMusic PigMusic-failed-")
    restored = index(commands, "data.failed-test-release-v1")
    renamed = index(commands, "docker rename PigMusic-before-test-release-v1 PigMusic")
    assert failed < restored < renamed
    assert unraid.settings == SETTINGS


def test_retention_reports_without_deleting():
    class Listing:
        def __init__(self):
            self.commands = []

        def run(self, command, payload=None):
            self.commands.append(command)
            if command.startswith("ls"):
                return "\n".join(
                    [
                        "pre-r4",
                        "pre-r3",
                        "pre-first-run-20261007-v1",
                        "pre-r1",
                        "legacy-phase1a-20261006",
                    ]
                )
            return "\n".join(
                ["PigMusic-before-r4", "PigMusic-before-r1", "PigMusic-failed-r3"]
            )

    listing = Listing()
    report = deploy.retention(listing, keep=2)
    assert report["backups"] == ["pre-r1"]
    assert report["containers"] == ["PigMusic-before-r1"]
    assert "pre-first-run-20261007-v1" in report["kept"]
    assert not any(c.startswith(("rm", "docker rm")) for c in listing.commands)


def test_state_snippet_reads_and_pauses_downloads_in_sqlite(tmp_path):
    """The snippet runs inside the live container; execute it for real."""
    import sqlite3
    import subprocess
    import sys

    database = tmp_path / "pig-music.sqlite3"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE documents (name TEXT PRIMARY KEY, payload TEXT)")
        conn.execute("CREATE TABLE queue (id TEXT, status TEXT)")
        conn.execute(
            "INSERT INTO documents VALUES ('downloads', ?)", (json.dumps(SETTINGS),)
        )
        conn.execute(
            "INSERT INTO queue VALUES ('a', 'downloading'), ('b', 'completed')"
        )
    code = deploy.STATE.replace(deploy.DATABASE, str(database))

    def run(change=""):
        result = subprocess.run(
            [sys.executable, "-c", code],
            input=change,
            text=True,
            capture_output=True,
            check=True,
        )
        return json.loads(result.stdout)

    assert run() == {"settings": SETTINGS, "active": 1}
    paused = {**SETTINGS, "enabled": False}
    assert run(json.dumps(paused))["settings"] == paused
    assert run()["settings"] == paused


def test_baseline_snippet_compiles():
    compile(deploy.BASELINE, "baseline", "exec")


def test_a_release_without_household_values_fails_before_any_remote_command(
    tmp_path, monkeypatch
):
    """Codex round 1 on 4f666f3: a Release built outside main() used the
    example network values, so a direct rollback reconnected br0 to them."""
    try:
        from scripts import household
    except ImportError:
        # The image's scripts package has no household.py: load the checkout's
        # copy under the name deploy.household() falls back to.
        spec = importlib.util.spec_from_file_location(
            "household", SOURCE / "scripts/household.py"
        )
        household = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(household)
        monkeypatch.setitem(sys.modules, "household", household)

    monkeypatch.setattr(household, "FILE", tmp_path / "missing.env")
    for key in household.read(SOURCE / ".env.example"):
        monkeypatch.delenv(key, raising=False)
    unraid = FakeUnraid()
    with pytest.raises(household.HouseholdError):
        deploy.Release(unraid, IMAGE, REVISION, "v1", http=FakeHTTP())
    assert unraid.commands == []
