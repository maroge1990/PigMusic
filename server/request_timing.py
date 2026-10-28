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
import collections
import contextlib
import contextvars
import logging
import re
import secrets
import sys
import threading
import time
import traceback

import anyio.to_thread

log = logging.getLogger("pig_music.timing")

SLOW_MS = 1000
CLIENT_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")
PHASE_NAME = re.compile(r"[a-z][a-z0-9-]{0,31}")
MAX_PHASES = 24

_trace = contextvars.ContextVar("pig_music_request_trace", default=None)

STARTED = time.time()
# Bounded history for the diagnostics endpoints: recent API requests, the
# headers times of each route, event-loop blocks and stall stacks. Deques
# append atomically, so the stall thread can add to them too.
RECENT = collections.deque(maxlen=500)
ROUTES = {}
# Route keys come from the app's own routes, so this is only a backstop.
MAX_ROUTES = 400
METHODS = {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}
BLOCKS = collections.deque(maxlen=200)
STALLS = collections.deque(maxlen=50)


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


def add_phase(name, ms):
    """Records a phase timed elsewhere (`ms` long) on the current request."""
    trace = _trace.get()
    if trace is not None:
        trace.add(name, ms)


def mark(name):
    """Records how long after arrival the request reached this point, such as
    a sync endpoint finally getting a worker thread."""
    trace = _trace.get()
    if trace is not None:
        trace.add(name, trace.elapsed())


def pools(loop=None):
    """How busy the two worker pools are: the event loop's default executor
    (`asyncio.to_thread`) and AnyIO's (sync endpoints and dependencies), as
    (busy, size, waiting) each. Call on the event loop. Reads private
    executor state, so best effort: None for a pool it cannot read. From
    another thread, pass the loop; AnyIO's pool is then unknown."""
    default = anyio_pool = None
    try:
        loop = loop or asyncio.get_running_loop()
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
        if not path.startswith("/api/") or path == "/api/health":
            return
        # A relayed stream's path is its credential.
        if path.startswith("/api/v1/remote-playback/streams/"):
            path = "/api/v1/remote-playback/streams/[redacted]"
        # Anything a client can make up is folded away: an unknown method or
        # an unmatched path never becomes a key of its own.
        route = getattr(scope.get("route"), "path", None)
        method = method if method in METHODS else "OTHER"
        state = scope.get("state")
        user = (state.get("user") if isinstance(state, dict) else None) or {}
        RECENT.append(
            {
                "id": trace.id,
                "at": round(time.time(), 3),
                "user": user.get("id"),
                "method": method,
                "path": path[:300],
                "route": route or "unmatched",
                "status": status,
                "headers_ms": None if headers_ms is None else round(headers_ms, 1),
                "done_ms": round(trace.elapsed(), 1),
                "phases": [[name, round(ms, 1)] for name, ms in trace.phases],
            }
        )
        if headers_ms is not None and route:
            key = f"{method} {route}"
            if key in ROUTES or len(ROUTES) < MAX_ROUTES:
                ROUTES.setdefault(key, collections.deque(maxlen=200)).append(headers_ms)
        slow = headers_ms is None or headers_ms >= self.slow_ms
        if not (slow or always_logged(method, path) or (status or 0) >= 500):
            return
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


