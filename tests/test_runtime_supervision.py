"""The optional source engine never takes the web server down with it, and a
server that stops answering is noticed, explained and restarted (#238)."""

import json
import socket
from pathlib import Path
import threading
import time
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

    def start_engine(root, proceed=None):
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


class HungEngine(FakeProcess):
    """Never exits by itself; records when it was ended."""

    def __init__(self, clock):
        super().__init__(lifetime=None)
        self.clock = clock
        self.ended = None

    def terminate(self):
        if self.ended is None:
            self.ended = self.clock[0]
        super().terminate()


def engine_supervised(
    tmp_path, monkeypatch, ticks, answer, kind=HungEngine, server_probes=None
):
    """main() with a fake server living `ticks` seconds, hung engines and a
    scripted engine probe: `answer(clock)` returns its state."""
    clock = [0.0]
    engines, probes = [], []

    def server_probe():
        if server_probes is not None:
            server_probes.append(clock[0])
        return "ok", []

    def start_engine(root, proceed=None):
        engines.append((clock[0], kind(clock)))
        return engines[-1][1], None

    def sleep(seconds):
        clock[0] += 1

    def engine_probe():
        probes.append(clock[0])
        return answer(clock[0]), []

    monkeypatch.setenv("PIG_MUSIC_DATA", str(tmp_path))
    monkeypatch.setenv("PIG_MUSIC_EMBEDDED_ENGINE", "true")
    monkeypatch.setattr(runtime, "start_engine", start_engine)
    monkeypatch.setattr(runtime, "proxy_trust", lambda: "127.0.0.1")
    monkeypatch.setattr(
        runtime.subprocess, "Popen", lambda *a, **k: FakeProcess(lifetime=ticks)
    )
    monkeypatch.setattr(runtime.time, "sleep", sleep)
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(runtime.signal, "signal", lambda *a: None)
    monkeypatch.setattr(runtime, "probe", server_probe, raising=False)
    monkeypatch.setattr(runtime, "engine_probe", engine_probe, raising=False)
    assert runtime.main() == 1
    return engines, probes


def test_a_hung_engine_is_ended_and_started_again(tmp_path, monkeypatch, capsys):
    """#256: an engine that stops answering without exiting was never
    restarted; every call to it just timed out."""
    engines, probes = engine_supervised(
        tmp_path, monkeypatch, 600, lambda now: "silent"
    )
    # Ten silent probes, 30 s apart, after its 180 s grace.
    assert probes[:10] == [180 + 30 * n for n in range(10)]
    (first, hung), (again, _) = engines[:2]
    assert hung.ended == 450
    assert again == 450 + runtime.ENGINE_BACKOFF_START
    err = capsys.readouterr().err
    assert "Source engine has not answered for 300 s; restarting it in 5s" in err
    # The server itself was never touched.
    assert "Server has not answered" not in err


class UnkillableEngine(HungEngine):
    """Stuck in uninterruptible I/O: survives even a kill. A wait with a
    limit lets the fake clock run out; one without would block for ever."""

    def __init__(self, clock):
        super().__init__(clock)
        self.killed = None

    def poll(self):
        return None

    def terminate(self):
        if self.ended is None:
            self.ended = self.clock[0]

    def kill(self):
        if self.killed is None:
            self.killed = self.clock[0]

    def wait(self, timeout=None):
        if timeout is None:
            raise AssertionError("waited for the engine without a limit")
        self.clock[0] += timeout
        raise runtime.subprocess.TimeoutExpired("slskd", timeout)


