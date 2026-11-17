"""What a client is told when a request fails for a reason of the server's.

A busy database ("database is locked" once the busy timeout has passed) is
worth trying again, for a read: 503 with `Retry-After` and the code
`server_busy`. For a write it is not that simple. The busy error can arrive
after part of the change has committed (a playlist is inserted, then read
back on another connection), so the server cannot say whether it was saved
and only a read is safe to repeat blindly: anything but GET, HEAD and
OPTIONS gets 503 with the code `busy_unconfirmed` and no `Retry-After`.
Anything else unexpected will not get better by asking again: a 500 with
the code `server_error` and the request's ID, which is the one in the
timing log line and beside the traceback. The body never carries the
exception's text, a path or SQL.

Both are ordinary responses to the middleware around them: they carry the
security headers, the request ID and the timing, and the timing line names
their status.

`Problems` keeps what the owner's System page shows (#241): the newest
unexpected errors (time, request ID, method, path, the exception's class
and a plain sentence) and how often the database was busy. It never keeps
the exception's text, a query string, a payload, SQL or a file path.
"""

import collections
import logging
import sqlite3
import threading
import time

import anyio
from fastapi.responses import JSONResponse
from starlette.requests import ClientDisconnect

from .request_timing import REQUEST_ID

log = logging.getLogger("pig_music.errors")

RETRY_AFTER = 2
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# A client that went away mid-request is not a fault of the server's:
# Starlette raises the first when a body or a stream loses its reader, and
# the other two come from the stream its function middleware relays through.
GONE = (ClientDisconnect, anyio.BrokenResourceError, anyio.EndOfStream)


RECENT_ERRORS = 20


class Problems:
    """What went wrong since the server started; safe from any thread."""

    def __init__(self, limit=RECENT_ERRORS):
        self.lock = threading.Lock()
        self.errors = collections.deque(maxlen=limit)
        self.busy_count = 0
        self.busy_at = None

    def error(self, request_id, method, path, exc):
        # The path only: the query string is not part of scope["path"], and
        # is cut here too in case one is ever passed.
        path = str(path).split("?", 1)[0]
        entry = {
            "at": time.time(),
            "request_id": str(request_id or ""),
            "method": str(method or ""),
            "path": path,
            "error": type(exc).__name__,
            "sentence": (
                "Something went wrong on the server while handling" f" {method} {path}."
            ),
        }
        with self.lock:
            self.errors.append(entry)

    def busy(self):
        with self.lock:
            self.busy_count += 1
            self.busy_at = time.time()

    def snapshot(self):
        """Newest error first."""
        with self.lock:
            return {
                "errors": [dict(entry) for entry in reversed(self.errors)],
                "busy": {"count": self.busy_count, "last_at": self.busy_at},
            }


def busy(exc):
    """A database another writer held for the whole busy timeout."""
    text = str(exc).lower()
    return "locked" in text or "busy" in text


class ServerErrors:
    """Innermost app middleware: answers whatever the routes did not handle
    with the JSON 500, once, and logs it once with its traceback. An error
    after the response has begun cannot be answered: it is logged and raised
    on, for the server to end the connection. Cancellation and a client that
    has gone pass through untouched."""

    def __init__(self, app, problems=None):
        self.app = app
        self.problems = problems if problems is not None else Problems()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def watched(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, watched)
        except GONE:
            raise
        except Exception as exc:
            identity = scope.get(REQUEST_ID) or ""
            log.error(
                "unhandled error: %s %s %s, request %s",
                scope.get("method", ""),
                scope.get("path", ""),
                "failed after its response began" if started else "answered 500",
                identity,
                exc_info=exc,
            )
            try:
                self.problems.error(
                    identity, scope.get("method", ""), scope.get("path", ""), exc
                )
            except Exception:
                pass  # Never let the record keep the answer from being sent.
            if started:
                raise
            response = JSONResponse(
                {
                    "detail": "Something went wrong on the server.",
                    "code": "server_error",
                    "request_id": identity,
                },
                status_code=500,
            )
            await response(scope, receive, send)


def install(app):
    """Call before any other `add_middleware`: the last one added is the
    outermost, and `ServerErrors` must be inside them all. What went wrong
    is kept in `app.state.problems`."""
    problems = Problems()
    app.state.problems = problems
    app.add_middleware(ServerErrors, problems=problems)

    @app.exception_handler(sqlite3.OperationalError)
    async def database_error(request, exc):
        if not busy(exc):
            # Disk full, a malformed database: not something to try again.
            raise exc
        problems.busy()
        # A write may have committed some of its change before this.
        safe = request.method in SAFE_METHODS
        log.warning(
            "database busy: %s %s answered 503 (%s), request %s",
            request.method,
            request.url.path,
            "try again" if safe else "unconfirmed, may have been saved",
            request.scope.get(REQUEST_ID) or "",
        )
        if safe:
            return JSONResponse(
                {
                    "detail": "The server is busy. Try again shortly.",
                    "code": "server_busy",
                },
                status_code=503,
                headers={"Retry-After": str(RETRY_AFTER)},
            )
        return JSONResponse(
            {
                "detail": "The server was busy. Check whether this was saved before trying again.",
                "code": "busy_unconfirmed",
            },
            status_code=503,
        )
