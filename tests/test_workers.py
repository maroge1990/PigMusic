"""#236: the download, export and artist workers survive an unexpected error,
say so in the log and restart. Each used to end for the life of the process
(live: SQLite's "database is locked" from a claim), silently until shutdown,
where awaiting the dead task raised and skipped the rest of the cleanup."""

import asyncio
import logging
import sqlite3
import threading
import time

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.artist_jobs import ArtistJobs
from server.downloader import Downloader
from server.exporter import Exporter
from server.store import Store
from server.workers import WorkerStatus, supervise
from test_idle_work import enable_downloads, queue_row

FAST = {"backoff_start": 0.01, "backoff_limit": 0.04}


async def until(condition, seconds=5):
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        await asyncio.sleep(0.005)


async def supervised(name, run, stopping, done, status=None, **options):
    """Supervise `run` until `done()` holds, then stop it; returns the status."""
    status = status or WorkerStatus()
    task = asyncio.create_task(
        supervise(name, run, stopping, status, **{**FAST, **options})
    )
    try:
        await until(done)
    finally:
        stopping.set()
        await asyncio.wait_for(task, 5)
    return status


def test_a_worker_that_raises_is_restarted_and_the_error_recorded(caplog):
    calls = []
    stopping = asyncio.Event()

    async def run():
        calls.append(1)
        if len(calls) <= 2:
            raise sqlite3.OperationalError("database is locked")
        await stopping.wait()

    async def scenario():
        status = WorkerStatus()
        task = asyncio.create_task(
            supervise("downloads", run, stopping, status, **FAST)
        )
        await until(lambda: len(calls) == 3)
        during = status.snapshot()["downloads"]
        stopping.set()
        await asyncio.wait_for(task, 5)
        return during, status.snapshot()["downloads"]

    with caplog.at_level(logging.ERROR, logger="pig_music.workers"):
        during, after = asyncio.run(scenario())
    assert len(calls) == 3
    assert during["running"] is True and during["restarts"] == 2
    assert during["last_error"] == "OperationalError: database is locked"
    assert during["last_error_at"] >= during["failing_since"] > 0
    assert during["started_at"] >= during["last_error_at"]
    assert after["running"] is False and after["restarts"] == 2
    # The traceback once; the same error again is one line with its count.
    first, second = caplog.records
    assert first.exc_info and "downloads" in first.getMessage()
    assert not second.exc_info and "2 times" in second.getMessage()
    assert "database is locked" in second.getMessage()


def test_a_different_error_is_logged_with_its_traceback_again(caplog):
    errors = [ValueError("one"), ValueError("one"), RuntimeError("two")]
    stopping = asyncio.Event()

    async def run():
        if errors:
            raise errors.pop(0)
        await stopping.wait()

    with caplog.at_level(logging.ERROR, logger="pig_music.workers"):
        asyncio.run(supervised("exports", run, stopping, lambda: not errors))
    assert [bool(record.exc_info) for record in caplog.records] == [True, False, True]


def test_the_recorded_error_is_one_trimmed_line():
    stopping = asyncio.Event()
    raised = []

    async def run():
        if not raised:
            raised.append(1)
            raise RuntimeError("first line\n" + "x" * 500)
        await stopping.wait()

    status = asyncio.run(supervised("exports", run, stopping, lambda: raised))
    error = status.snapshot()["exports"]["last_error"]
    assert error.startswith("RuntimeError: first line x")
    assert len(error) == 200 and "\n" not in error


def test_a_worker_that_returns_early_is_restarted(caplog):
    calls = []
    stopping = asyncio.Event()

    async def run():
        calls.append(1)
        if len(calls) == 1:
            return
        await stopping.wait()

    with caplog.at_level(logging.ERROR, logger="pig_music.workers"):
        status = asyncio.run(
            supervised("artists", run, stopping, lambda: len(calls) == 2)
        )
    entry = status.snapshot()["artists"]
    assert len(calls) == 2 and entry["restarts"] == 1
    assert "without being stopped" in entry["last_error"]
    assert "ended without being stopped" in caplog.records[0].getMessage()


