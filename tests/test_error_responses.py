"""A busy database is told apart from a broken request (#239).

Faults are raised where real ones come from: inside the worker thread of an
async endpoint (`asyncio.to_thread`, GET /api/v1/playlists) and of a sync
one (the thread pool, GET /api/v1/admin/queue), behind real sign-in and the
whole middleware stack. The client is Starlette's default one, which raises
whatever the app lets out: every answer here was sent with nothing raised
on to the server.
"""

import asyncio
import logging
import re
import sqlite3
from contextlib import contextmanager

import anyio
from fastapi.testclient import TestClient
import pytest
from starlette.requests import ClientDisconnect
from starlette.responses import PlainTextResponse

from server.errors import ServerErrors
from server.playlists import Playlists
from server.request_timing import REQUEST_ID
from test_security import PASSWORD, protected

ASYNC_PATH = "/api/v1/playlists"
SYNC_PATH = "/api/v1/admin/queue"
BUSY = {"detail": "The server is busy. Try again shortly.", "code": "server_busy"}
UNCONFIRMED = {
    "detail": "The server was busy. Check whether this was saved before trying again.",
    "code": "busy_unconfirmed",
}
SECURITY_HEADERS = (
    "x-content-type-options",
    "referrer-policy",
    "cross-origin-opener-policy",
    "permissions-policy",
    "content-security-policy",
    "strict-transport-security",
)


@contextmanager
def signed_in(tmp_path, monkeypatch):
    app, _ = protected(tmp_path, monkeypatch)
    token = app.state.auth.login("mark", PASSWORD, "device", "phone", "ios", "fixture")[
        "token"
    ]
    with TestClient(
        app,
        base_url="https://private.ts.net",
        headers={"Authorization": "Bearer " + token},
    ) as client:
        assert client.get(ASYNC_PATH).status_code == 200
        assert client.get(SYNC_PATH).status_code == 200
        yield app, client


@contextmanager
def fail(app, monkeypatch, path, error):
    """Makes the endpoint at `path` meet `error` in its worker thread, until
    the block ends."""

    def broken(*args, **kwargs):
        raise error

    with monkeypatch.context() as patch:
        if path == ASYNC_PATH:
            patch.setattr(Playlists, "list", broken)
        else:
            patch.setattr(app.state.store, "queue", broken)
        yield


def timing_line(caplog, path):
    """The timing middleware's log line for the one request to `path`."""
    lines = [
        r.getMessage()
        for r in caplog.records
        if r.name == "pig_music.timing" and f" GET {path} " in r.getMessage()
    ]
    assert len(lines) == 1, lines
    return lines[0]


def timing_id(caplog, path):
    return re.match(r"request (\S+) ", timing_line(caplog, path)).group(1)


def tracebacks(caplog):
    return [r for r in caplog.records if r.exc_info]


def passed_through_the_stack(answer):
    """The security headers, the request ID and the timing are all there."""
    for name in SECURITY_HEADERS:
        assert name in answer.headers, name
    assert answer.headers["cache-control"] == "no-store"
    assert answer.headers["server-timing"].startswith("app;dur=")
    return answer.headers["x-request-id"]


@pytest.mark.parametrize("path", [ASYNC_PATH, SYNC_PATH])
def test_a_locked_database_answers_503_and_says_when_to_try_again(
    tmp_path, monkeypatch, caplog, path
):
    with signed_in(tmp_path, monkeypatch) as (app, client):
        caplog.set_level(logging.INFO)
        caplog.clear()
        with fail(
            app, monkeypatch, path, sqlite3.OperationalError("database is locked")
        ):
            answer = client.get(path)
        assert answer.status_code == 503
        assert answer.json() == BUSY
        assert answer.headers["retry-after"] == "2"
        identity = passed_through_the_stack(answer)
        assert timing_id(caplog, path) == identity
        assert f" GET {path} 503 headers " in timing_line(caplog, path)
        assert tracebacks(caplog) == []
        (warning,) = [r for r in caplog.records if r.name == "pig_music.errors"]
        assert warning.levelno == logging.WARNING
        assert f"GET {path}" in warning.getMessage()
        assert identity in warning.getMessage()
        assert "\n" not in warning.getMessage()
        assert client.get(path).status_code == 200


def test_sqlite_busy_is_busy_too(tmp_path, monkeypatch):
    with signed_in(tmp_path, monkeypatch) as (app, client):
        error = sqlite3.OperationalError("Database is BUSY")
        with fail(app, monkeypatch, ASYNC_PATH, error):
            answer = client.get(ASYNC_PATH)
        assert (answer.status_code, answer.json()) == (503, BUSY)


def write(client, method, playlist):
    if method == "POST":
        return client.post(ASYNC_PATH, json={"name": "Second"})
    address = f"{ASYNC_PATH}/{playlist['id']}"
    if method == "PATCH":
        return client.patch(
            address, json={"revision": playlist["revision"], "name": "Renamed"}
        )
    return client.delete(address, params={"revision": playlist["revision"]})


