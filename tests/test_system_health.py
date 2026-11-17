"""The owner can see what broke and why without the container log (#241).

The supervisor writes how each run ended to logs/restarts.json; the server
keeps its newest unexpected errors and busy-database answers; the admin
System page reads them all from GET /api/v1/admin/system/health."""

import json
import sqlite3
from types import SimpleNamespace

from fastapi.testclient import TestClient

from server import runtime, system_health
from server.errors import Problems
from test_error_responses import ASYNC_PATH, fail, signed_in
from test_runtime_supervision import FakeProcess, Supervised, observe, watchdog
from test_ui_contract import household, sign_in

HEALTH = "/api/v1/admin/system/health"


def stopped_by_signal(run):
    run.handlers[runtime.signal.SIGTERM](runtime.signal.SIGTERM, None)
    return "ok", []


def test_each_run_records_its_start_and_how_it_ended(tmp_path, monkeypatch, capsys):
    # A release: the container is asked to stop.
    run = Supervised(
        tmp_path, monkeypatch, FakeProcess(lifetime=None), stopped_by_signal
    )
    assert runtime.main() == 0
    entries = run.entries()
    assert [e["kind"] for e in entries] == ["start", "stop"]
    assert "SIGTERM" in entries[1]["detail"]
    assert system_health.previous_run(entries + [{"at": 2e6, "kind": "start"}]) == {
        "at": entries[1]["at"],
        "kind": "stop",
        "sentence": "It was stopped on purpose (a release or a restart of the container).",
    }
    # A run that was killed writes nothing after its start...
    runtime.record_restart(run.history, 1_000_500, "start", "Server started")
    capsys.readouterr()
    # ...so the next start says so, then this run ends by itself.
    run = Supervised(
        tmp_path, monkeypatch, FakeProcess(lifetime=5), lambda r: ("ok", [])
    )
    assert runtime.main() == 1
    assert runtime.UNEXPECTED_END in capsys.readouterr().err
    entries = run.entries()
    assert [e["kind"] for e in entries] == [
        "start",
        "stop",
        "start",
        "unexpected",
        "start",
        "exit",
    ]
    # While that last run was going, the one before had ended unexpectedly.
    previous = system_health.previous_run(entries[:5])
    assert previous["kind"] == "unexpected"
    assert previous["sentence"] == (
        "It ended unexpectedly (the container was killed: out of memory,"
        " power or a forced stop)."
    )


def test_starts_and_stops_never_count_towards_the_watchdog_limit(tmp_path):
    dog, said, history = watchdog(tmp_path)
    wall = 1_000_000
    for n in range(12):
        for kind in ("start", "stop", "unexpected", "exit"):
            runtime.record_restart(history, wall - 60 * n, kind, kind)
    # Only watchdog restarts are limited: this silence still restarts.
    assert observe(dog, [("silent", [])] * 10, wall=wall)[-1] == "restart"
    entries = runtime.restart_history(history)
    # Starts and stops were asked for; the rest are restarts.
    assert system_health.restarts_last_day(entries, wall + 3600) == 12 + 12 + 1


def test_the_history_is_still_read_when_damaged(tmp_path):
    history = tmp_path / "restarts.json"
    history.write_text(
        '[{"at": 5, "kind": "stop"}, "junk", {"at": "x"}, {"at": 9, "kind": "start"}]'
    )
    entries = runtime.restart_history(history)
    assert system_health.previous_run(entries)["kind"] == "stop"
    assert system_health.previous_run([]) is None
    history.write_text("{broken")
    assert (
        system_health.report(1, history, SimpleNamespace(snapshot=dict), Problems())[
            "previous_run"
        ]
        is None
    )


def test_an_unexpected_error_is_kept_without_its_text_or_query(tmp_path, monkeypatch):
    secret = "SELECT * FROM users WHERE token='abc' /music/private.flac"
    with signed_in(tmp_path, monkeypatch) as (app, client):
        with fail(app, monkeypatch, ASYNC_PATH, RuntimeError(secret)):
            answer = client.get(ASYNC_PATH + "?token=hidden")
        assert answer.status_code == 500
        [entry] = app.state.problems.snapshot()["errors"]
        assert entry["request_id"] == answer.headers["x-request-id"]
        assert (entry["method"], entry["path"], entry["error"]) == (
            "GET",
            ASYNC_PATH,
            "RuntimeError",
        )
        assert entry["sentence"] == (
            f"Something went wrong on the server while handling GET {ASYNC_PATH}."
        )
        health = client.get(HEALTH)
        assert health.status_code == 200
        shown = health.json()["recent_errors"]
        assert shown[0]["request_id"] == entry["request_id"]
        text = json.dumps(shown)
        for leaked in ("hidden", "token", "SELECT", "private.flac"):
            assert leaked not in text


