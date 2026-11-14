"""Counters for the parity targets (#191, D-88): instant listening, first
sound, downloads and artwork, kept for a rolling week.

Events go to their own small database beside the main one, so counting never
waits on the main database's locks, never tells clients that library data
changed, and a rollback to an older release needs no schema step. They are
written in batches off the event loop, and only after something happened:
an idle server does no counting work. Nothing personal is kept: no person,
no song title, only the kind of event, its source and its time.
"""

import asyncio
import logging
import sqlite3
import statistics
import threading
import time
from contextlib import closing

log = logging.getLogger("pig_music.performance")

FILENAME = "performance.sqlite3"
RETENTION_SECONDS = 7 * 86400
# Events wait this long after the first of a batch before they are written.
FLUSH_SECONDS = 30
# The oldest are dropped first if writing falls this far behind.
PENDING_LIMIT = 5000
PRUNE_SECONDS = 3600

# The targets in #191.
TARGETS = {
    "taps_played": 0.90,
    "first_sound_median_ms": 1500,
    "first_sound_p90_ms": 3000,
    "mix_downloads_completed": 0.80,
    "library_downloads_completed": 0.95,
    "artwork_placeholders": 0.02,
    "artists_with_photo": 0.95,
}

SCHEMA = """CREATE TABLE IF NOT EXISTS events (
    at REAL NOT NULL,
    metric TEXT NOT NULL,
    label TEXT NOT NULL,
    outcome TEXT NOT NULL,
    ms INTEGER,
    detail TEXT)"""


def connect(path):
    conn = sqlite3.connect(path, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(SCHEMA)
    conn.execute("CREATE INDEX IF NOT EXISTS events_at ON events(at)")
    return conn


class Performance:
    """Thread-safe: `record` may be called from the event loop or a worker."""

    def __init__(self, directory):
        self.path = directory / FILENAME
        self.lock = threading.Lock()
        self.pending = []
        self.dropped = 0
        self.pruned_at = None
        self.loop = None
        self.wake = None
        self.task = None

    def record(self, metric, label, outcome, ms=None, detail=None, at=None):
        event = (
            time.time() if at is None else at,
            metric,
            str(label),
            str(outcome),
            None if ms is None else max(0, int(round(ms))),
            None if detail is None else str(detail)[:200],
        )
        with self.lock:
            first = not self.pending
            self.pending.append(event)
            if len(self.pending) > PENDING_LIMIT:
                del self.pending[0]
                self.dropped += 1
        if first and self.loop is not None:
            try:
                self.loop.call_soon_threadsafe(self.wake.set)
            except RuntimeError:  # The loop has closed; close() flushed.
                pass

    def start(self):
        self.loop = asyncio.get_running_loop()
        self.wake = asyncio.Event()
        with self.lock:
            if self.pending:
                self.wake.set()
        self.task = asyncio.create_task(self.run())

    async def run(self):
        while True:
            await self.wake.wait()
            await asyncio.sleep(FLUSH_SECONDS)
            self.wake.clear()
            try:
                await asyncio.to_thread(self.flush)
            except Exception:
                log.exception("Writing performance counters failed")
                self.wake.set()  # The batch was kept: try again later.

    async def close(self):
        if self.task is not None:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None
        try:
            await asyncio.to_thread(self.flush)
        except Exception:
            log.exception("Writing performance counters failed")
        self.loop = None

    def flush(self, now=None):
        with self.lock:
            events, self.pending = self.pending, []
        now = time.time() if now is None else now
        prune = self.pruned_at is None or now - self.pruned_at >= PRUNE_SECONDS
        if not events and not prune:
            return 0
        try:
            with closing(connect(self.path)) as conn, conn:
                conn.executemany("INSERT INTO events VALUES (?,?,?,?,?,?)", events)
                if prune:
                    conn.execute(
                        "DELETE FROM events WHERE at < ?", (now - RETENTION_SECONDS,)
                    )
        except Exception:
            with self.lock:  # Kept for the next attempt, within the limit.
                room = max(0, PENDING_LIMIT - len(self.pending))
                kept = events[max(0, len(events) - room) :] if room else []
                self.dropped += len(events) - len(kept)
                self.pending[:0] = kept
            raise
        if prune:
            self.pruned_at = now
        return len(events)

    def report(self, since=None, until=None, artists=None):
        """The numbers for a period (the last week by default)."""
        self.flush()
        with closing(connect(self.path)) as conn:
            return report(conn, since, until, artists)


def percentile(values, share):
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round(share * (len(ordered) - 1))))
    return ordered[index]


