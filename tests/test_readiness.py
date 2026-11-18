"""#238: /api/health/ready says whether the server can do its work, for the
container's health check and the supervisor's watchdog, and answers nobody
else."""

import asyncio
import logging
import os
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import server.app as server_app
import server.auth_logging as auth_logging
from server.app import create_app
from server.security import api_routes

READY = "/api/health/ready"
LOCAL = ("127.0.0.1", 50000)
PASSWORD = "readiness fixture password"
ALL_OK = {"thread_pool": True, "database": True, "workers": True, "storage": True}


def test_a_healthy_server_is_ready_and_says_nothing_else(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app, client=LOCAL) as client:
        response = client.get(READY)
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": ALL_OK}


def test_readiness_is_not_in_the_api_document(tmp_path):
    app = create_app(tmp_path)
    assert READY in {route.path for route in api_routes(app)}
    assert READY not in app.openapi()["paths"]
    assert "/api/health" in app.openapi()["paths"]


@pytest.mark.parametrize("seconds,ready", [(299, True), (301, False)])
def test_a_worker_failing_for_five_minutes_makes_the_server_unavailable(
    tmp_path, seconds, ready
):
    app = create_app(tmp_path)
    with TestClient(app, client=LOCAL) as client:
        # Between restarts (not running) is fine; failing for long is not.
        app.state.workers.failed("exports", "RuntimeError: fixture")
        with app.state.workers.lock:
            entry = app.state.workers.workers["exports"]
            entry["failing_since"] = time.time() - seconds
        response = client.get(READY)
    assert response.status_code == (200 if ready else 503)
    assert response.json() == {
        "status": "ok" if ready else "unavailable",
        "checks": {**ALL_OK, "workers": ready},
    }


def test_a_full_thread_pool_makes_the_server_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(server_app, "READY_TIMEOUT", 0.5, raising=False)
    app = create_app(tmp_path)
    release = threading.Event()

    async def fill():
        # Every thread of the pool asyncio.to_thread uses waits on the event.
        loop = asyncio.get_running_loop()
        await asyncio.to_thread(lambda: None)
        size = loop._default_executor._max_workers
        return [loop.run_in_executor(None, release.wait, 30) for _ in range(size)]

    with TestClient(app, client=LOCAL) as client:
        try:
            client.portal.call(fill)
            began = time.monotonic()
            response = client.get(READY)
            # Both pool checks wait together, not one after the other.
            assert time.monotonic() - began < 0.9
        finally:
            release.set()
        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "unavailable"
        assert body["checks"]["thread_pool"] is False
        assert body["checks"]["workers"] is True
        assert set(body["checks"]) == set(ALL_OK)
        # Ready again once the threads are free.
        assert client.get(READY).json() == {"status": "ok", "checks": ALL_OK}


def test_missing_storage_is_reported_without_making_the_server_unavailable(
    tmp_path, monkeypatch
):
    # A download root whose library mount is absent: /api/health's own test.
    monkeypatch.setenv("PIG_MUSIC_DOWNLOAD_ROOT", str(tmp_path / "work"))
    monkeypatch.setenv("PIG_MUSIC_LIBRARY", str(tmp_path / "absent"))
    app = create_app(tmp_path)
    with TestClient(app, client=LOCAL) as client:
        assert client.get("/api/health").json()["storage_available"] is False
        response = client.get(READY)
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {**ALL_OK, "storage": False}}


def protected(tmp_path, monkeypatch, owner=True):
    monkeypatch.delenv("PIG_MUSIC_TEST_LEGACY_API", raising=False)
    monkeypatch.setenv("PIG_MUSIC_LIBRARY", str(tmp_path / "nas"))
    app = create_app(tmp_path)
    if owner:
        app.state.auth.create_owner("mark", PASSWORD)
    return app


def test_readiness_answers_only_a_direct_local_caller(tmp_path, monkeypatch):
    app = protected(tmp_path, monkeypatch)
    token = app.state.auth.login("mark", PASSWORD, "device", "phone", "ios", "fixture")[
        "token"
    ]
    with TestClient(app, client=LOCAL) as client:
        assert client.get(READY).status_code == 200
        assert client.get("/api/health").status_code == 200
        # Relayed by a proxy, whatever address it connects from.
        for header in ("X-Forwarded-For", "CF-Connecting-IP", "X-Real-IP"):
            relayed = client.get(READY, headers={header: "203.0.113.9"})
            assert relayed.status_code == 404, header
            assert "checks" not in relayed.text
        relayed = client.get(READY, headers={"Forwarded": "for=203.0.113.9"})
        assert relayed.status_code == 404
        # Only GET exists.
        assert client.post(READY, json={}).status_code == 405
        assert client.head(READY).status_code == 405
    # Any other address, signed in or not.
    with TestClient(app) as client:
        assert client.get(READY).status_code == 404
        signed_in = client.get(READY, headers={"Authorization": "Bearer " + token})
        assert signed_in.status_code == 404
        assert "checks" not in signed_in.text
        assert client.post(READY, json={}).status_code == 405
    with TestClient(app, client=("198.51.100.20", 50000)) as client:
        assert client.get(READY).status_code == 404


