"""Reading Trash, organisation and tidy-up records one row at a time (#240).

Every reader of `removed:`, `organisation:` and `tidy:` documents goes
through here. A row that is not a JSON object, or lacks the fields its
readers rely on, is left as it is and skipped, so one bad record never
stops the server starting, a list showing or an Undo of a healthy record.
Each is logged and listed for the System page once per process.
"""

import json
import logging
import math
import sqlite3
import threading

from . import startup_problems

log = logging.getLogger("pig_music.records")

# Document prefix: the area the System page names.
AREAS = {"removed": "trash", "organisation": "organisation", "tidy": "tidy"}


# The shape checks require what every release has written and its readers
# use without a default; a field later releases added is checked only when
# present, and its readers tolerate its absence. Every value a reader binds
# into SQL or uses as a path is type-checked here, so a record either passes
# whole or is set aside before anything acts on it.

NUMBER = (int, float)


def _fields(record, kinds):
    for field, kind in kinds:
        if not isinstance(record[field], kind):
            raise TypeError(field)


def _optional(record, kinds):
    for field, kind in kinds:
        if field in record and not isinstance(record[field], kind):
            raise TypeError(field)


def _strings(values, field):
    if not all(isinstance(value, str) for value in values):
        raise TypeError(field)


def _rows(values, kinds, field):
    """Each value a list whose columns have the types in `kinds`."""
    for row in values:
        if not isinstance(row, list) or len(row) != len(kinds):
            raise TypeError(field)
        if not all(isinstance(v, kind) for v, kind in zip(row, kinds)):
            raise TypeError(field)


INTEGER = (-(2**63), 2**63 - 1)  # What SQLite binds.
UNSIGNED = (0, 2**64 - 1)  # A device or inode number: compared, never bound.


def _text(value):
    """Text SQLite binds: it encodes as UTF-8 (json reads an escaped lone
    surrogate, which SQLite refuses)."""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("text that is not UTF-8") from None


def _integer(value, field, bounds=INTEGER):
    if not isinstance(value, bool) and not bounds[0] <= value <= bounds[1]:
        raise ValueError(field + " out of range")


def _finite(record, fields):
    """A time compared and converted with int(): json also reads NaN and
    Infinity, which int() refuses."""
    for field in fields:
        value = record.get(field)
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(field + " out of range")


# Only what a reader binds into SQL is held to SQLite's limits (#257): the
# song snapshot, paths and other JSON metadata keep any value json stores
# (a file's mtime_ns may pass 2**63; a file name need not be UTF-8).


def bindable_removal(record):
    for field in ("id", "track_id"):
        _text(record[field])
    for user, song, added, source in record.get("memberships", []):
        for text in (user, song, source):
            _text(text)
        _integer(added, "memberships")
    for number in record.get("quarantine_folder") or []:
        _integer(number, "quarantine_folder", UNSIGNED)
    _finite(record, ("removed_at", "purged_at"))


def bindable_operation(record):
    for field in ("id", "track_id"):
        _text(record[field])


def bindable_tidy(record):
    _text(record["id"])
    for text in (*record["moves"], *record["removals"]):
        _text(text)
    for row in record["memberships"]:
        for text in row:
            _text(text)
    for item in record.get("unresolved", []):
        _text(item["id"])
    _finite(record, ("time",))


def usable_removal(record):
    _fields(
        record,
        (
            ("id", str),
            ("status", str),
            ("track_id", str),
            ("root", str),
            ("original", str),
            ("trash", str),
            ("sha256", str),
        ),
    )
    # The song as it was; None when the snapshot had no entry for it.
    _optional(
        record,
        (
            ("track", (dict, type(None))),
            ("memberships", list),
            ("removed_at", NUMBER),
            ("purged_at", (*NUMBER, type(None))),
            # A purge's private folder name and its (device, inode).
            ("quarantine", (str, type(None))),
            ("quarantine_folder", (list, type(None))),
            ("operation_id", (str, type(None))),
        ),
    )
    # (user, song, added, source) rows, as library_members holds them.
    _rows(record.get("memberships", []), (str, str, int, str), "memberships")
    _rows(
        [record["quarantine_folder"]] if record.get("quarantine_folder") else [],
        (int, int),
        "quarantine_folder",
    )


def usable_operation(record):
    _fields(
        record,
        (
            ("id", str),
            ("status", str),
            ("track_id", str),
            ("track", dict),
            ("root", str),
            ("source", str),
            ("destination", str),
            ("backup", str),
            ("stage", str),
            ("before", str),
        ),
    )
    # Written with "publishing", the step every later status follows.
    if record["status"] not in {"preparing", "cancelled"}:
        _fields(record, (("after", str),))
    _optional(record, (("placed_from", dict),))