def timing(values):
    return {
        "count": len(values),
        "median_ms": round(statistics.median(values)) if values else None,
        "p90_ms": percentile(values, 0.9),
    }


def share(part, whole):
    return round(part / whole, 4) if whole else None


def report(conn, since=None, until=None, artists=None):
    """The #191 numbers from an events database for [since, until) (epoch
    seconds; the last 7 days by default). `artists` is (with a photo, total)
    for the library, measured by the caller, or None."""
    until = time.time() if until is None else until
    since = until - RETENTION_SECONDS if since is None else since
    rows = conn.execute(
        "SELECT metric,label,outcome,ms,detail FROM events WHERE at>=? AND at<? "
        "ORDER BY at",
        (since, until),
    ).fetchall()
    taps = {"played": 0, "no match": 0, "unavailable": 0, "by_source": {}}
    tap_ms = []
    sources = {}
    sounds = []
    sounds_by = {}
    downloads = {
        kind: {"completed": 0, "failed": 0, "cancelled": 0, "by_source": {}}
        for kind in ("mix", "library")
    }
    artwork = {}
    supplied = {}
    for metric, label, outcome, ms, detail in rows:
        if metric == "tap":
            if outcome == "played":
                taps["played"] += 1
                taps["by_source"][label] = taps["by_source"].get(label, 0) + 1
            elif outcome in taps:
                taps[outcome] += 1
            if ms is not None and outcome == "played":
                tap_ms.append(ms)
        elif metric == "remote-source":
            entry = sources.setdefault(label, {"outcomes": {}, "found_ms": []})
            entry["outcomes"][outcome] = entry["outcomes"].get(outcome, 0) + 1
            if outcome == "found" and ms is not None:
                entry["found_ms"].append(ms)
        elif metric == "first-sound" and ms is not None:
            sounds.append(ms)
            sounds_by.setdefault(label, []).append(ms)
        elif metric == "download" and label in downloads:
            entry = downloads[label]
            if outcome in ("completed", "failed", "cancelled"):
                entry[outcome] += 1
            if outcome == "completed":
                source = detail or "unknown"
                entry["by_source"][source] = entry["by_source"].get(source, 0) + 1
        elif metric == "artwork-source":
            # Which source supplied each image saved (#194; #201's pass mark).
            entry = supplied.setdefault(label, {})
            entry[outcome] = entry.get(outcome, 0) + 1
        elif metric == "artwork":
            # A cover is a placeholder when no answer for it in the period
            # was the picture; one served late still counts as shown.
            items = artwork.setdefault(label, {"served": 0, "missing": 0, "items": {}})
            items[outcome] = items.get(outcome, 0) + 1
            if detail:
                items["items"][detail] = (
                    items["items"].get(detail, False) or outcome == "served"
                )
    asked = taps["played"] + taps["no match"] + taps["unavailable"]
    for kind in downloads.values():
        kind["completion"] = share(
            kind["completed"], kind["completed"] + kind["failed"]
        )
    covers = {}
    seen = shown = 0
    for kind, entry in sorted(artwork.items()):
        items = entry.pop("items")
        never = sum(1 for served in items.values() if not served)
        seen += len(items)
        shown += len(items) - never
        covers[kind] = {
            **entry,
            "distinct": len(items),
            "placeholders": never,
            "placeholder_share": share(never, len(items)),
        }
    result = {
        "since": since,
        "until": until,
        "targets": TARGETS,
        "instant": {
            "taps": asked,
            **taps,
            "played_share": share(taps["played"], asked),
            "server_answer": timing(tap_ms),
            "sources": {
                name: {"outcomes": entry["outcomes"], **timing(entry["found_ms"])}
                for name, entry in sorted(sources.items())
            },
        },
        "first_sound": {
            **timing(sounds),
            "by_mode": {
                mode: timing(values) for mode, values in sorted(sounds_by.items())
            },
        },
        "downloads": downloads,
        "artwork": {
            "kinds": covers,
            "distinct": seen,
            "placeholders": seen - shown,
            "placeholder_share": share(seen - shown, seen),
            "sources": supplied,
        },
        "artists": (
            None
            if artists is None
            else {
                "with_photo": artists[0],
                "total": artists[1],
                "share": share(artists[0], artists[1]),
            }
        ),
    }
    return result
