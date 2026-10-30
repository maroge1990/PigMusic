"""The T3 test rig on Unraid (#15, D-53): a staging PigMusic with fake
metadata services and a disposable generated library.

    python -m scripts.test_rig up --image ghcr.io/maroge1990/pig-music@sha256:...
    python -m scripts.test_rig check | artist | mixes
    python -m scripts.test_rig status | down | reset

Limits (D-53): containers named pig-music-test-*, the app capped at 2 CPUs
and 2 GB, --restart no, data only under its own appdata folder. It never
touches the production container, its appdata, its image tag or the NAS:
the library is generated inside the rig's folder. The engine runs without
connecting, so every download search finds nothing. The app answers only
inside its container (no published port); the driver runs there.
"""

import argparse
import re
import shlex
import subprocess
from pathlib import Path

ROOT = "/mnt/user/appdata/pig-music-test"
NETWORK = "pig-music-test"
APP, FAKE = "pig-music-test-app", "pig-music-test-fake"
# Only an exact digest: pulling a tag such as :main would move that tag in
# the Docker daemon production shares (D-53, Codex round 1).
PINNED = re.compile(r"ghcr\.io/maroge1990/pig-music@sha256:[0-9a-f]{64}")
SCRIPTS = Path(__file__).resolve().parent
UPLOADS = ("fake_services.py", "rig_driver.py")


def household(key):
    from scripts import household as values

    return values.get(key)


def pinned(image):
    if not PINNED.fullmatch(image):
        raise SystemExit("Use an image digest: ghcr.io/maroge1990/pig-music@sha256:…")
    return image


def up_commands(image):
    image = pinned(image)
    owned = f"{ROOT}/data {ROOT}/library"
    return [
        f"mkdir -p {owned} {ROOT}/rig/scripts && chown -R 99:100 {owned}",
        f"docker rm -f {APP} {FAKE} >/dev/null 2>&1 || true",
        # Internal: the rig's containers reach each other, never the internet,
        # so no request can reach a real provider whatever the image does.
        f"docker network rm {NETWORK} >/dev/null 2>&1 || true",
        f"docker network create --internal {NETWORK} >/dev/null",
        f"docker pull -q {shlex.quote(image)} >/dev/null",
        # Only an image with the test-only switch (server/test_services.py).
        f"docker run --rm --network none --entrypoint python {shlex.quote(image)}"
        " -c 'import server.test_services'",
        f"docker run -d --name {FAKE} --network {NETWORK} --cpus 0.5 --memory 256m"
        f" --restart no --user 99:100 -v {ROOT}/rig:/rig:ro --entrypoint python"
        f" {shlex.quote(image)} /rig/scripts/fake_services.py --port 8790",
        f"docker run -d --name {APP} --network {NETWORK} --cpus 2 --memory 2g"
        f" --restart no --user 99:100"
        f" -e PIG_MUSIC_TEST_SERVICES=http://{FAKE}:8790"
        " -e PIG_MUSIC_ENGINE_NO_CONNECT=true -e PIG_MUSIC_PRELOAD=false"
        " -e PIG_MUSIC_LIBRARY=/library"
        f" -v {ROOT}/data:/data -v {ROOT}/library:/library -v {ROOT}/rig:/rig:ro"
        f" {shlex.quote(image)}",
    ]


def run_command(scenario, code):
    return (
        f"docker exec -e RIG_INSTALLATION_CODE={shlex.quote(code)} {APP}"
        f" python /rig/scripts/rig_driver.py {shlex.quote(scenario)}"
    )


def down_commands():
    return [
        f"docker rm -f {APP} {FAKE} >/dev/null 2>&1 || true",
        f"docker network rm {NETWORK} >/dev/null 2>&1 || true",
    ]


def reset_commands():
    # The folder is this rig's alone; the guard keeps a typo from widening it.
    assert ROOT.rstrip("/").endswith("/appdata/pig-music-test")
    return down_commands() + [f"rm -rf {ROOT}"]


STATUS = (
    "docker ps -a --filter name=pig-music-test- --format '{{.Names}} {{.Status}}';"
    " docker inspect PigMusic --format 'production"
    ' rev={{index .Config.Labels "org.opencontainers.image.revision"}}'
    " health={{.State.Health.Status}}'"
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", choices=["up", "check", "artist", "mixes", "status", "down", "reset"]
    )
    parser.add_argument("--image", help="ghcr.io/maroge1990/pig-music@sha256:…")
    parser.add_argument("--key", default=str(Path.home() / ".ssh/pig_music_unraid"))
    args = parser.parse_args()
    ssh = [
        "ssh",
        "-i",
        args.key,
        "-o",
        "BatchMode=yes",
        household("PIG_MUSIC_UNRAID_SSH"),
    ]

    def remote(command, **kwargs):
        return subprocess.run(ssh + [command], check=True, **kwargs)

    if args.command == "up":
        pinned(args.image or "")
        remote(up_commands(args.image)[0])
        for name in UPLOADS:
            with open(SCRIPTS / name, "rb") as source:
                remote(f"cat > {ROOT}/rig/scripts/{name}", stdin=source)
        for command in up_commands(args.image)[1:]:
            remote(command)
    elif args.command in ("check", "artist", "mixes"):
        logs = remote(f"docker logs {APP} 2>&1", capture_output=True, text=True).stdout
        lines = logs.splitlines()
        marker = next(
            (i for i, line in enumerate(lines) if "installation code" in line), None
        )
        code = lines[marker + 1].strip() if marker is not None else "unused"
        remote(run_command(args.command, code))
    elif args.command == "status":
        remote(STATUS)
    else:
        for command in down_commands() if args.command == "down" else reset_commands():
            remote(command)


if __name__ == "__main__":
    main()
