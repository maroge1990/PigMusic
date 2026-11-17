"""The optional source engine never takes the web server down with it, and a
server that stops answering is noticed, explained and restarted (#238)."""

import json
import socket
from pathlib import Path
import threading
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import server
from server import runtime
from server.security import FORWARDED


class FakeProcess:
    def __init__(self, lifetime, code=1):
        self.lifetime = lifetime
        self.code = code
        self.returncode = None

    def poll(self):
        if self.returncode is None and self.lifetime is not None:
            self.lifetime -= 1
            if self.lifetime <= 0:
                self.returncode = self.code
        return self.returncode

    def terminate(self):
        self.returncode = 0

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


def test_engine_crash_restarts_with_backoff_while_server_keeps_running(
    tmp_path, monkeypatch
):
    clock = [0.0]
    engines = []
    server = FakeProcess(lifetime=400)

    def start_engine(root):
        engines.append((clock[0], FakeProcess(lifetime=3)))
        return engines[-1][1], None

    def sleep(seconds):
        clock[0] += 1

    monkeypatch.setenv("PIG_MUSIC_DATA", str(tmp_path))
    monkeypatch.setenv("PIG_MUSIC_EMBEDDED_ENGINE", "true")
    monkeypatch.setattr(runtime, "start_engine", start_engine)
    monkeypatch.setattr(runtime, "proxy_trust", lambda: "127.0.0.1")
    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *a, **k: server)
    monkeypatch.setattr(runtime.time, "sleep", sleep)
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(runtime.signal, "signal", lambda *a: None)
    monkeypatch.setattr(runtime, "probe", lambda: ("ok", []), raising=False)

    # The server outlives repeated engine crashes; only its own exit ends main.
    assert runtime.main() == 1
    starts = [at for at, _ in engines]
    assert len(starts) >= 4
    gaps = [later - earlier for earlier, later in zip(starts, starts[1:])]
    # Each restart waits longer than the last, up to the limit.
    assert gaps == sorted(gaps)
    assert gaps[1] > gaps[0]


def test_the_engine_heap_has_room_for_a_burst_of_search_replies(tmp_path, monkeypatch):
    """#89: with a 256 MB .NET heap the engine aborted on OutOfMemory while
    decompressing many search replies at once (the container had 3 GB free)."""
    launched = {}

    def popen(command, env, **kwargs):
        launched.update(env)
        kwargs["stdout"].close()
        return "engine"

    monkeypatch.setattr(runtime.subprocess, "Popen", popen)
    monkeypatch.delenv("PIG_MUSIC_DOWNLOAD_ROOT", raising=False)
    monkeypatch.setenv("PIG_MUSIC_EMBEDDED_ENGINE", "true")
    process, log = runtime.start_engine(tmp_path)
    assert process == "engine"
    assert int(launched["DOTNET_GCHeapHardLimit"], 16) >= 512 * 1024 * 1024


class Supervised:
    """main() with a fake server child, a fake clock (one second a tick) and
    a scripted probe in place of the HTTP request."""

    def __init__(self, tmp_path, monkeypatch, server, answer):
        self.clock = [0.0]
        self.server = server
        self.probes = []
        self.handlers = {}
        self.history = tmp_path / "logs" / "restarts.json"

        def probe():
            self.probes.append(self.clock[0])
            return answer(self)

        def sleep(seconds):
            self.clock[0] += 1

        monkeypatch.setenv("PIG_MUSIC_DATA", str(tmp_path))
        monkeypatch.setenv("PIG_MUSIC_EMBEDDED_ENGINE", "false")
        monkeypatch.setattr(runtime, "start_engine", lambda root: (None, None))
        monkeypatch.setattr(runtime, "proxy_trust", lambda: "127.0.0.1")
        monkeypatch.setattr(runtime.subprocess, "Popen", lambda *a, **k: server)
        monkeypatch.setattr(runtime.time, "sleep", sleep)
        monkeypatch.setattr(runtime.time, "monotonic", lambda: self.clock[0])
        monkeypatch.setattr(runtime.time, "time", lambda: 1_000_000 + self.clock[0])
        monkeypatch.setattr(runtime.signal, "signal", self.handlers.__setitem__)
        monkeypatch.setattr(runtime, "probe", probe, raising=False)

    def entries(self):
        return json.loads(self.history.read_text())


def silent(run):
    return "silent", []


def test_a_server_silent_for_300_seconds_is_ended_and_the_restart_recorded(
    tmp_path, monkeypatch, capsys
):
    run = Supervised(tmp_path, monkeypatch, FakeProcess(lifetime=None), silent)
    assert runtime.main() == 1
    # Ten probes, 30 s apart, the first after the 180 s startup grace.
    assert run.probes == [180 + 30 * n for n in range(10)]
    assert run.server.returncode == 0  # terminated, not left to exit
    [entry] = run.entries()
    assert entry["kind"] == "watchdog" and entry["at"] == 1_000_000 + 450
    err = capsys.readouterr().err
    assert "Server has not answered for 300 s; restarting it" in err
    # The watchdog's own restart is not also reported as the server exiting.
    assert "Server exited" not in err and "ended by signal" not in err