def test_an_engine_that_outlives_a_kill_never_stalls_the_supervisor(
    tmp_path, monkeypatch, capsys
):
    """Codex on 7866ac1: after the kill the supervisor waited for the engine
    without a limit, so one stuck in kernel I/O stopped the server's
    watchdog, every engine restart and the shutdown deadline."""
    server_probes = []
    engines, probes = engine_supervised(
        tmp_path,
        monkeypatch,
        1500,
        lambda now: "silent",
        kind=UnkillableEngine,
        server_probes=server_probes,
    )
    ((_, stuck),) = engines  # No second engine while the first is still there.
    assert stuck.ended == 450 and stuck.killed == 460
    # The server's watchdog kept probing every 30 s throughout.
    after = [at for at in server_probes if at > 450]
    assert after and all(b - a == 30 for a, b in zip(after, after[1:]))
    assert after[-1] > 1400
    err = capsys.readouterr().err
    assert "did not stop within 10 s; killing it" in err
    assert "Waiting for the old source engine to end" in err


def test_starting_the_engine_on_a_hung_mount_never_stalls_the_supervisor(
    tmp_path, monkeypatch, capsys
):
    """Codex on 438047f: the engine's start checks the library mount and
    makes its folders on the NAS from the supervisor's own loop, so a hung
    mount stopped the server's watchdog, restarts and shutdown."""
    clock = [0.0]
    release = threading.Event()
    launches, server_probes, active = [], [], [0, 0]

    def start_engine(root, proceed=None):
        launches.append((clock[0], threading.current_thread().name))
        if len(launches) == 1:
            return HungEngine(clock), None
        active[0] += 1
        active[1] = max(active)
        if threading.current_thread() is threading.main_thread():
            raise AssertionError("the mount check would hang the supervisor")
        release.wait(5)  # The mount check hangs.
        active[0] -= 1
        return None, None

    def server_probe():
        server_probes.append(clock[0])
        if launches[1:] and clock[0] > launches[1][0] + 300:
            release.set()  # The loop kept running while the start hung.
        return "ok", []

    def sleep(seconds):
        clock[0] += 1

    monkeypatch.setenv("PIG_MUSIC_DATA", str(tmp_path))
    monkeypatch.setenv("PIG_MUSIC_EMBEDDED_ENGINE", "true")
    monkeypatch.setattr(runtime, "start_engine", start_engine)
    monkeypatch.setattr(runtime, "proxy_trust", lambda: "127.0.0.1")
    monkeypatch.setattr(
        runtime.subprocess, "Popen", lambda *a, **k: FakeProcess(lifetime=1200)
    )
    monkeypatch.setattr(runtime.time, "sleep", sleep)
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(runtime.signal, "signal", lambda *a: None)
    monkeypatch.setattr(runtime, "probe", server_probe, raising=False)
    monkeypatch.setattr(runtime, "engine_probe", lambda: ("silent", []))
    try:
        assert runtime.main() == 1
    finally:
        release.set()
    assert release.is_set() and len(launches) >= 2
    # The hung start ran off the loop, and only one start at a time.
    assert all(name == "engine-launch" for _, name in launches)
    assert active[1] == 1
    after = [at for at in server_probes if at > launches[1][0]]
    assert after and all(b - a == 30 for a, b in zip(after, after[1:]))
    err = capsys.readouterr().err
    assert "Starting the source engine is waiting on the library mount" in err


