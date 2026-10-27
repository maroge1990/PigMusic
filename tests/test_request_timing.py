import asyncio
import gc
import logging
import threading
import time
import weakref
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from server import request_timing
from server.request_timing import RequestTiming, StallWatch, phase, watch_event_loop
from test_mixes import cached, member, ready, track
from test_security import PASSWORD, protected
from test_downloader import audio  # noqa: F401  Shared real MP3 fixture.


def timings(response):
    return {
        entry.split(";")[0]: float(entry.split("dur=")[1])
        for entry in response.headers["server-timing"].split(", ")
    }


def test_mix_address_names_its_phases_and_echoes_the_request_id(
    tmp_path, monkeypatch, audio, caplog
):
    app, owner = protected(tmp_path, monkeypatch)
    mixes = app.state.store.mixes
    user, token = member(app, owner)
    cached(mixes, track()).write_bytes(audio.read_bytes())
    identity = ready(mixes, user, [track()])
    caplog.set_level(logging.INFO, "pig_music.timing")
    with TestClient(
        app,
        base_url="https://private.ts.net",
        headers={"Authorization": "Bearer " + token},
    ) as client:
        answer = client.post(
            f"/api/v1/playback/mix:{identity}:0/url",
            headers={"X-Request-ID": "phone-0001"},
        )
        assert answer.status_code == 200, answer.text
        assert answer.headers["x-request-id"] == "phone-0001"
        phases = timings(answer)
        for name in (
            "app",
            "auth-ready",
            "auth-session",
            "auth-mix",
            "handler",
            "mix-entry",
            "mix-lock",
            "mix-lease",
            "mix-file",
        ):
            assert name in phases, phases
        # The address request is logged as a baseline, without its query.
        line = next(r.message for r in caplog.records if "phone-0001" in r.message)
        assert f"POST /api/v1/playback/mix:{identity}:0/url 200" in line
        assert "mix-lease=" in line

        caplog.clear()
        signed = answer.json()["url"]
        assert "sig=" in signed
        media = TestClient(app, base_url="https://private.ts.net")
        played = media.get(signed, headers={"X-Request-ID": "bad id!"})
        assert played.status_code == 200
        # An unusable client ID is replaced, never echoed.
        assert len(played.headers["x-request-id"]) == 16
        assert "auth-link" in timings(played)
        # Fast file requests are not logged, and nothing logs a signature.
        assert not [r for r in caplog.records if "sig=" in r.getMessage()]


def test_slow_and_unanswered_requests_are_logged_with_pool_state(caplog):
    caplog.set_level(logging.INFO, "pig_music.timing")

    async def slow(scope, receive, send):
        with phase("work"):
            await asyncio.sleep(0.05)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def gone(scope, receive, send):
        raise RuntimeError("No response returned.")

    async def call(app, path):
        scope = {"type": "http", "method": "GET", "path": path, "headers": []}
        sent = []

        async def send(message):
            sent.append(message)

        try:
            await RequestTiming(app, slow_ms=10)(scope, None, send)
        except RuntimeError:
            pass
        return sent

    asyncio.run(call(slow, "/api/v1/remote-playback/streams/secret-relay-token"))
    relayed = caplog.records.pop().getMessage()
    assert "secret-relay-token" not in relayed and "streams/[redacted]" in relayed
    sent = asyncio.run(call(slow, "/api/v1/library"))
    headers = dict(sent[0]["headers"])
    assert b"work;dur=" in headers[b"server-timing"]
    asyncio.run(call(gone, "/api/v1/events"))
    slow_line, gone_line = [r.getMessage() for r in caplog.records]
    assert "GET /api/v1/library 200 headers" in slow_line and "work=" in slow_line
    assert "anyio" in slow_line
    assert "GET /api/v1/events no response" in gone_line
    # Phases from outside a request are ignored.
    with phase("loose"):
        pass


def test_event_loop_watch_reports_a_blocked_loop_and_a_thread_queue(caplog):
    caplog.set_level(logging.INFO, "pig_music.timing")

    async def scenario():
        loop = asyncio.get_running_loop()
        watcher = asyncio.create_task(
            watch_event_loop(interval=0.01, blocked_ms=50, quiet_seconds=0)
        )
        await asyncio.sleep(0.02)
        threading.Event().wait(0.1)  # Blocks the loop, as a sync call on it would.
        release = threading.Event()
        loop.set_default_executor(ThreadPoolExecutor(2))
        jobs = [loop.run_in_executor(None, release.wait) for _ in range(4)]
        await asyncio.sleep(0.1)
        release.set()
        await asyncio.gather(*jobs)
        watcher.cancel()

    asyncio.run(scenario())
    messages = [r.getMessage() for r in caplog.records]
    assert any(m.startswith("event loop blocked for") for m in messages), messages
    assert any(m.startswith("worker threads saturated") for m in messages), messages


def test_stall_watch_logs_where_the_loop_is_stuck(caplog):
    caplog.set_level(logging.INFO, "pig_music.timing")
    lock = threading.Lock()
    held = threading.Event()

    def hold_the_lock():
        with lock:
            held.set()
            time.sleep(0.6)

    def hold_the_loop():
        # A sync call on the loop waiting for a lock a worker holds.
        with lock:
            pass

    async def scenario():
        stall = StallWatch(stalled_seconds=0.2, repeat_seconds=10, check_every=0.05)
        stall.start()
        watcher = asyncio.create_task(watch_event_loop(interval=0.05, stall=stall))
        await asyncio.sleep(0.1)
        worker = asyncio.get_running_loop().run_in_executor(None, hold_the_lock)
        held.wait()
        hold_the_loop()
        await worker
        await asyncio.sleep(0.2)
        stall.stop()
        watcher.cancel()

    asyncio.run(scenario())
    stuck = [r.getMessage() for r in caplog.records if "stuck for" in r.getMessage()]
    # Once per stall, with the loop's own stack and the default pool's load.
    assert len(stuck) == 1, stuck
    assert "in hold_the_loop" in stuck[0] and "asyncio 1/" in stuck[0]


