"""Supervise the web service and its private Soulseek engine in one container."""

import http.client
import json
import os
import signal
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

from .transport import proxy_trust
from .storage import require_library_mount

# A crashed source engine is restarted after 5 s, doubling to 5 minutes; the
# delay resets once it has stayed up for 10 minutes.
ENGINE_BACKOFF_START = 5
ENGINE_BACKOFF_LIMIT = 300
ENGINE_HEALTHY_SECONDS = 600
ENGINE_HEAP_MB = 512
# The server's own log file (#148, D-75): two files of 5 MB, so about 10 MB
# survives restarts in the data folder; the container log is unchanged.
LOG_FILE_BYTES = 5 * 1024 * 1024
LOG_FILE_BACKUPS = 1
# The watchdog (#238) asks the server whether it is ready every 30 s once it
# has had 3 minutes to start. Ten probes in a row without any answer (300 s)
# end the server, so Docker restarts the container; at most 3 times an hour.
READY_URL = "http://127.0.0.1:8765/api/health/ready"
WATCHDOG_GRACE = 180
WATCHDOG_INTERVAL = 30
WATCHDOG_PROBE_TIMEOUT = 10
WATCHDOG_SILENT_SECONDS = 300
WATCHDOG_RESTART_LIMIT = 3
WATCHDOG_RESTART_WINDOW = 3600
WATCHDOG_REMINDER_SECONDS = 600
RESTART_HISTORY_ENTRIES = 50


def log_config(root):
    """logging.json plus a rotating file in <data>/logs, written beside it;
    returns the written config's path for uvicorn's --log-config."""
    packaged = Path(__file__).with_name("logging.json")
    try:
        return _file_log_config(packaged, Path(root) / "logs")
    except OSError as error:
        # The file is optional: never let it keep the server from starting.
        print(
            f"Server log file unavailable ({error}); logging to the console only",
            file=sys.stderr,
            flush=True,
        )
        return packaged


def _file_log_config(packaged, logs):
    config = json.loads(packaged.read_text())
    logs.mkdir(parents=True, exist_ok=True)
    # Opened now, so a file that can't be written falls back here rather
    # than failing uvicorn's own logging setup.
    with open(logs / "pig-music.log", "a", encoding="utf-8"):
        pass
    config["formatters"]["file"] = {
        "format": "%(asctime)s %(levelname)s %(name)s: %(message)s"
    }
    config["handlers"]["file"] = {
        "class": "logging.handlers.RotatingFileHandler",
        "formatter": "file",
        # The same filters as the console: no secrets, no successful health check.
        "filters": ["health", "credentials"],
        "filename": str(logs / "pig-music.log"),
        "maxBytes": LOG_FILE_BYTES,
        "backupCount": LOG_FILE_BACKUPS,
        "encoding": "utf-8",
    }
    for name in ("uvicorn", "uvicorn.access"):
        config["loggers"][name]["handlers"].append("file")
    config["root"]["handlers"].append("file")
    path = logs / "logging.json"
    path.write_text(json.dumps(config, indent=2))
    return path


def say(line):
    print(line, file=sys.stderr, flush=True)


def probe(url=READY_URL, timeout=WATCHDOG_PROBE_TIMEOUT):
    """Ask the server whether it is ready: ("ok", []), ("degraded", [what it
    reports as failing]) for any other HTTP answer, or ("silent", []) when it
    does not answer at all (refused, reset or timed out)."""
    target = urllib.parse.urlsplit(url)
    connection = http.client.HTTPConnection(
        target.hostname, target.port or 80, timeout=timeout
    )
    try:
        connection.request("GET", target.path)
        response = connection.getresponse()
        if response.status == 200:
            return "ok", []
        try:
            checks = json.loads(response.read(4096))["checks"]
            failing = sorted(name for name, passed in checks.items() if not passed)
        except Exception:
            failing = []
        return "degraded", failing or [f"HTTP {response.status}"]
    except (OSError, http.client.HTTPException):
        return "silent", []
    finally:
        connection.close()


def restart_history(path):
    """The entries of logs/restarts.json; anything unreadable is no history."""
    try:
        entries = json.loads(Path(path).read_text())
        return [
            entry
            for entry in entries
            if isinstance(entry, dict)
            and isinstance(entry.get("at"), (int, float))
            and not isinstance(entry.get("at"), bool)
        ]
    except Exception:
        return []