def test_cancellation_propagates():
    async def scenario():
        status = WorkerStatus()
        started = asyncio.Event()

        async def run():
            started.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(
            supervise("downloads", run, asyncio.Event(), status, **FAST)
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return status.snapshot()["downloads"]

    entry = asyncio.run(scenario())
    assert entry["running"] is False and entry["restarts"] == 0


def test_stopping_during_the_backoff_ends_promptly():
    async def scenario():
        stopping = asyncio.Event()
        status = WorkerStatus()
        calls = []

        async def run():
            calls.append(1)
            raise RuntimeError("no")

        task = asyncio.create_task(
            supervise("downloads", run, stopping, status, backoff_start=30)
        )
        await until(lambda: calls and status.snapshot()["downloads"]["last_error"])
        started = time.monotonic()
        stopping.set()
        await asyncio.wait_for(task, 5)
        return time.monotonic() - started, calls

    waited, calls = asyncio.run(scenario())
    assert waited < 0.2 and len(calls) == 1


def test_the_backoff_doubles_to_its_limit_and_resets_after_a_healthy_run():
    # Runs 1-4 fail at once, run 5 fails after a healthy 0.05 s, run 6 fails
    # at once: the waits before runs 2-7 are 0.01, 0.02, 0.04, 0.04 (the
    # limit), then 0.01 again after the healthy run, then 0.02.
    starts, ends = [], []
    stopping = asyncio.Event()
    status = WorkerStatus()
    cleared = []

    async def run():
        starts.append(time.monotonic())
        try:
            if len(starts) == 5:
                await asyncio.sleep(0.05)
                # Healthy while still running: no longer reported as failing.
                cleared.append(status.snapshot()["downloads"]["failing_since"])
            if len(starts) <= 6:
                raise RuntimeError("no")
            await stopping.wait()
        finally:
            ends.append(time.monotonic())

    asyncio.run(
        supervised(
            "downloads",
            run,
            stopping,
            lambda: len(starts) == 7,
            status,
            healthy_after=0.03,
        )
    )
    waits = [start - end for start, end in zip(starts[1:], ends)]
    assert len(waits) == 6
    for wait, least in zip(waits, [0.01, 0.02, 0.04, 0.04, 0.01, 0.02]):
        assert least <= wait < least + 0.03
    assert cleared == [None]
    # The failure after the healthy run began a new run of failures.
    entry = status.snapshot()["downloads"]
    assert entry["failing_since"] is not None and entry["restarts"] == 6


def test_the_downloader_claims_a_later_job_after_a_locked_database(tmp_path):
    store = Store(tmp_path)
    enable_downloads(store)
    queue_row(store, "song")
    worker = Downloader(store, providers={})
    claim, claims, handled = store.claim_request, [], []

    def locked_once():
        claims.append(1)
        if len(claims) == 1:
            raise sqlite3.OperationalError("database is locked")
        return claim()

    async def handle(job):
        handled.append(job["id"])

    store.claim_request = locked_once
    worker.handle = handle
    status = asyncio.run(
        supervised("downloads", worker.run, worker.stopping, lambda: handled)
    )
    assert handled == ["song"]
    entry = status.snapshot()["downloads"]
    assert entry["restarts"] == 1
    assert entry["last_error"] == "OperationalError: database is locked"
    # Each run added and removed its own queue listener.
    assert store.queue_listeners == []


def test_the_exporter_runs_its_next_pass_after_a_failed_one(tmp_path):
    store = Store(tmp_path)
    exporter = Exporter(store, root=tmp_path / "library")
    passes = []

    def process():
        passes.append(1)
        if len(passes) == 1:
            raise RuntimeError("export failed")

    exporter.process = process
    status = asyncio.run(
        supervised("exports", exporter.run, exporter.stopping, lambda: len(passes) > 1)
    )
    entry = status.snapshot()["exports"]
    assert (
        entry["restarts"] == 1 and entry["last_error"] == "RuntimeError: export failed"
    )
    assert store.publish_listeners == []


def test_the_artist_worker_runs_its_next_pass_after_a_failed_one(tmp_path):
    jobs = ArtistJobs(Store(tmp_path), None)
    passes = []

    def pending():
        passes.append(1)
        if len(passes) == 1:
            raise sqlite3.OperationalError("database is locked")
        return []

    jobs.pending = pending
    status = asyncio.run(
        supervised(
            "artists", lambda: jobs.run(None), jobs.stopping, lambda: len(passes) > 1
        )
    )
    entry = status.snapshot()["artists"]
    assert entry["restarts"] == 1
    assert entry["last_error"] == "OperationalError: database is locked"


def test_shutdown_completes_after_a_worker_error(tmp_path, monkeypatch):
    # The dead export task used to raise from `await export_task`, skipping
    # the cleanup after it (the look-ahead and performance workers).
    failed = threading.Event()
    closed = []

    def process(self):
        failed.set()
        raise RuntimeError("export failed")

    monkeypatch.setattr(Exporter, "process", process)
    app = create_app(tmp_path)
    close = app.state.look_ahead.close

    async def closing():
        closed.append(1)
        await close()

    app.state.look_ahead.close = closing
    with TestClient(app) as client:
        assert failed.wait(5)
        assert client.get("/api/health").status_code == 200
        deadline = time.monotonic() + 5
        while not app.state.workers.snapshot()["exports"]["last_error"]:
            assert time.monotonic() < deadline
            time.sleep(0.01)
    assert closed == [1]
    entry = app.state.workers.snapshot()["exports"]
    assert entry["last_error"] == "RuntimeError: export failed"
    assert entry["running"] is False
    assert app.state.workers.snapshot()["downloads"]["restarts"] == 0