def test_stall_watch_lets_a_finished_jobs_memory_go(caplog):
    caplog.set_level(logging.INFO, "pig_music.timing")

    class Payload:
        pass

    gone = threading.Event()
    stalled = threading.Event()

    def job():
        payload = Payload()
        weakref.finalize(payload, gone.set)
        stalled.wait(2)  # Its frame, holding the payload, is live during the stall.

    async def scenario():
        stall = StallWatch(stalled_seconds=0.2, repeat_seconds=10, check_every=0.05)
        stall.start()
        watcher = asyncio.create_task(watch_event_loop(interval=0.05, stall=stall))
        await asyncio.sleep(0.1)
        worker = asyncio.get_running_loop().run_in_executor(None, job)
        time.sleep(0.5)  # Stall the loop while the job runs.
        stalled.set()
        await worker
        # The loop is healthy again and the watchdog still running.
        await asyncio.sleep(0.3)
        gc.collect()
        collected = gone.is_set()
        stall.stop()
        watcher.cancel()
        return collected

    assert asyncio.run(scenario())
    assert any("stuck for" in r.getMessage() for r in caplog.records)


def test_diagnostics_show_members_their_own_requests_and_owners_everything(
    tmp_path, monkeypatch
):
    # The history is the process's own; other tests' requests are in it too.
    request_timing.RECENT.clear()
    request_timing.ROUTES.clear()
    request_timing.STALLS.clear()
    request_timing.STALLS.append({"at": 1.0, "seconds": 3.0, "stack": "File x"})
    app, owner = protected(tmp_path, monkeypatch)
    user, member_token = member(app, owner)
    owner_token = app.state.auth.login(
        "mark", PASSWORD, "device", "phone", "ios", "fixture"
    )["token"]
    with TestClient(app, base_url="https://private.ts.net") as client:
        as_member = {"Authorization": "Bearer " + member_token}
        as_owner = {"Authorization": "Bearer " + owner_token}
        client.get(
            "/api/v1/library", headers={**as_member, "X-Request-ID": "member-01"}
        )
        client.get(
            "/api/v1/library", headers={**as_owner, "X-Request-ID": "owner-0001"}
        )
        own = client.get("/api/v1/diagnostics", headers=as_member).json()
        ids = [r["id"] for r in own["requests"]]
        assert "member-01" in ids and "owner-0001" not in ids
        (seen,) = [r for r in own["requests"] if r["id"] == "member-01"]
        assert seen["route"] == "/api/v1/library" and seen["status"] == 200
        assert seen["headers_ms"] > 0
        assert "auth-session" in [name for name, _ in seen["phases"]]
        # Members see when the server stalled, never where.
        assert own["stalls"] == [{"at": 1.0, "seconds": 3.0}]
        assert "routes" not in own and set(own["pools"]) == {"asyncio", "anyio"}
        assert (
            client.get("/api/v1/admin/diagnostics", headers=as_member).status_code
            == 403
        )
        everything = client.get("/api/v1/admin/diagnostics", headers=as_owner).json()
        ids = [r["id"] for r in everything["requests"]]
        assert "member-01" in ids and "owner-0001" in ids
        assert everything["stalls"][0]["stack"] == "File x"
        (library,) = [
            r for r in everything["routes"] if r["route"] == "GET /api/v1/library"
        ]
        assert library["count"] == 2 and library["p95_ms"] >= library["p50_ms"]


def test_made_up_methods_and_paths_never_grow_the_route_history(tmp_path, monkeypatch):
    request_timing.RECENT.clear()
    request_timing.ROUTES.clear()
    app, _ = protected(tmp_path, monkeypatch)
    with TestClient(app, base_url="https://private.ts.net") as client:
        # No credentials: rejected by routing or the guard, still recorded.
        for n in range(1000):
            client.request(f"CUSTOM{n}", "/api/v1/library")
            client.get(f"/api/v1/no-such-route-{n}/" + "x" * 500)
        keys = set(request_timing.ROUTES)
        assert len(keys) <= 2, keys
        assert all(key.split(" ", 1)[0] in {"GET", "OTHER"} for key in keys)
        assert len(request_timing.RECENT) == request_timing.RECENT.maxlen
        assert all(len(r["path"]) <= 300 for r in request_timing.RECENT)
        assert {r["method"] for r in request_timing.RECENT} <= {"GET", "OTHER"}
    # A backstop holds even if the app had more routes than expected.
    request_timing.ROUTES.clear()
    monkeypatch.setattr(request_timing, "MAX_ROUTES", 3)
    timing = RequestTiming(None)

    class Route:
        def __init__(self, path):
            self.path = path

    for n in range(10):
        scope = {"method": "GET", "path": "/api/x", "route": Route(f"/api/{n}")}
        timing.report(scope, request_timing.Trace("backstop"), 200, 1.0)
    assert len(request_timing.ROUTES) == 3