def test_readiness_answers_before_the_first_owner_exists(tmp_path, monkeypatch):
    app = protected(tmp_path, monkeypatch, owner=False)
    with TestClient(app, client=LOCAL) as client:
        assert client.get("/api/v1/library").status_code == 503
        response = client.get(READY)
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": ALL_OK}


def access(path, status):
    return logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", path, "1.1", status),
        None,
    )


def test_only_a_failing_readiness_check_reaches_the_access_log():
    keep = auth_logging.HealthCheckFilter().filter
    assert not keep(access(READY, 200))
    assert keep(access(READY, 503))
    assert keep(access(READY, 404))
    assert keep(access(READY + "/more", 200))
    assert not keep(access("/api/health", 200))


def blocking(original, release, calls, only_ready_threads=False):
    """`original`, waiting on `release` first; `calls` gets each thread name."""

    def blocked(*args, **kwargs):
        name = threading.current_thread().name
        if only_ready_threads and not name.startswith("ready"):
            return original(*args, **kwargs)
        calls.append(name)
        release.wait(30)
        return original(*args, **kwargs)

    return blocked


def free_thread_soon(client):
    """Whether the shared pool runs a job promptly, asked on the app's loop."""

    async def ask():
        await asyncio.wait_for(asyncio.to_thread(lambda: None), timeout=1)
        return True

    return client.portal.call(ask)


def test_a_hung_storage_check_is_not_started_again_or_on_the_shared_pool(
    tmp_path, monkeypatch
):
    """Codex on 05b98d6: each poll left one more shared thread blocked on an
    unresponsive mount until no endpoint had a thread to run on."""
    monkeypatch.setattr(server_app, "READY_TIMEOUT", 1, raising=False)
    app = create_app(tmp_path)
    release, calls = threading.Event(), []
    with TestClient(app, client=LOCAL) as client:
        try:
            monkeypatch.setattr(
                server_app,
                "download_root",
                blocking(server_app.download_root, release, calls),
            )
            assert client.get(READY).json()["checks"]["storage"] is False
            began = time.monotonic()
            for _ in range(20):
                response = client.get(READY)
                # Storage alone never makes the server unavailable.
                assert response.status_code == 200
                assert response.json()["checks"] == {**ALL_OK, "storage": False}
            # Not waited for again while the first call is still out.
            assert time.monotonic() - began < 1
            assert len(calls) == 1 and calls[0].startswith("ready")
            assert app.state.ready_checks.outstanding() == ["storage"]
            assert free_thread_soon(client)
        finally:
            release.set()
        deadline = time.monotonic() + 5
        while app.state.ready_checks.outstanding():
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert client.get(READY).json() == {"status": "ok", "checks": ALL_OK}
        assert len(calls) == 2