@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE"])
def test_a_locked_database_behind_a_write_is_not_told_to_try_again(
    tmp_path, monkeypatch, caplog, method
):
    # The write itself meets the lock here; the server still cannot know in
    # general how far a change got, so no write is offered a blind retry.
    with signed_in(tmp_path, monkeypatch) as (app, client):
        playlist = client.post(ASYNC_PATH, json={"name": "First"}).json()
        caplog.set_level(logging.INFO)
        caplog.clear()

        def locked(*args, **kwargs):
            raise sqlite3.OperationalError("database is locked")

        with monkeypatch.context() as patch:
            patch.setattr(Playlists, "create" if method == "POST" else "edit", locked)
            answer = write(client, method, playlist)
        assert answer.status_code == 503
        assert answer.json() == UNCONFIRMED
        assert "retry-after" not in answer.headers
        identity = passed_through_the_stack(answer)
        assert tracebacks(caplog) == []
        (warning,) = [r for r in caplog.records if r.name == "pig_music.errors"]
        assert warning.levelno == logging.WARNING
        assert "unconfirmed" in warning.getMessage()
        assert f"{method} {ASYNC_PATH}" in warning.getMessage()
        assert identity in warning.getMessage()
        # A read meeting the same lock keeps the answer that invites a retry.
        with fail(app, monkeypatch, ASYNC_PATH, sqlite3.OperationalError("locked")):
            read = client.get(ASYNC_PATH)
        assert (read.status_code, read.json()) == (503, BUSY)
        assert read.headers["retry-after"] == "2"
        assert write(client, method, playlist).status_code == 200


def test_a_playlist_saved_before_the_database_was_busy_is_not_made_twice(
    tmp_path, monkeypatch
):
    # Playlists.create commits its INSERT, then reads the playlist back on
    # another connection. A lock met by that read used to answer "try again",
    # and the client that did had two playlists.
    with signed_in(tmp_path, monkeypatch) as (app, client):

        def locked(*args, **kwargs):
            raise sqlite3.OperationalError("database is locked")

        with monkeypatch.context() as patch:
            patch.setattr(Playlists, "detail", locked)
            answer = client.post(ASYNC_PATH, json={"name": "Road"})
        assert answer.status_code == 503
        assert answer.json() == UNCONFIRMED
        assert "retry-after" not in answer.headers
        saved = client.get(ASYNC_PATH).json()["playlists"]
        assert [p["name"] for p in saved] == ["Road"]


@pytest.mark.server_error_expected
@pytest.mark.parametrize("path", [ASYNC_PATH, SYNC_PATH])
def test_a_coding_fault_answers_a_500_that_names_its_request_and_nothing_else(
    tmp_path, monkeypatch, caplog, path
):
    with signed_in(tmp_path, monkeypatch) as (app, client):
        caplog.set_level(logging.INFO)
        caplog.clear()
        with fail(app, monkeypatch, path, KeyError("musicbrainz_release_id")):
            answer = client.get(path, headers={"X-Request-ID": "phone-1234-abcd"})
        assert answer.status_code == 500
        assert answer.headers["content-type"] == "application/json"
        body = answer.json()
        assert body == {
            "detail": "Something went wrong on the server.",
            "code": "server_error",
            "request_id": "phone-1234-abcd",
        }
        assert "musicbrainz" not in answer.text and "KeyError" not in answer.text
        assert "retry-after" not in answer.headers
        # An ordinary response to the middleware around it, the timing
        # included: its line says 500, not "no response".
        assert passed_through_the_stack(answer) == body["request_id"]
        assert timing_id(caplog, path) == body["request_id"]
        assert f" GET {path} 500 headers " in timing_line(caplog, path)
        # One error line, with the traceback, the address and the ID.
        (error,) = [r for r in caplog.records if r.name == "pig_music.errors"]
        assert error.levelno == logging.ERROR
        assert error.exc_info[0] is KeyError
        assert f"GET {path}" in error.getMessage()
        assert body["request_id"] in error.getMessage()
        assert tracebacks(caplog) == [error]
        assert client.get(path).status_code == 200


@pytest.mark.server_error_expected
def test_a_request_without_an_id_of_its_own_is_given_one(tmp_path, monkeypatch, caplog):
    with signed_in(tmp_path, monkeypatch) as (app, client):
        caplog.set_level(logging.INFO)
        caplog.clear()
        with fail(app, monkeypatch, ASYNC_PATH, RuntimeError("/mnt/user/private")):
            answer = client.get(ASYNC_PATH)
        identity = answer.json()["request_id"]
        assert re.fullmatch(r"[0-9a-f]{16}", identity)
        assert timing_id(caplog, ASYNC_PATH) == identity
        assert answer.headers["x-request-id"] == identity
        assert "/mnt" not in answer.text