def test_only_the_newest_twenty_errors_are_kept_newest_first():
    problems = Problems()
    for n in range(25):
        problems.error(f"r{n}", "GET", f"/api/v1/x{n}?q=1", ValueError("no"))
    errors = problems.snapshot()["errors"]
    assert [e["request_id"] for e in errors] == [f"r{n}" for n in range(24, 4, -1)]
    assert errors[0]["path"] == "/api/v1/x24"


def test_busy_answers_are_counted(tmp_path, monkeypatch):
    with signed_in(tmp_path, monkeypatch) as (app, client):
        assert client.get(HEALTH).json()["busy"] == {"count": 0, "last_at": None}
        locked = sqlite3.OperationalError("database is locked")
        with fail(app, monkeypatch, ASYNC_PATH, locked):
            assert client.get(ASYNC_PATH).status_code == 503
            assert client.get(ASYNC_PATH).status_code == 503
        busy = client.get(HEALTH).json()["busy"]
        assert busy["count"] == 2 and busy["last_at"] > 0
        assert app.state.problems.snapshot()["errors"] == []


def test_the_health_report_is_for_the_owner_only(tmp_path, monkeypatch):
    app = household(tmp_path, monkeypatch)
    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    (logs / "restarts.json").write_text(
        json.dumps(
            [
                {"at": 100, "kind": "start", "detail": "Server started"},
                {"at": 200, "kind": "exit", "detail": runtime.exit_reason(-9)},
                {"at": 300, "kind": "start", "detail": "Server started"},
            ]
        )
    )
    monkeypatch.setattr(
        system_health,
        "startup_problems",
        SimpleNamespace(
            snapshot=lambda: [
                {"at": 5, "area": "tidy", "record": "x", "error": "/music/a.flac"}
            ]
        ),
    )
    with TestClient(app, base_url="https://private.ts.net") as client:
        assert client.get(HEALTH).status_code == 401
        sign_in(client, "member")
        assert client.get(HEALTH).status_code == 403
        sign_in(client, "mark")
        answer = client.get(HEALTH)
        assert answer.status_code == 200, answer.text
        body = answer.json()
        assert body["started_at"] > 0
        assert body["previous_run"] == {
            "at": 200,
            "kind": "exit",
            "sentence": "It was ended suddenly, possibly because it ran out of"
            " memory, and was restarted.",
        }
        assert body["recent_errors"] == []
        assert {"name", "running"} <= set(body["workers"][0])
        assert body["startup_problems"] == [
            {
                "at": 5,
                "area": "tidy",
                "sentence": "A saved tidy task could not be resumed and was set aside.",
            }
        ]
        assert "/music" not in answer.text


def test_workers_show_the_error_class_only():
    status = SimpleNamespace(
        snapshot=lambda: {
            "downloads": {
                "running": False,
                "restarts": 2,
                "last_error": "OperationalError: database is locked",
                "last_error_at": 50,
            }
        }
    )
    engine = {"configured": True, "connected": False}
    workers = system_health.report(1, "/nowhere", status, Problems(), engine)["workers"]
    assert workers == [
        {
            "name": "downloads",
            "running": False,
            "restarts": 2,
            "last_error": "OperationalError",
            "last_error_at": 50,
        },
        {"name": "source engine", "running": False},
    ]


def test_a_relay_token_in_the_path_is_never_kept_or_logged(
    tmp_path, monkeypatch, caplog
):
    """A relayed stream's path is its credential: an error while serving it
    keeps the route's template, never the token."""
    from server.remote import RemoteAudio

    token = "relaytoken0123456789secret"

    def broken(self, *args):
        raise RuntimeError("relay failed")

    monkeypatch.setattr(RemoteAudio, "relay", broken)
    with signed_in(tmp_path, monkeypatch) as (app, client):
        caplog.clear()
        answer = client.get(f"/api/v1/remote-playback/streams/{token}?x=1")
        assert answer.status_code == 500
        [entry] = app.state.problems.snapshot()["errors"]
        assert entry["path"] == "/api/v1/remote-playback/streams/{token}"
        health = client.get(HEALTH)
        assert health.status_code == 200
        assert token not in json.dumps(app.state.problems.snapshot())
        assert token not in health.text
        assert all(token not in r.getMessage() for r in caplog.records)
    # Without a matched route, request timing's redaction applies.
    from server.errors import shown_path

    assert shown_path({"path": f"/api/v1/remote-playback/streams/{token}"}) == (
        "/api/v1/remote-playback/streams/[redacted]"
    )