def record_restart(path, at, kind, detail):
    """Append {"at", "kind": "watchdog"|"exit", "detail"}, keeping the newest
    entries. Never raises: returns whether the entry was written."""
    try:
        path = Path(path)
        entries = restart_history(path)
        entries.append({"at": at, "kind": kind, "detail": detail})
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(path.name + ".part")
        partial.write_text(json.dumps(entries[-RESTART_HISTORY_ENTRIES:], indent=1))
        partial.replace(path)
        return True
    except Exception:
        return False


def exit_reason(code):
    if code is not None and code < 0:
        try:
            name = signal.Signals(-code).name
        except ValueError:
            name = "unknown"
        if -code == signal.SIGKILL:
            name += ": possibly out of memory"
        return f"Server was ended by signal {-code} ({name})"
    return f"Server exited with code {code}"


class Watchdog:
    """Decides what a probe's result means. `due` says when to probe;
    `observe` takes the result and returns "restart" when the server should
    be ended. It never sleeps, probes or ends anything itself."""

    def __init__(self, mode, started, history, say=say):
        self.mode = mode if mode in {"restart", "log", "off"} else "restart"
        self.history = history
        self.say = say
        if mode != self.mode:
            say(f"PIG_MUSIC_WATCHDOG={mode} is not restart, log or off; using restart")
        self.next_probe = started + WATCHDOG_GRACE
        self.silent = 0
        self.degraded = False
        self.reminded = None

    def due(self, now):
        if self.mode == "off" or now < self.next_probe:
            return False
        self.next_probe = now + WATCHDOG_INTERVAL
        return True

    def observe(self, now, wall, state, failing=()):
        """`now` is the monotonic clock, `wall` the time of day (the restart
        history outlives this process)."""
        if state != "silent":
            self.silent = 0
            self.reminded = None
            if state == "ok" and self.degraded:
                self.degraded = False
                self.say("Server reports ok again")
            elif state == "degraded" and not self.degraded:
                self.degraded = True
                self.say("Server reports unavailable: " + ", ".join(failing))
            return None
        self.silent += 1
        if self.silent * WATCHDOG_INTERVAL < WATCHDOG_SILENT_SECONDS:
            return None
        unanswered = f"Server has not answered for {WATCHDOG_SILENT_SECONDS} s"
        refusal = None
        if self.mode == "log":
            refusal = "not restarting it (PIG_MUSIC_WATCHDOG=log)"
        else:
            recent = [
                entry
                for entry in restart_history(self.history)
                if entry.get("kind") == "watchdog"
                and 0 <= wall - entry["at"] < WATCHDOG_RESTART_WINDOW
            ]
            if len(recent) >= WATCHDOG_RESTART_LIMIT:
                refusal = (
                    f"not restarting again: {WATCHDOG_RESTART_LIMIT} restarts"
                    " in the last hour"
                )
            # The limit lives in the history: without a written entry the
            # restart could repeat every few minutes for ever, so it is
            # recorded first and not done when that fails.
            elif not record_restart(self.history, wall, "watchdog", unanswered):
                refusal = "not restarting it: logs/restarts.json cannot be written"
        if refusal:
            if (
                self.reminded is None
                or now - self.reminded >= WATCHDOG_REMINDER_SECONDS
            ):
                self.reminded = now
                self.say(f"{unanswered}; {refusal}")
            return None
        self.say(f"{unanswered}; restarting it")
        return "restart"


class _Stopping(BaseException):
    """Raised by the stop signal to leave a probe that is still waiting."""


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
    probing = False

    def stop(signum, frame):
        nonlocal stopping
        first = not stopping
        stopping = True
        for child in (engine, server):
            if child and child.poll() is None:
                child.terminate()
        # A probe can wait 10 s for a server that is not answering: the
        # signal ends that wait, so stopping never queues behind it.
        if probing and first and signum is not None:
            raise _Stopping()

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
                str(log_config(root)),
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
        history = root / "logs" / "restarts.json"
        watchdog = Watchdog(
            (os.environ.get("PIG_MUSIC_WATCHDOG") or "restart").strip().lower(),
            started,
            history,
        )
        unanswered = False
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
            if watchdog.due(time.monotonic()):
                # Done here rather than on a thread: one probe holds the loop
                # for at most its 10 s timeout, once every 30 s.
                try:
                    try:
                        probing = True
                        state, failing = probe()
                    finally:
                        probing = False
                except _Stopping:
                    continue
                if (
                    not stopping
                    and watchdog.observe(time.monotonic(), time.time(), state, failing)
                    == "restart"
                ):
                    unanswered = True
                    break
            time.sleep(0.25)
        failed = not stopping
        if failed and not unanswered:
            reason = exit_reason(server.poll())
            say(reason)
            record_restart(history, time.time(), "exit", reason)
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
