"""Reading Trash, organisation and tidy-up records one row at a time (#240).

Every reader of `removed:`, `organisation:` and `tidy:` documents goes
through here. A row that is not a JSON object, or lacks the fields its
readers rely on, is left as it is and skipped, so one bad record never
stops the server starting, a list showing or an Undo of a healthy record.
Each is logged and listed for the System page once per process.
"""

import json
import logging
import threading

from . import startup_problems

log = logging.getLogger("pig_music.records")

# Document prefix: the area the System page names.
AREAS = {"removed": "trash", "organisation": "organisation", "tidy": "tidy"}


# The shape checks require what every release has written and its readers
# use without a default; a field later releases added is checked only when
# present, and its readers tolerate its absence.


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


def _rows(values, size, field):
    if not all(isinstance(row, list) and len(row) == size for row in values):
        raise TypeError(field)


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
    _optional(record, (("track", (dict, type(None))), ("memberships", list)))
    # (user, song, added, source) rows, as library_members holds them.
    _rows(record.get("memberships", []), 4, "memberships")


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
    _rows(record["memberships"], 2, "memberships")
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