@pytest.mark.server_error_expected
@pytest.mark.parametrize("path", [ASYNC_PATH, SYNC_PATH])
def test_a_full_disk_is_not_called_busy(tmp_path, monkeypatch, caplog, path):
    with signed_in(tmp_path, monkeypatch) as (app, client):
        caplog.set_level(logging.INFO)
        caplog.clear()
        error = sqlite3.OperationalError("database or disk is full")
        with fail(app, monkeypatch, path, error):
            answer = client.get(path)
        assert answer.status_code == 500
        body = answer.json()
        assert body["code"] == "server_error" and body["request_id"]
        assert body["detail"] == "Something went wrong on the server."
        assert "disk" not in answer.text
        assert "retry-after" not in answer.headers
        passed_through_the_stack(answer)
        (logged,) = [r for r in caplog.records if r.name == "pig_music.errors"]
        assert logged.levelno == logging.ERROR
        assert logged.exc_info[0] is sqlite3.OperationalError
        assert tracebacks(caplog) == [logged]
        assert client.get(path).status_code == 200


def test_not_found_and_invalid_requests_answer_as_before(tmp_path, monkeypatch):
    with signed_in(tmp_path, monkeypatch) as (app, client):
        missing = client.get(SYNC_PATH + "/nothing/log")
        assert missing.status_code == 404
        assert missing.json() == {
            "detail": "Not found in Downloads",
            "code": "not_found",
        }
        unmatched = client.get("/api/v1/nowhere")
        assert unmatched.status_code == 404
        assert unmatched.json() == {"detail": "Not Found", "code": "not_found"}
        invalid = client.post(ASYNC_PATH, json={})
        assert invalid.status_code == 422
        assert invalid.json() == {
            "detail": "Check the submitted fields",
            "code": "invalid_request",
        }
        for answer in (missing, unmatched, invalid):
            assert "retry-after" not in answer.headers
            assert "request_id" not in answer.json()
            assert "content-security-policy" in answer.headers
        assert client.get(ASYNC_PATH).status_code == 200


def test_the_error_middleware_is_the_innermost(tmp_path, monkeypatch):
    # The last middleware added is the outermost; this one must be inside
    # them all for its 500 to be an ordinary response to the rest.
    app, _ = protected(tmp_path, monkeypatch)
    assert app.user_middleware[-1].cls is ServerErrors
    assert [m.cls for m in app.user_middleware].count(ServerErrors) == 1


# The middleware alone, spoken to in ASGI: what a client or the server does
# that a test client cannot.


def call(app, scope=None):
    """Runs `app` behind the middleware; returns what was sent and raised."""
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = scope or {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/stream",
        "headers": [],
        REQUEST_ID: "abcd1234abcd1234",
    }
    raised = None
    try:
        asyncio.run(ServerErrors(app)(scope, receive, send))
    except BaseException as exc:
        raised = exc
    return sent, raised


@pytest.mark.server_error_expected
def test_an_error_before_the_response_is_answered_once_and_not_raised(caplog):
    async def app(scope, receive, send):
        raise KeyError("musicbrainz_release_id")

    sent, raised = call(app)
    assert raised is None
    assert [m["type"] for m in sent] == ["http.response.start", "http.response.body"]
    assert sent[0]["status"] == 500
    assert b'"request_id":"abcd1234abcd1234"' in sent[1]["body"]
    assert b"musicbrainz" not in sent[1]["body"]
    (logged,) = caplog.records
    assert logged.exc_info[0] is KeyError
    assert "GET /api/v1/stream answered 500" in logged.getMessage()


@pytest.mark.server_error_expected
def test_an_error_after_the_response_began_is_logged_and_raised_not_answered(
    caplog,
):
    error = OSError("the file went away")

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"abc", "more_body": True})
        raise error

    sent, raised = call(app)
    # The same exception, for the server to end the connection with.
    assert raised is error
    assert [m["type"] for m in sent] == ["http.response.start", "http.response.body"]
    assert sent[0]["status"] == 200
    (logged,) = caplog.records
    assert logged.levelno == logging.ERROR and logged.exc_info[1] is error
    assert "failed after its response began" in logged.getMessage()
    assert "abcd1234abcd1234" in logged.getMessage()


@pytest.mark.parametrize(
    "error",
    [
        ClientDisconnect(),
        anyio.BrokenResourceError(),
        anyio.EndOfStream(),
        asyncio.CancelledError(),
        KeyboardInterrupt(),
    ],
    ids=lambda error: type(error).__name__,
)
@pytest.mark.parametrize("started", [False, True])
def test_a_client_that_left_and_a_cancelled_request_pass_through_unlogged(
    caplog, error, started
):
    async def app(scope, receive, send):
        if started:
            await send({"type": "http.response.start", "status": 200, "headers": []})
        raise error

    sent, raised = call(app)
    assert raised is error
    assert len(sent) == int(started)
    assert caplog.records == []


def test_a_good_response_and_other_scopes_are_left_alone(caplog):
    sent, raised = call(PlainTextResponse("fine"))
    assert raised is None and sent[0]["status"] == 200
    assert sent[1]["body"] == b"fine"

    async def socket(scope, receive, send):
        raise KeyError("not http")

    sent, raised = call(socket, {"type": "websocket", "path": "/", "headers": []})
    assert isinstance(raised, KeyError) and sent == []
    assert caplog.records == []
