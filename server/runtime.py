"""Supervise the web service and its private Soulseek engine in one container."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from .transport import proxy_trust
from .storage import require_library_mount

# A crashed source engine is restarted after 5 s, doubling to 5 minutes; the
# delay resets once it has stayed up for 10 minutes.
ENGINE_BACKOFF_START = 5
ENGINE_BACKOFF_LIMIT = 300
ENGINE_HEALTHY_SECONDS = 600
ENGINE_HEAP_MB = 512


def start_engine(root):
    log = None
    try:
        engine = root / "engine"
        engine.mkdir(parents=True, exist_ok=True)
        if os.environ.get("PIG_MUSIC_EMBEDDED_ENGINE", "true").lower() == "true":
            if os.environ.get("PIG_MUSIC_DOWNLOAD_ROOT"):
                require_library_mount(
                    root,
                    Path(os.environ.get("PIG_MUSIC_LIBRARY", "/library")),
                    required=True,
                )
                work = Path(os.environ["PIG_MUSIC_DOWNLOAD_ROOT"])
                for name in ("engine-downloads", "engine-incomplete"):
                    (work / name).mkdir(parents=True, exist_ok=True)
            env = os.environ.copy()
            private = engine / "engine.env"
            if private.exists():
                for line in private.read_text().splitlines():
                    if "=" in line and not line.startswith("#"):
                        key, value = line.split("=", 1)
                        if key.startswith("SLSKD_"):
                            env[key] = value.strip().strip('"').strip("'")
            env.update(
                SLSKD_HTTP_IP_ADDRESS="127.0.0.1",
                SLSKD_HTTP_PORT="5030",
                SLSKD_NO_HTTPS="true",
                SLSKD_REMOTE_CONFIGURATION="false",
                SLSKD_APP_DIR=str(engine),
                SLSKD_DOWNLOADS_DIR=(
                    str(
                        Path(os.environ.get("PIG_MUSIC_DOWNLOAD_ROOT", str(engine)))
                        / "engine-downloads"
                    )
                    if os.environ.get("PIG_MUSIC_DOWNLOAD_ROOT")
                    else str(engine / "downloads")
                ),
                SLSKD_INCOMPLETE_DIR=(
                    str(
                        Path(os.environ.get("PIG_MUSIC_DOWNLOAD_ROOT", str(engine)))
                        / "engine-incomplete"
                    )
                    if os.environ.get("PIG_MUSIC_DOWNLOAD_ROOT")
                    else str(engine / "incomplete")
                ),
                DOTNET_BUNDLE_EXTRACT_BASE_DIR=str(engine / "bundle"),
                DOTNET_EnableDiagnostics="0",
                # 512 MB: a burst of compressed search replies ran a 256 MB
                # heap out of memory and aborted the engine (#89).
                DOTNET_GCHeapHardLimit=hex(ENGINE_HEAP_MB * 1024 * 1024),
                SLSKD_UMASK="077",
            )
            command = [
                "/opt/slskd/slskd",
                "--app-dir",
                str(engine),
                "--no-logo",
                "--no-version-check",
                "--no-https",
                "--http-ip-address",
                "127.0.0.1",
                "--http-port",
                "5030",
                "--headless",
            ]
            config = engine / "slskd.yml"
            if config.exists():
                command += ["--config", str(config)]
            if (
                not env.get("SLSKD_SLSK_USERNAME")
                or os.environ.get("PIG_MUSIC_ENGINE_NO_CONNECT") == "true"
            ):
                command += ["--no-connect"]
            logfile = engine / "runtime.log"
            if logfile.exists() and logfile.stat().st_size > 10_000_000:
                logfile.replace(engine / "runtime.previous.log")
            log = logfile.open("ab")
            os.chmod(logfile, 0o600)
            return (
                subprocess.Popen(
                    command,
                    cwd="/opt/slskd",
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                ),
                log,
            )
    except (OSError, ValueError):
        if log:
            log.close()
    return None, None


def main():
    server = None
    engine = None
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        stopping = True
        for child in (engine, server):
            if child and child.poll() is None:
                child.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    log = None
    try:
        root = Path(os.environ.get("PIG_MUSIC_DATA", "/data"))
        engine, log = start_engine(root)
        server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "server.app:app",
                "--host",
                "0.0.0.0",
                "--port",
                "8765",
                "--proxy-headers",
                "--forwarded-allow-ips",
                proxy_trust(),
                "--log-config",
                str(Path(__file__).with_name("logging.json")),
            ],
            env={
                key: value
                for key, value in os.environ.items()
                if key != "PIG_MUSIC_TEST_LEGACY_API"
            },
        )
        # The source engine is optional: browsing and saved playback keep working
        # while it is down, so it is restarted with backoff instead of taking the
        # server with it. Only the server exiting ends the container.
        backoff = ENGINE_BACKOFF_START
        started = time.monotonic()
        next_engine_check = 0
        while not stopping and server.poll() is None:
            if engine and engine.poll() is not None:
                if time.monotonic() - started >= ENGINE_HEALTHY_SECONDS:
                    backoff = ENGINE_BACKOFF_START
                print(
                    f"Source engine exited ({engine.returncode}); restarting in {backoff}s",
                    file=sys.stderr,
                    flush=True,
                )
                engine = None
                next_engine_check = time.monotonic() + backoff
                backoff = min(backoff * 2, ENGINE_BACKOFF_LIMIT)
            if (
                engine is None
                and os.environ.get("PIG_MUSIC_EMBEDDED_ENGINE", "true").lower()
                == "true"
                and time.monotonic() >= next_engine_check
            ):
                if log:
                    log.close()
                engine, log = start_engine(root)
                started = time.monotonic()
                if not engine:
                    next_engine_check = time.monotonic() + 5
            time.sleep(0.25)
        failed = not stopping
        stop(None, None)
        deadline = time.monotonic() + 25
        for child in (engine, server):
            if not child:
                continue
            try:
                child.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        return 1 if failed else 0
    finally:
        for child in (engine, server):
            if child and child.poll() is None:
                child.kill()
                child.wait()
        if log:
            log.close()


if __name__ == "__main__":
    sys.exit(main())