def test_a_hung_database_check_is_not_started_again_or_on_the_shared_pool(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(server_app, "READY_TIMEOUT", 1, raising=False)
    app = create_app(tmp_path)
    release, calls = threading.Event(), []
    store = app.state.store
    with TestClient(app, client=LOCAL) as client:
        try:
            # Only the readiness read hangs; the app's own reads go on.
            monkeypatch.setattr(
                store,
                "read_document",
                blocking(store.read_document, release, calls, only_ready_threads=True),
            )
            first = client.get(READY)
            assert first.status_code == 503
            began = time.monotonic()
            for _ in range(20):
                response = client.get(READY)
                assert response.status_code == 503
                assert response.json() == {
                    "status": "unavailable",
                    "checks": {**ALL_OK, "database": False},
                }
            assert time.monotonic() - began < 1
            assert len(calls) == 1
            assert app.state.ready_checks.outstanding() == ["database"]
            assert free_thread_soon(client)
        finally:
            release.set()
        deadline = time.monotonic() + 5
        while app.state.ready_checks.outstanding():
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert client.get(READY).json() == {"status": "ok", "checks": ALL_OK}


def test_the_server_stops_while_a_readiness_check_is_still_hung(tmp_path, monkeypatch):
    monkeypatch.setattr(server_app, "READY_TIMEOUT", 0.3, raising=False)
    app = create_app(tmp_path)
    release, calls = threading.Event(), []
    try:
        with TestClient(app, client=LOCAL) as client:
            monkeypatch.setattr(
                server_app,
                "download_root",
                blocking(server_app.download_root, release, calls),
            )
            assert client.get(READY).json()["checks"]["storage"] is False
            checks = app.state.ready_checks
            began = time.monotonic()
        assert time.monotonic() - began < 2
        assert calls and checks.outstanding() == ["storage"]
    finally:
        release.set()
    # The same app started again has working checks.
    monkeypatch.undo()
    with TestClient(app, client=LOCAL) as client:
        deadline = time.monotonic() + 5
        while client.get(READY).json()["checks"] != ALL_OK:
            assert time.monotonic() < deadline
            time.sleep(0.01)


def test_a_readiness_check_that_raises_or_hangs_fails_and_runs_once_at_a_time():
    from server.readiness import ReadinessChecks

    async def scenario():
        checks = ReadinessChecks()
        release, calls = threading.Event(), []

        def hang():
            calls.append(1)
            release.wait(30)

        try:
            assert await checks.passes("fine", lambda: None, timeout=1) is True
            assert await checks.passes("broken", lambda: 1 / 0, timeout=1) is False
            assert await checks.passes("slow", hang, timeout=0.2) is False
            began = time.monotonic()
            assert await checks.passes("slow", hang, timeout=5) is False
            assert time.monotonic() - began < 0.5
            assert len(calls) == 1 and checks.outstanding() == ["slow"]
            # One hung check does not hold up another.
            assert await checks.passes("fine", lambda: None, timeout=1) is True
        finally:
            release.set()
        while checks.outstanding():
            await asyncio.sleep(0.01)
        assert await checks.passes("slow", lambda: None, timeout=1) is True
        checks.close()

    asyncio.run(scenario())


def test_overlapping_callers_share_one_check_and_its_answer():
    """Codex on 7866ac1: a caller arriving while a healthy mount test was
    still running was told at once the mount was unavailable."""
    from server.readiness import ReadinessChecks

    threads = []

    def slow():
        threads.append(threading.current_thread().name)
        time.sleep(0.3)

    async def scenario():
        checks = ReadinessChecks()
        return await asyncio.gather(
            checks.passes("storage", slow, timeout=2),
            checks.passes("storage", slow, timeout=2),
        )

    assert asyncio.run(scenario()) == [True, True]
    assert len(threads) == 1


def test_overlapping_callers_of_a_stalled_check_use_one_thread_and_fail():
    from server.readiness import ReadinessChecks

    release, threads = threading.Event(), []

    def stalled():
        threads.append(threading.current_thread().name)
        release.wait(30)

    async def scenario():
        checks = ReadinessChecks()
        began = time.monotonic()
        answers = await asyncio.gather(
            checks.passes("storage", stalled, timeout=0.3),
            checks.passes("storage", stalled, timeout=0.3),
        )
        return answers, time.monotonic() - began

    try:
        answers, took = asyncio.run(scenario())
    finally:
        release.set()
    assert answers == [False, False] and took < 2
    assert len(threads) == 1


def test_health_never_waits_on_a_hung_mount_on_a_request_thread(tmp_path, monkeypatch):
    """#256: /api/health tested the mount on each caller's own pool thread,
    so every app checking reachability held one more on a hung mount."""
    monkeypatch.setattr(server_app, "HEALTH_STORAGE_TIMEOUT", 0.3, raising=False)
    app = create_app(tmp_path)
    release, calls = threading.Event(), []
    with TestClient(app, client=LOCAL) as client:
        try:
            monkeypatch.setattr(
                server_app,
                "download_root",
                blocking(server_app.download_root, release, calls),
            )
            began = time.monotonic()
            for _ in range(20):
                response = client.get("/api/health")
                assert response.status_code == 200
                assert response.json()["storage_available"] is False
            # Waited for once, not once per caller.
            assert time.monotonic() - began < 3
            assert len(calls) == 1 and calls[0].startswith("ready")
            assert free_thread_soon(client)
            # Readiness shares the one outstanding mount test.
            assert client.get(READY).json()["checks"]["storage"] is False
            assert len(calls) == 1
        finally:
            release.set()
        deadline = time.monotonic() + 5
        while not client.get("/api/health").json()["storage_available"]:
            assert time.monotonic() < deadline
            time.sleep(0.01)


def test_a_hung_check_never_holds_up_the_process_exit(tmp_path):
    """#256: a check still blocked on a dead mount kept the server's process
    from exiting until the supervisor killed it 25 s later."""
    import subprocess
    import sys

    script = (
        "import asyncio, time\n"
        "from server.readiness import ReadinessChecks\n"
        "checks = ReadinessChecks()\n"
        "async def main():\n"
        "    assert not await checks.passes('storage', time.sleep, 30, timeout=0.1)\n"
        "    checks.close()\n"
        "asyncio.run(main())\n"
    )
    # The image runs the tests from outside the source tree, so find the
    # server package the suite itself imported.
    source = str(Path(server_app.__file__).resolve().parents[1])
    env = {**os.environ, "PYTHONPATH": source}
    began = time.monotonic()
    subprocess.run(
        [sys.executable, "-c", script],
        cwd=source,
        env=env,
        check=True,
        timeout=20,
    )
    assert time.monotonic() - began < 10
