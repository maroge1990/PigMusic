"""Records that startup recovery skipped (#240).

A recovery record that cannot be read or settled is left as it is and
skipped, so one bad record never stops the server starting; one whose file
step fails again (#258) is listed as unfinished and tried at the next
start. Each is kept here for the admin System page: the area, the record's document name
and the error's class only, never its text, a path or the payload.
"""

import threading
import time

LIMIT = 50

_lock = threading.Lock()
_problems = []


def report(area, name, exc, kind="set_aside"):
    """`kind` is "set_aside" (a record that could not be read or settled)
    or "unfinished" (its file step failed again; tried at the next start)."""
    entry = {
        "at": time.time(),
        "area": area,
        "record": name,
        "error": type(exc).__name__,
        "kind": kind,
    }
    with _lock:
        _problems.append(entry)
        del _problems[:-LIMIT]


def snapshot():
    with _lock:
        return [dict(entry) for entry in _problems]


def clear():
    """For tests: forget every reported problem."""
    with _lock:
        _problems.clear()