def test_a_silent_server_is_left_alone_while_it_starts(tmp_path, monkeypatch, capsys):
    run = Supervised(tmp_path, monkeypatch, FakeProcess(lifetime=170), silent)
    assert runtime.main() == 1
    assert run.probes == []
    assert "not answered" not in capsys.readouterr().err
    dog = runtime.Watchdog("restart", 0, tmp_path / "none.json")
    assert not dog.due(179.9)
    assert dog.due(180)
    assert not dog.due(209.9)
    assert dog.due(210)


def watchdog(tmp_path, mode="restart"):
    said = []
    history = tmp_path / "logs" / "restarts.json"
    return runtime.Watchdog(mode, 0, history, say=said.append), said, history


def observe(dog, results, start=180, wall=1_000_000):
    """Feed probe results 30 s apart; returns each decision."""
    return [
        dog.observe(start + 30 * n, wall + start + 30 * n, *result)
        for n, result in enumerate(results)
    ]


def test_an_answer_after_nine_silent_probes_starts_the_count_again(tmp_path):
    dog, said, history = watchdog(tmp_path)
    results = [("silent", [])] * 9 + [("ok", [])] + [("silent", [])] * 9
    assert observe(dog, results) == [None] * 19
    assert said == [] and not history.exists()
    # A server that answers "unavailable" is answering too.
    results = [("degraded", ["workers"])] + [("silent", [])] * 9
    assert observe(dog, results) == [None] * 10
    assert observe(dog, [("silent", [])]) == ["restart"]


def test_an_unavailable_server_is_reported_once_and_never_ended(tmp_path):
    dog, said, history = watchdog(tmp_path)
    results = [("degraded", ["thread_pool", "workers"])] * 40
    assert observe(dog, results) == [None] * 40
    assert said == ["Server reports unavailable: thread_pool, workers"]
    assert observe(dog, [("ok", [])] * 3) == [None] * 3
    assert said[1:] == ["Server reports ok again"]
    assert not history.exists()


def test_the_fourth_restart_in_an_hour_is_refused(tmp_path):
    dog, said, history = watchdog(tmp_path)
    history.parent.mkdir()
    wall = 1_000_000
    earlier = [
        # Counted: three watchdog restarts within the hour.
        {"at": wall - 2700, "kind": "watchdog", "detail": ""},
        {"at": wall - 2000, "kind": "watchdog", "detail": ""},
        {"at": wall - 1000, "kind": "watchdog", "detail": ""},
        # Not counted: older than an hour, and the server's own exits.
        {"at": wall - 5000, "kind": "watchdog", "detail": ""},
        {"at": wall - 500, "kind": "exit", "detail": ""},
        {"at": wall - 400, "kind": "exit", "detail": ""},
    ]
    history.write_text(json.dumps(earlier))
    # 30 probes (to 870 s) of silence: refused, said at 270 s and at 870 s.
    assert observe(dog, [("silent", [])] * 30, start=0, wall=wall) == [None] * 30
    line = (
        "Server has not answered for 300 s; "
        "not restarting again: 3 restarts in the last hour"
    )
    assert said == [line, line]
    assert json.loads(history.read_text()) == earlier
    # Once the oldest of the three is over an hour old, it restarts again.
    assert dog.observe(900, wall + 900, "silent") == "restart"
    assert json.loads(history.read_text())[-1]["kind"] == "watchdog"


def test_three_restarts_are_allowed_then_refused(tmp_path):
    _, _, history = watchdog(tmp_path)
    for n in range(3):
        dog, said, _ = watchdog(tmp_path)
        decisions = observe(dog, [("silent", [])] * 10, wall=1_000_000 + 500 * n)
        assert decisions[-1] == "restart"
    dog, said, _ = watchdog(tmp_path)
    assert observe(dog, [("silent", [])] * 10, wall=1_002_000) == [None] * 10
    assert len(json.loads(history.read_text())) == 3


def test_log_mode_reports_a_silent_server_without_ending_it(
    tmp_path, monkeypatch, capsys
):
    dog, said, history = watchdog(tmp_path, "log")
    assert observe(dog, [("silent", [])] * 12) == [None] * 12
    assert said == [
        "Server has not answered for 300 s; not restarting it (PIG_MUSIC_WATCHDOG=log)"
    ]
    assert not history.exists()
    monkeypatch.setenv("PIG_MUSIC_WATCHDOG", "log")
    run = Supervised(tmp_path, monkeypatch, FakeProcess(lifetime=1500), silent)
    assert runtime.main() == 1
    assert len(run.probes) > 20
    # It outlived the silence and ended by itself: only that is recorded.
    assert [entry["kind"] for entry in run.entries()] == ["exit"]


def test_off_mode_never_probes(tmp_path, monkeypatch):
    monkeypatch.setenv("PIG_MUSIC_WATCHDOG", "off")
    run = Supervised(tmp_path, monkeypatch, FakeProcess(lifetime=1500), silent)
    assert runtime.main() == 1
    assert run.probes == []
    assert not runtime.Watchdog("off", 0, tmp_path / "none.json").due(10_000)
    # Anything else is the default.
    assert runtime.Watchdog("maybe", 0, tmp_path / "none.json").mode == "restart"


