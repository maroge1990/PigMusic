"""#238: /api/health/ready says whether the server can do its work, for the
container's health check and the supervisor's watchdog, and answers nobody
else."""

import asyncio
import logging
import threading
import time

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
    with TestClient(app, client=("192.168.1.20", 50000)) as client:
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