@pytest.mark.parametrize("released", ["before_reap", "during_server_wait"])
def test_an_engine_start_released_during_shutdown_leaves_no_engine_running(
    tmp_path, monkeypatch, released
):
    """Codex on b71c163: a start held up on the mount and released while
    the supervisor was stopping (reaping the web server) still spawned the
    engine, which nobody then ended."""
    clock = [0.0]
    release = threading.Event()
    handlers, engines = {}, []
    pause = time.sleep  # Real time: the fake clock replaces time.sleep.

    class Server(FakeProcess):
        def wait(self, timeout=None):
            if released == "during_server_wait":
                release.set()
                pause(0.3)  # The launcher spawns meanwhile.
            return super().wait(timeout)

    server = Server(lifetime=None)

    def popen(command, **kwargs):
        if "uvicorn" in command:
            return server
        engines.append(FakeProcess(lifetime=None))
        return engines[-1]

    def mount(*args, **kwargs):
        release.wait(5)  # A hung NAS mount, until released.

    def sleep(seconds):
        clock[0] += 1
        if clock[0] == 20:
            handlers[runtime.signal.SIGTERM](runtime.signal.SIGTERM, None)
            if released == "before_reap":
                release.set()
                pause(0.3)

    monkeypatch.setenv("PIG_MUSIC_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("PIG_MUSIC_EMBEDDED_ENGINE", "true")
    monkeypatch.setenv("PIG_MUSIC_DOWNLOAD_ROOT", str(tmp_path / "work"))
    monkeypatch.setenv("PIG_MUSIC_LIBRARY", str(tmp_path / "nas"))
    monkeypatch.setattr(runtime, "require_library_mount", mount)
    monkeypatch.setattr(runtime, "proxy_trust", lambda: "127.0.0.1")
    monkeypatch.setattr(runtime.subprocess, "Popen", popen)
    monkeypatch.setattr(runtime.time, "sleep", sleep)
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(runtime.signal, "signal", handlers.__setitem__)
    monkeypatch.setattr(runtime, "probe", lambda: ("ok", []), raising=False)
    try:
        assert runtime.main() == 0
    finally:
        release.set()
    for thread in threading.enumerate():
        if thread.name == "engine-launch":
            thread.join(5)
    assert all(engine.returncode is not None for engine in engines)


def test_an_engine_mid_spawn_at_shutdown_is_ended_before_the_supervisor_exits(
    tmp_path, monkeypatch
):
    """Codex on 4753842: with the child created but Popen not yet returned
    when SIGTERM came, shutdown saw no engine and exited, leaving it alive."""
    clock = [0.0]
    release = threading.Event()
    handlers, engines = {}, []
    server = FakeProcess(lifetime=None)

    def popen(command, **kwargs):
        if "uvicorn" in command:
            return server
        engines.append(FakeProcess(lifetime=None))  # The child exists now.
        release.wait(5)  # Popen has not returned it yet.
        return engines[-1]

    def sleep(seconds):
        clock[0] += 1
        if clock[0] == 20:
            threading.Timer(0.2, release.set).start()
            handlers[runtime.signal.SIGTERM](runtime.signal.SIGTERM, None)

    monkeypatch.setenv("PIG_MUSIC_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("PIG_MUSIC_EMBEDDED_ENGINE", "true")
    monkeypatch.delenv("PIG_MUSIC_DOWNLOAD_ROOT", raising=False)
    monkeypatch.setattr(runtime, "proxy_trust", lambda: "127.0.0.1")
    monkeypatch.setattr(runtime.subprocess, "Popen", popen)
    monkeypatch.setattr(runtime.time, "sleep", sleep)
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(runtime.signal, "signal", handlers.__setitem__)
    monkeypatch.setattr(runtime, "probe", lambda: ("ok", []), raising=False)
    try:
        assert runtime.main() == 0
        # Ended before main returned, not by a thread left behind.
        assert len(engines) == 1 and engines[0].returncode is not None
    finally:
        release.set()
        for thread in threading.enumerate():
            if thread.name == "engine-launch":
                thread.join(5)


def test_an_engine_that_answers_is_never_ended(tmp_path, monkeypatch):
    # Nine silent probes, then any answer (even a refusal) starts the count again.
    engines, probes = engine_supervised(
        tmp_path,
        monkeypatch,
        1500,
        # Every ninth probe answers.
        lambda now: "degraded" if (now - 180) % 270 == 240 else "silent",
    )
    assert len(engines) == 1
    assert engines[0][1].ended >= 1499  # Only when the server ended.
    assert len(probes) > 40


def test_a_hung_engine_is_restarted_three_times_an_hour_then_reported(
    tmp_path, monkeypatch, capsys
):
    engines, probes = engine_supervised(
        tmp_path, monkeypatch, 3000, lambda now: "silent"
    )
    assert len(engines) == 4
    ended = [e.ended for _, e in engines]
    # The fourth hang within the hour is left running until the server ends.
    assert all(at < 2000 for at in ended[:3]) and ended[3] >= 2999
    err = capsys.readouterr().err
    assert err.count("restarting it in") == 3
    assert (
        "Source engine has not answered for 300 s; not restarting it again:"
        " 3 restarts in the last hour"
    ) in err
    # Reminded every 10 minutes, not on every probe.
    assert err.count("not restarting it again") <= 2


def test_the_engine_probe_asks_its_local_port():
    assert runtime.ENGINE_URL.startswith("http://127.0.0.1:5030/")


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
        monkeypatch.setattr(
            runtime, "start_engine", lambda root, proceed=None: (None, None)
        )
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
    start, entry = run.entries()
    assert start["kind"] == "start" and start["at"] == 1_000_000
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
    assert [entry["kind"] for entry in run.entries()] == ["start", "exit"]


def test_off_mode_never_probes(tmp_path, monkeypatch):
    monkeypatch.setenv("PIG_MUSIC_WATCHDOG", "off")
    run = Supervised(tmp_path, monkeypatch, FakeProcess(lifetime=1500), silent)
    assert runtime.main() == 1
    assert run.probes == []
    assert not runtime.Watchdog("off", 0, tmp_path / "none.json").due(10_000)


def test_an_unknown_watchdog_setting_is_named_and_means_restart(tmp_path, monkeypatch):
    said = []
    dog = runtime.Watchdog("maybe", 0, tmp_path / "none.json", say=said.append)
    assert dog.mode == "restart"
    assert said == [
        "PIG_MUSIC_WATCHDOG=maybe is not restart, log or off; using restart"
    ]
    for known in ("restart", "log", "off"):
        runtime.Watchdog(known, 0, tmp_path / "none.json", say=said.append)
    assert len(said) == 1
    # Set but empty is the default, without a remark.
    monkeypatch.setenv("PIG_MUSIC_WATCHDOG", "")
    run = Supervised(tmp_path, monkeypatch, FakeProcess(lifetime=None), silent)
    assert runtime.main() == 1
    assert len(run.probes) == 10


@pytest.mark.parametrize("damage", ["corrupt", "not a list"])
def test_an_unreadable_restart_history_is_no_history(tmp_path, damage):
    dog, said, history = watchdog(tmp_path)
    history.parent.mkdir()
    if damage == "corrupt":
        history.write_text("{not json")
    else:
        history.write_text('{"at": "yesterday"}')
    assert runtime.restart_history(history) == []
    # It can be written, so the restart goes ahead and replaces it.
    assert observe(dog, [("silent", [])] * 10)[-1] == "restart"
    assert [e["kind"] for e in json.loads(history.read_text())] == ["watchdog"]


def test_no_restart_when_the_history_cannot_be_written(tmp_path, monkeypatch, capsys):
    """Without a written entry the hourly limit has no memory, and the restart
    could repeat every few minutes for ever."""
    dog, said, history = watchdog(tmp_path)
    history.mkdir(parents=True)  # a folder where the file belongs
    assert observe(dog, [("silent", [])] * 31, start=0) == [None] * 31
    line = (
        "Server has not answered for 300 s; "
        "not restarting it: logs/restarts.json cannot be written"
    )
    # At 270 s and 600 s later, not at every probe.
    assert said == [line, line]
    # Writable again: the next silent probe restarts.
    history.rmdir()
    assert dog.observe(930, 1_000_930, "silent") == "restart"
    assert [e["kind"] for e in json.loads(history.read_text())] == ["watchdog"]
    # The server ending by itself is still reported when it cannot be recorded.
    history.unlink()
    history.mkdir()
    Supervised(tmp_path, monkeypatch, FakeProcess(lifetime=5), silent)
    assert runtime.main() == 1
    assert "Server exited with code 1" in capsys.readouterr().err


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
    _, entry = run.entries()
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
    # Recorded as asked to stop, not as a crash or a watchdog restart.
    assert [entry["kind"] for entry in run.entries()] == ["start", "stop"]
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
