"""Keeps the long-running workers alive (#236).

The download, export and artist loops each ended for the life of the process
on any error their loop did not catch (say SQLite's "database is locked"),
silently: the bare task held the exception until shutdown. `supervise` logs
the error, waits and runs the worker again; `WorkerStatus` records what
happened for other code to read.
"""

import asyncio
import logging
import threading
import time

logger = logging.getLogger("pig_music.workers")

ERROR_LIMIT = 200


def describe(error):
    """The exception's class and message on one line, without a traceback."""
    text = " ".join(f"{type(error).__name__}: {error}".split())
    return text[:ERROR_LIMIT]


class WorkerStatus:
    """What each supervised worker is doing; safe to read from any thread."""

    def __init__(self):
        self.lock = threading.Lock()
        self.workers = {}

    def entry(self, name):
        return self.workers.setdefault(
            name,
            {
                # True while the worker's run() is executing; False while it
                # waits to be restarted and after it has stopped.
                "running": False,
                "started_at": None,
                "restarts": 0,
                # The first failure of the current unbroken run of failures.
                "failing_since": None,
                "last_error": None,
                "last_error_at": None,
            },
        )

    def started(self, name, restart=False):
        with self.lock:
            entry = self.entry(name)
            entry.update(running=True, started_at=time.time())
            if restart:
                entry["restarts"] += 1

    def failed(self, name, error):
        """Record a failure (`error` is its one-line text)."""
        now = time.time()
        with self.lock:
            entry = self.entry(name)
            entry.update(
                running=False, last_error=error[:ERROR_LIMIT], last_error_at=now
            )
            if entry["failing_since"] is None:
                entry["failing_since"] = now

    def healthy(self, name):
        with self.lock:
            self.entry(name)["failing_since"] = None

    def stopped(self, name):
        with self.lock:
            self.entry(name)["running"] = False

    def snapshot(self):
        with self.lock:
            return {name: dict(entry) for name, entry in self.workers.items()}


async def supervise(
    name,
    run,
    stopping,
    status,
    *,
    backoff_start=1.0,
    backoff_limit=60.0,
    healthy_after=300.0,
):
    """Run `run()` until `stopping` is set, restarting it whenever it ends.

    An error is recorded in `status` and logged: with its traceback the first
    time and whenever it differs from the one before, otherwise as one line
    with the repeat count. The wait before a restart doubles from
    `backoff_start` to `backoff_limit`; a run that lasted `healthy_after`
    seconds starts it over. Only cancellation leaves this by raising.
    """
    loop = asyncio.get_running_loop()
    backoff = backoff_start
    previous, repeats = None, 0
    restart = False
    while not stopping.is_set():
        status.started(name, restart)
        restart = True
        began = time.monotonic()
        # A worker that has run this long is no longer failing, whether or
        # not it ever ends.
        recovered = loop.call_later(healthy_after, status.healthy, name)
        error = None
        try:
            await run()
        except asyncio.CancelledError:
            status.stopped(name)
            raise
        except Exception as raised:
            error = raised
        finally:
            recovered.cancel()
        if error is None and stopping.is_set():
            break
        if time.monotonic() - began >= healthy_after:
            backoff = backoff_start
            status.healthy(name)
        text = describe(error) if error else "ended without being stopped"
        status.failed(name, text)
        repeats = repeats + 1 if text == previous else 1
        try:
            if error is None:
                logger.error(
                    "The %s worker ended without being stopped; restarting in %.0f s",
                    name,
                    backoff,
                )
            elif repeats == 1:
                logger.error(
                    "The %s worker failed; restarting in %.0f s",
                    name,
                    backoff,
                    exc_info=error,
                )
            else:
                logger.error(
                    "The %s worker failed again (%d times in a row): %s; restarting in %.0f s",
                    name,
                    repeats,
                    text,
                    backoff,
                )
        except Exception:
            pass  # A logging fault must not end the worker for good.
        previous = text
        try:
            await asyncio.wait_for(stopping.wait(), timeout=backoff)
        except TimeoutError:
            pass
        backoff = min(backoff * 2, backoff_limit)
    status.stopped(name)