def usable_tidy(record):
    _fields(
        record,
        (
            ("id", str),
            ("status", str),
            ("moves", list),
            ("removals", list),
            ("memberships", list),
            ("time", (int, float)),
        ),
    )
    record["title"], record["artist"]
    _strings(record["moves"], "moves")
    _strings(record["removals"], "removals")
    # (user, song) rows of library_members this tidy-up added.
    _rows(record["memberships"], (str, str), "memberships")
    _optional(record, (("unresolved", list), ("dismissed", list)))
    for item in record.get("unresolved", []):
        if not isinstance(item, dict):
            raise TypeError("unresolved")
        _fields(item, (("id", str), ("kind", str)))


SHAPES = {
    "removed": usable_removal,
    "organisation": usable_operation,
    "tidy": usable_tidy,
}

BINDABLE = {
    "removed": bindable_removal,
    "organisation": bindable_operation,
    "tidy": bindable_tidy,
}

_reported = set()
_lock = threading.Lock()


def report(area, name, error):
    """A record that could not be read or settled: logged with its
    traceback and listed once per process, never on every read."""
    with _lock:
        if name in _reported:
            return
        _reported.add(name)
    log.error("Skipped %s record %s", area, name, exc_info=error)
    startup_problems.report(area, name, error)


# What a recovery step meets when the disk, the library mount or the
# database refuses it for now (full, locked, busy, an I/O error): the
# record stays and is tried again at the next start (#258).
RETRYABLE = (OSError, ValueError, KeyError, sqlite3.OperationalError)


def unfinished(area, name, error):
    """A readable record whose file step failed again at startup (disk
    full, permission denied, the library mount changed): logged and listed
    once per process; it stays as it is and is tried at the next start."""
    with _lock:
        if ("unfinished", name) in _reported:
            return
        _reported.add(("unfinished", name))
    log.error("Could not finish %s record %s yet", area, name, exc_info=error)
    startup_problems.report(area, name, error, kind="unfinished")


def check_new(name, record):
    """Raise ValueError if `record`, about to be saved as `name`, would not
    read back: checked before any file changes, so nothing is moved that
    Restore or Undo could not find again."""
    prefix = name.split(":", 1)[0]
    try:
        again = json.loads(json.dumps(record))
        SHAPES[prefix](again)
        BINDABLE[prefix](again)
    except Exception:
        raise ValueError(
            "This song's details can't be saved for Restore; nothing was changed."
        ) from None


def clear():
    """For tests: forget which records were reported."""
    with _lock:
        _reported.clear()


def parse(name, payload):
    """The record in `payload`, or None when it is unusable (reported)."""
    prefix = name.split(":", 1)[0]
    try:
        record = json.loads(payload)
        if not isinstance(record, dict):
            raise TypeError("record is not an object")
        SHAPES[prefix](record)
        BINDABLE[prefix](record)
    except Exception as error:
        report(AREAS[prefix], name, error)
        return None
    return record


def _fetch(source, sql, params):
    if hasattr(source, "connect"):
        with source.connect() as conn:
            return conn.execute(sql, params).fetchall()
    return source.execute(sql, params).fetchall()


def rows(source, prefix, where="", params=()):
    """(name, record) for each usable `prefix:` document. `source` is a
    store or an open connection; `where` adds SQL after the name range
    (guard any json_extract with json_valid: bad rows are still there)."""
    found = _fetch(
        source,
        "SELECT name,payload FROM documents WHERE name >= ? AND name < ?" + where,
        (prefix + ":", prefix + ";", *params),
    )
    usable = []
    for name, payload in found:
        record = parse(name, payload)
        if record is not None:
            usable.append((name, record))
    return usable


def records(source, prefix, where="", params=()):
    return [record for _, record in rows(source, prefix, where, params)]


def read(source, name):
    """The usable record called `name`, or {} when absent or unusable."""
    found = _fetch(source, "SELECT payload FROM documents WHERE name=?", (name,))
    if not found:
        return {}
    record = parse(name, found[0][0])
    return {} if record is None else record


def require_usable(source, names):
    """Raise ValueError if any present record among `names` is unusable:
    an Undo checks this before it changes anything, so a bad record stops
    it before it starts rather than part-way."""
    for name in names:
        found = _fetch(source, "SELECT payload FROM documents WHERE name=?", (name,))
        if found and parse(name, found[0][0]) is None:
            raise ValueError("A recorded step can't be read; nothing was changed.")
