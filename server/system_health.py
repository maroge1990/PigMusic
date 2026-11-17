"""What the owner's System page says about the server's own health (#241).

How the last run ended (from the supervisor's logs/restarts.json), the
supervised workers, the newest unexpected errors and busy-database answers,
and saved tasks that could not be resumed at startup. Every line is plain:
no exception text, SQL, payloads, query strings or file paths.
"""

import time

from pydantic import BaseModel

from .runtime import ENDINGS, restart_history

try:
    from . import startup_problems
except ImportError:  # Added by another change; none until then.
    startup_problems = None

DAY = 86400
# Endings nobody asked for: these are restarts.
UNASKED = {"exit", "watchdog", "unexpected"}


class PreviousRun(BaseModel):
    at: float
    kind: str
    sentence: str


class WorkerHealth(BaseModel):
    name: str
    running: bool
    restarts: int | None = None
    last_error: str | None = None
    last_error_at: float | None = None


class RecentError(BaseModel):
    at: float
    request_id: str
    method: str
    path: str
    error: str
    sentence: str


class BusyHealth(BaseModel):
    count: int
    last_at: float | None = None


class StartupProblem(BaseModel):
    at: float | None = None
    area: str
    sentence: str


class SystemHealth(BaseModel):
    started_at: float
    previous_run: PreviousRun | None = None
    restarts_last_day: int
    workers: list[WorkerHealth]
    recent_errors: list[RecentError]
    busy: BusyHealth
    startup_problems: list[StartupProblem]


def ending_sentence(entry):
    kind, detail = entry.get("kind"), str(entry.get("detail") or "")
    if kind == "stop":
        return "It was stopped on purpose (a release or a restart of the container)."
    if kind == "watchdog":
        return "It stopped answering for 5 minutes, so it was restarted."
    if kind == "unexpected":
        return (
            "It ended unexpectedly (the container was killed: out of memory,"
            " power or a forced stop)."
        )
    if "SIGKILL" in detail:
        return (
            "It was ended suddenly, possibly because it ran out of memory,"
            " and was restarted."
        )
    return "It stopped on its own with an error and was restarted."


def previous_run(entries):
    """How the run before this one ended: the newest ending before the newest
    start (or the newest ending at all, without a supervisor's start)."""
    starts = [i for i, entry in enumerate(entries) if entry.get("kind") == "start"]
    before = entries[: starts[-1]] if starts else entries
    for entry in reversed(before):
        if entry.get("kind") == "start":
            return None  # That run's end was never written.
        if entry.get("kind") in ENDINGS:
            return {
                "at": entry["at"],
                "kind": entry["kind"],
                "sentence": ending_sentence(entry),
            }
    return None


def restarts_last_day(entries, now):
    return sum(
        1
        for entry in entries
        if entry.get("kind") in UNASKED and 0 <= now - entry["at"] < DAY
    )


def worker_lines(snapshot):
    lines = []
    for name, entry in sorted(snapshot.items()):
        last = entry.get("last_error")
        lines.append(
            {
                "name": name,
                "running": bool(entry.get("running")),
                "restarts": entry.get("restarts", 0),
                # The class only ("OperationalError"), never its message.
                "last_error": (last.split(":", 1)[0] if last and ":" in last else None),
                "last_error_at": entry.get("last_error_at"),
            }
        )
    return lines


def startup_lines():
    if startup_problems is None:
        return []
    try:
        found = startup_problems.snapshot()
    except Exception:
        return []
    lines = []
    for problem in found or []:
        try:
            area = str(problem.get("area") or "saved")
            lines.append(
                {
                    "at": problem.get("at"),
                    "area": area,
                    "sentence": (
                        f"Couldn't resume a saved {area} task," " so it was set aside."
                    ),
                }
            )
        except Exception:
            continue
    return lines


def report(started_at, history_path, workers, problems, engine=None, now=None):
    """`engine` is the source engine's health ({"configured", "connected"}),
    shown as a worker once it is configured."""
    now = time.time() if now is None else now
    entries = restart_history(history_path)
    lines = worker_lines(workers.snapshot())
    if engine and engine.get("configured"):
        lines.append(
            {"name": "source engine", "running": bool(engine.get("connected"))}
        )
    seen = problems.snapshot()
    return {
        "started_at": started_at,
        "previous_run": previous_run(entries),
        "restarts_last_day": restarts_last_day(entries, now),
        "workers": lines,
        "recent_errors": seen["errors"],
        "busy": seen["busy"],
        "startup_problems": startup_lines(),
    }
