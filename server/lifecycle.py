"""Serialize household file mutations with membership and publication changes."""

from functools import wraps
import logging
import os
import sys
import threading
import time

log = logging.getLogger("pig_music.timing")


class TimedRLock:
    """A re-entrant lock that logs any hold over `warn_after` seconds, with the
    code that took it (LOCK1: long holds stalled downloads, sync and playback)."""

    def __init__(self, name, warn_after=1.0):
        self.name, self.warn_after = name, warn_after
        self._lock = threading.RLock()
        self._depth = threading.local()
        self.holder = None  # (label, since) of the outermost hold
        # The holder's named steps, so a long hold says where it went (#17).
        self.steps, self._mark = [], 0.0

    def acquire(self, blocking=True, timeout=-1, label=None):
        if not self._lock.acquire(blocking, timeout):
            return False
        depth = getattr(self._depth, "value", 0)
        self._depth.value = depth + 1
        if depth == 0:
            self.holder = (caller(label), time.monotonic())
            self.steps, self._mark = [], self.holder[1]
        return True

    def step(self, name):
        """The holder finished `name`: its time joins a long hold's warning."""
        if not self.owned():
            return
        now = time.monotonic()
        self.steps.append((name, now - self._mark))
        self._mark = now

    def release(self):
        depth = self._depth.value - 1
        self._depth.value = depth
        holder = self.holder if depth == 0 else None
        steps = self.steps if depth == 0 else []
        if depth == 0:
            self.holder, self.steps = None, []
        self._lock.release()
        if holder:
            held = time.monotonic() - holder[1]
            if held >= self.warn_after:
                if steps:
                    rest = held - sum(seconds for _, seconds in steps)
                    parts = [*steps, ("rest", rest)]
                    detail = " (%s)" % ", ".join(
                        f"{name} {seconds:.2f} s" for name, seconds in parts
                    )
                else:
                    detail = ""
                log.warning(
                    "%s held for %.1f s by %s%s", self.name, held, holder[0], detail
                )

    def owned(self):
        """Whether the calling thread holds the lock."""
        return getattr(self._depth, "value", 0) > 0

    __enter__ = acquire

    def __exit__(self, *exc):
        self.release()


def caller(label=None):
    """Who took the lock: `label`, else the first frame outside this module
    and contextlib, then the server function that called it, so a shared
    helper such as `Store.refresh_library` names what asked for it."""
    frame = sys._getframe(2)
    here = os.path.normcase(__file__)
    package = os.path.dirname(here) + os.sep

    def name(frame):
        module = os.path.basename(frame.f_code.co_filename).removesuffix(".py")
        return f"{module}.{frame.f_code.co_qualname}"

    while frame and (
        os.path.normcase(frame.f_code.co_filename) == here
        or frame.f_code.co_filename.endswith("contextlib.py")
    ):
        frame = frame.f_back
    if frame is None:
        return label or "?"
    taker = label or name(frame)
    while frame and (
        not os.path.normcase(frame.f_code.co_filename).startswith(package)
        or os.path.normcase(frame.f_code.co_filename) == here
        or name(frame) == taker
    ):
        frame = frame.f_back
    return f"{taker} via {name(frame)}" if frame else taker


def step(lock, name):
    """Name a step of the current hold, for locks that keep them."""
    if isinstance(lock, TimedRLock):
        lock.step(name)


def coordinated(method):
    # The wrapper takes the lock before the method runs, so it names the
    # method itself (a worker thread would otherwise show as the holder).
    label = f"{method.__module__.rsplit('.', 1)[-1]}.{method.__qualname__}"

    @wraps(method)
    def run(self, *args, **kwargs):
        store = self.store if hasattr(self, "store") else self
        lock = store.lifecycle_lock
        # A download's library rebuild runs after its publication releases
        # the lock; whoever takes the lock in between rebuilds first (#74).
        catch_up = getattr(store, "catch_up", lambda: None)
        if not isinstance(lock, TimedRLock):
            with lock:
                catch_up()
                return method(self, *args, **kwargs)
        lock.acquire(label=label)
        try:
            catch_up()
            lock.step("catch-up")
            return method(self, *args, **kwargs)
        finally:
            lock.release()

    return run
