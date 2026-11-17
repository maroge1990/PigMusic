"""What a client is told when a request fails for a reason of the server's.

A busy database ("database is locked" once the busy timeout has passed) is
worth trying again: 503 with `Retry-After` and the code `server_busy`.
Anything else unexpected will not get better by asking again: a 500 with
the code `server_error` and the request's ID, which is the one in the
timing log line and beside the traceback. The body never carries the
exception's text, a path or SQL.

Both are ordinary responses to the middleware around them: they carry the
security headers, the request ID and the timing, and the timing line names
their status.
"""

import logging
import sqlite3

import anyio
from fastapi.responses import JSONResponse
from starlette.requests import ClientDisconnect

from .request_timing import REQUEST_ID

log = logging.getLogger("pig_music.errors")

RETRY_AFTER = 2
# A client that went away mid-request is not a fault of the server's:
# Starlette raises the first when a body or a stream loses its reader, and
# the other two come from the stream its function middleware relays through.
GONE = (ClientDisconnect, anyio.BrokenResourceError, anyio.EndOfStream)


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

    def __init__(self, app):
        self.app = app

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
    outermost, and `ServerErrors` must be inside them all."""
    app.add_middleware(ServerErrors)

    @app.exception_handler(sqlite3.OperationalError)
    async def database_error(request, exc):
        if not busy(exc):
            # Disk full, a malformed database: not something to try again.
            raise exc
        log.warning(
            "database busy: %s %s answered 503, request %s",
            request.method,
            request.url.path,
            request.scope.get(REQUEST_ID) or "",
        )
        return JSONResponse(
            {"detail": "The server is busy. Try again shortly.", "code": "server_busy"},
            status_code=503,
            headers={"Retry-After": str(RETRY_AFTER)},
        )
