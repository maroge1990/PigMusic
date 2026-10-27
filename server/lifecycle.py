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

    def acquire(self, blocking=True, timeout=-1, label=None):
        if not self._lock.acquire(blocking, timeout):
            return False
        depth = getattr(self._depth, "value", 0)
        self._depth.value = depth + 1
        if depth == 0:
            self.holder = (caller(label), time.monotonic())
        return True

    def release(self):
        depth = self._depth.value - 1
        self._depth.value = depth
        holder = self.holder if depth == 0 else None
        if depth == 0:
            self.holder = None
        self._lock.release()
        if holder:
            held = time.monotonic() - holder[1]
            if held >= self.warn_after:
                log.warning("%s held for %.1f s by %s", self.name, held, holder[0])

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


def coordinated(method):
    # The wrapper takes the lock before the method runs, so it names the
    # method itself (a worker thread would otherwise show as the holder).
    label = f"{method.__module__.rsplit('.', 1)[-1]}.{method.__qualname__}"

    @wraps(method)
    def run(self, *args, **kwargs):
        store = self.store if hasattr(self, "store") else self
        lock = store.lifecycle_lock
        if not isinstance(lock, TimedRLock):
            with lock:
                return method(self, *args, **kwargs)
        lock.acquire(label=label)
        try:
            return method(self, *args, **kwargs)
        finally:
            lock.release()

    return run
