"""Request timing, for finding where a slow request spent its time.

Every request gets an ID (the client's own `X-Request-ID` when it sends a
sensible one) and a list of timed phases. Both go back to the client
(`X-Request-ID`, `Server-Timing`), so a phone's diagnostics name the server's
share of a wait, and to the log for playback addresses and slow requests,
with how busy the worker threads were. A watcher logs when the event loop
itself was blocked. Paths are logged without their query, where signed media
links carry their signature, and a relayed stream's path is masked.
"""

import asyncio
import contextlib
import contextvars
import logging
import re
import secrets
import time

import anyio.to_thread

log = logging.getLogger("pig_music.timing")

SLOW_MS = 1000
CLIENT_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")
PHASE_NAME = re.compile(r"[a-z][a-z0-9-]{0,31}")
MAX_PHASES = 24

_trace = contextvars.ContextVar("pig_music_request_trace", default=None)


class Trace:
    __slots__ = ("id", "started", "phases")

    def __init__(self, identity):
        self.id = identity
        self.started = time.perf_counter()
        self.phases = []

    def elapsed(self):
        return (time.perf_counter() - self.started) * 1000

    def add(self, name, ms):
        # Bounded: a loop of phases must not grow a header or a log line.
        if len(self.phases) < MAX_PHASES and PHASE_NAME.fullmatch(name):
            self.phases.append((name, ms))


@contextlib.contextmanager
def phase(name):
    """Times a block as part of the current request; free outside one.
    Worker threads see the request's trace: they run in a copy of its context."""
    trace = _trace.get()
    began = time.perf_counter()
    try:
        yield
    finally:
        if trace is not None:
            trace.add(name, (time.perf_counter() - began) * 1000)


def mark(name):
    """Records how long after arrival the request reached this point, such as
    a sync endpoint finally getting a worker thread."""
    trace = _trace.get()
    if trace is not None:
        trace.add(name, trace.elapsed())


def pools():
    """How busy the two worker pools are: the event loop's default executor
    (`asyncio.to_thread`) and AnyIO's (sync endpoints and dependencies), as
    (busy, size, waiting) each. Call on the event loop. Reads private
    executor state, so best effort: None for a pool it cannot read."""
    default = anyio_pool = None
    try:
        loop = asyncio.get_running_loop()
        executor = getattr(loop, "_default_executor", None)
        if executor is not None:
            idle = executor._idle_semaphore._value
            default = (
                len(executor._threads) - idle,
                executor._max_workers,
                executor._work_queue.qsize(),
            )
    except Exception:
        pass
    try:
        statistics = anyio.to_thread.current_default_thread_limiter().statistics()
        anyio_pool = (
            statistics.borrowed_tokens,
            statistics.total_tokens,
            statistics.tasks_waiting,
        )
    except Exception:
        pass
    return {"asyncio": default, "anyio": anyio_pool}


def describe(state):
    return "; ".join(
        f"{name} {value[0]}/{value[1]} busy, {value[2]} waiting"
        for name, value in state.items()
        if value is not None
    )


def always_logged(method, path):
    """Playback addresses are logged every time, as a baseline for the slow ones."""
    return method == "POST" and (
        (path.startswith("/api/v1/playback/") and path.endswith("/url"))
        or path == "/api/v1/remote-playback"
    )


class RequestTiming:
    """Outermost app middleware: times the whole request, including the
    other middleware and authentication."""

    def __init__(self, app, slow_ms=SLOW_MS):
        self.app = app
        self.slow_ms = slow_ms

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        given = ""
        for key, value in scope.get("headers", ()):
            if key == b"x-request-id":
                given = value.decode("latin-1")
                break
        trace = Trace(given if CLIENT_ID.fullmatch(given) else secrets.token_hex(8))
        token = _trace.set(trace)
        status = None
        headers_ms = None

        async def timed(message):
            nonlocal status, headers_ms
            if message["type"] == "http.response.start":
                status = message["status"]
                headers_ms = trace.elapsed()
                headers = list(message.get("headers", ()))
                headers.append((b"x-request-id", trace.id.encode()))
                headers.append(
                    (b"server-timing", self.server_timing(trace, headers_ms))
                )
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, timed)
        finally:
            _trace.reset(token)
            self.report(scope, trace, status, headers_ms)

    @staticmethod
    def server_timing(trace, headers_ms):
        entries = [f"app;dur={headers_ms:.1f}"]
        entries += [f"{name};dur={ms:.1f}" for name, ms in trace.phases]
        return ", ".join(entries).encode()

    def report(self, scope, trace, status, headers_ms):
        method, path = scope.get("method", ""), scope.get("path", "")
        if not path.startswith("/api/"):
            return
        slow = headers_ms is None or headers_ms >= self.slow_ms
        if not (slow or always_logged(method, path) or (status or 0) >= 500):
            return
        # A relayed stream's path is its credential.
        if path.startswith("/api/v1/remote-playback/streams/"):
            path = "/api/v1/remote-playback/streams/[redacted]"
        phases = " ".join(f"{name}={ms:.0f}" for name, ms in trace.phases)
        answered = (
            f"{status} headers {headers_ms:.0f} ms"
            if headers_ms is not None
            else "no response"
        )
        line = (
            f"request {trace.id} {method} {path} {answered},"
            f" done {trace.elapsed():.0f} ms [{phases}]"
        )
        if slow:
            log.warning("%s; %s", line, describe(pools()))
        else:
            log.info("%s", line)


async def watch_event_loop(interval=0.25, blocked_ms=500, quiet_seconds=10):
    """Logs when the event loop could not run for a while, and when worker
    threads have a queue, at most once per `quiet_seconds` each."""
    loop = asyncio.get_running_loop()
    last_blocked = last_queued = 0.0
    while True:
        began = loop.time()
        await asyncio.sleep(interval)
        now = loop.time()
        lag = (now - began - interval) * 1000
        if lag >= blocked_ms and now - last_blocked >= quiet_seconds:
            last_blocked = now
            log.warning("event loop blocked for %.0f ms; %s", lag, describe(pools()))
        state = pools()
        waiting = any(value and value[2] for value in state.values())
        if waiting and now - last_queued >= quiet_seconds:
            last_queued = now
            log.warning("worker threads saturated; %s", describe(state))
