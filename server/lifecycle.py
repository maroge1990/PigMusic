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

    def acquire(self, blocking=True, timeout=-1):
        if not self._lock.acquire(blocking, timeout):
            return False
        depth = getattr(self._depth, "value", 0)
        self._depth.value = depth + 1
        if depth == 0:
            self.holder = (caller(), time.monotonic())
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


def caller():
    """The first frame outside this module and contextlib: who took the lock."""
    frame = sys._getframe(2)
    here = os.path.normcase(__file__)
    while frame and (
        os.path.normcase(frame.f_code.co_filename) == here
        or frame.f_code.co_filename.endswith("contextlib.py")
    ):
        frame = frame.f_back
    if frame is None:
        return "?"
    module = os.path.basename(frame.f_code.co_filename).removesuffix(".py")
    return f"{module}.{frame.f_code.co_qualname}"


def coordinated(method):
    @wraps(method)
    def run(self, *args, **kwargs):
        store = self.store if hasattr(self, "store") else self
        with store.lifecycle_lock:
            return method(self, *args, **kwargs)

    return run