class StallWatch:
    """A thread that logs where the event loop is stuck while it is stuck:
    after `stalled_seconds` without a heartbeat from `watch_event_loop`,
    then every `repeat_seconds` while the stall lasts. Stacks show files,
    lines and code, never values."""

    def __init__(self, stalled_seconds=0.5, repeat_seconds=5.0, check_every=0.05):
        self.stalled_seconds = stalled_seconds
        self.repeat_seconds = repeat_seconds
        self.check_every = check_every
        self.beat = time.monotonic()
        self.loop_thread = None
        self.loop = None
        self.stopping = threading.Event()

    def start(self):
        """Call on the event loop's thread."""
        self.loop_thread = threading.get_ident()
        self.loop = asyncio.get_running_loop()
        self.beat = time.monotonic()
        threading.Thread(
            target=self.watch, name="loop-stall-watch", daemon=True
        ).start()

    def stop(self):
        self.stopping.set()

    def watch(self):
        reported = None
        while not self.stopping.wait(self.check_every):
            beat, now = self.beat, time.monotonic()
            stuck = now - beat
            if stuck < self.stalled_seconds:
                continue
            if (
                reported
                and reported[0] == beat
                and now - reported[1] < self.repeat_seconds
            ):
                continue
            reported = (beat, now)
            try:
                stacks = self.snapshot()
            except Exception:
                log.exception("Could not read the stalled event loop's stack")
                continue
            if stacks is None:
                continue
            stack, workers = stacks
            # "server frames" rather than "...code:", which the credential
            # filter reads as a secret and masks the line after.
            text = stack + "".join(
                "\nworker thread, server frames\n" + w for w in workers
            )
            STALLS.append(
                {"at": round(time.time(), 3), "seconds": round(stuck, 1), "stack": text}
            )
            log.warning(
                "event loop stuck for %.1f s; %s; at:\n%s",
                stuck,
                describe(pools(self.loop)),
                text,
            )

    def snapshot(self):
        """The loop's stack and the innermost frames of workers in server
        code (a lock holder shows up there), as text. Frames are released on
        return: holding one keeps a finished job's locals in memory."""
        frames = sys._current_frames()
        try:
            frame = frames.pop(self.loop_thread, None)
            if frame is None:
                return None
            stack = "".join(traceback.format_stack(frame)[-25:]).rstrip()
            workers = []
            for other in frames.values():
                lines = traceback.format_stack(other)
                if "/server/" in "".join(lines):
                    workers.append("".join(lines[-6:]).rstrip())
            return stack, workers
        finally:
            frame = other = None
            frames.clear()


async def watch_event_loop(interval=0.25, blocked_ms=500, quiet_seconds=10, stall=None):
    """Logs when the event loop could not run for a while, and when worker
    threads have a queue, at most once per `quiet_seconds` each. Beats
    `stall`'s heartbeat."""
    loop = asyncio.get_running_loop()
    last_blocked = last_queued = 0.0
    while True:
        began = loop.time()
        if stall is not None:
            stall.beat = time.monotonic()
        await asyncio.sleep(interval)
        now = loop.time()
        if stall is not None:
            stall.beat = time.monotonic()
        lag = (now - began - interval) * 1000
        if lag >= blocked_ms:
            BLOCKS.append({"at": round(time.time(), 3), "ms": round(lag)})
        if lag >= blocked_ms and now - last_blocked >= quiet_seconds:
            last_blocked = now
            log.warning("event loop blocked for %.0f ms; %s", lag, describe(pools()))
        state = pools()
        waiting = any(value and value[2] for value in state.values())
        if waiting and now - last_queued >= quiet_seconds:
            last_queued = now
            log.warning("worker threads saturated; %s", describe(state))


def percentile(values, share):
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, round(share * (len(ordered) - 1)))], 1)


def diagnostics(user_id=None):
    """Recent server timing. With `user_id`, only that person's requests and
    stalls without their stacks (a member's view); otherwise everything,
    with each route's headers times (the owner's). Call on the event loop."""
    requests = [r for r in list(RECENT) if user_id is None or r["user"] == user_id]
    result = {
        "uptime_seconds": round(time.time() - STARTED),
        "requests": requests[-200:],
        "blocks": list(BLOCKS)[-100:],
        "stalls": [
            {k: v for k, v in stall.items() if user_id is None or k != "stack"}
            for stall in list(STALLS)
        ],
        "pools": {
            name: (
                None if value is None else dict(zip(("busy", "size", "waiting"), value))
            )
            for name, value in pools().items()
        },
    }
    if user_id is None:
        result["routes"] = sorted(
            (
                {
                    "route": route,
                    "count": len(values),
                    "p50_ms": percentile(values, 0.5),
                    "p95_ms": percentile(values, 0.95),
                    "max_ms": round(max(values), 1),
                }
                for route, values in ((k, list(v)) for k, v in list(ROUTES.items()))
                if values
            ),
            key=lambda r: -r["p95_ms"],
        )
    return result