@pytest.mark.parametrize("damage", ["corrupt", "not a list", "a folder"])
def test_an_unreadable_restart_history_is_no_history(tmp_path, damage):
    dog, said, history = watchdog(tmp_path)
    history.parent.mkdir()
    if damage == "corrupt":
        history.write_text("{not json")
    elif damage == "not a list":
        history.write_text('{"at": "yesterday"}')
    else:
        history.mkdir()
    assert runtime.restart_history(history) == []
    # Still restarts, and a history that cannot be written is not an error.
    assert observe(dog, [("silent", [])] * 10)[-1] == "restart"
    if damage != "a folder":
        assert [e["kind"] for e in json.loads(history.read_text())] == ["watchdog"]


def test_the_restart_history_keeps_the_newest_fifty(tmp_path):
    history = tmp_path / "logs" / "restarts.json"
    for n in range(60):
        runtime.record_restart(history, n, "exit", f"exit {n}")
    entries = json.loads(history.read_text())
    assert [entry["at"] for entry in entries] == list(range(10, 60))
    assert entries[-1] == {"at": 59, "kind": "exit", "detail": "exit 59"}


def test_a_killed_server_says_which_signal_ended_it(tmp_path, monkeypatch, capsys):
    server = FakeProcess(lifetime=5, code=-9)
    run = Supervised(tmp_path, monkeypatch, server, lambda run: ("ok", []))
    assert runtime.main() == 1
    line = "Server was ended by signal 9 (SIGKILL: possibly out of memory)"
    assert line in capsys.readouterr().err
    [entry] = run.entries()
    assert entry["kind"] == "exit" and entry["detail"] == line
    assert runtime.exit_reason(3) == "Server exited with code 3"
    assert runtime.exit_reason(-15) == "Server was ended by signal 15 (SIGTERM)"


def test_a_stop_signal_during_a_probe_stops_at_once(tmp_path, monkeypatch, capsys):
    def interrupted(run):
        # SIGTERM arrives while the probe waits on a server that is silent.
        run.handlers[runtime.signal.SIGTERM](runtime.signal.SIGTERM, None)
        run.clock[0] += 10  # the rest of the wait, if nothing ended it
        return "silent", []

    run = Supervised(tmp_path, monkeypatch, FakeProcess(lifetime=None), interrupted)
    assert runtime.main() == 0
    assert run.probes == [180] and run.clock[0] == 180
    assert run.server.returncode == 0
    assert not run.history.exists()
    assert capsys.readouterr().err == ""


class Ready(BaseHTTPRequestHandler):
    answers = {
        "/ok": (200, {"status": "ok", "checks": {"workers": True}}),
        "/unavailable": (
            503,
            {
                "status": "unavailable",
                "checks": {"workers": False, "database": True, "thread_pool": False},
            },
        ),
        "/missing": (404, {"detail": "Not Found"}),
    }

    seen = []

    def do_GET(self):
        self.seen.append((self.client_address[0], {k.lower() for k in self.headers}))
        status, body = self.answers[self.path]
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def test_the_probe_tells_ok_unavailable_and_no_answer_apart():
    web = ThreadingHTTPServer(("127.0.0.1", 0), Ready)
    thread = threading.Thread(target=web.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{web.server_address[1]}"
    try:
        assert runtime.probe(base + "/ok", 5) == ("ok", [])
        assert runtime.probe(base + "/unavailable", 5) == (
            "degraded",
            ["thread_pool", "workers"],
        )
        assert runtime.probe(base + "/missing", 5) == ("degraded", ["HTTP 404"])
        # The server answers readiness only to a direct local caller: the
        # probe and the Dockerfile's health check (urllib) both are one.
        del Ready.seen[:]
        runtime.probe(base + "/ok", 5)
        urllib.request.urlopen(base + "/ok", timeout=5).close()
        assert len(Ready.seen) == 2
        for address, headers in Ready.seen:
            assert address == "127.0.0.1"
            assert not headers & set(FORWARDED)
        assert urllib.parse.urlsplit(runtime.READY_URL).hostname == "127.0.0.1"
        dockerfile = Path(server.__file__).resolve().parents[1] / "Dockerfile"
        if dockerfile.exists():  # not part of the image the suite also runs in
            check = "urlopen('http://127.0.0.1:8765/api/health/ready')"
            assert check in dockerfile.read_text()
    finally:
        web.shutdown()
        web.server_close()
        thread.join(5)
    # Nothing listens there any more: refused.
    assert runtime.probe(base + "/ok", 5) == ("silent", [])
    # Connected but never answered (a server whose loop is stuck): timed out.
    stuck = socket.create_server(("127.0.0.1", 0))
    try:
        url = f"http://127.0.0.1:{stuck.getsockname()[1]}/ok"
        assert runtime.probe(url, 0.3) == ("silent", [])
    finally:
        stuck.close()
