"""The request and download paths under load and faults (#244).

Many people asking for the same songs at once; a database that answers
"database is locked"; a disk that is full. A request answers 503 for a busy
database (`server_busy` for a read, `busy_unconfirmed` for a write) and the
JSON 500 for anything else, never a bare 500, and nothing is left half-made.
The download worker records the fault on the job and carries on.
"""

import asyncio
import errno
import json
import shutil
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest

from server.downloader import Downloader
from server.queue_states import ACTIVE
from server.store import Store
from server.workers import WorkerStatus, supervise
from test_downloader import audio, queued  # noqa: F401 (a fixture)
from test_request_matrix import Household, release, track, unfinished_twins

BUSY = {"detail": "The server is busy. Try again shortly.", "code": "server_busy"}
UNCONFIRMED = {
    "detail": "The server was busy. Check whether this was saved before trying again.",
    "code": "busy_unconfirmed",
}
LOCKED = sqlite3.OperationalError("database is locked")
DISK_FULL = sqlite3.OperationalError("database or disk is full")


class Faulty:
    """A connection whose statements starting with `prefix` meet `error`,
    from the `after`-th such statement on, `times` times."""

    def __init__(self, conn, fault):
        self._conn = conn
        self._fault = fault

    def execute(self, sql, *args):
        self._fault.meet(sql)
        return self._conn.execute(sql, *args)

    def executemany(self, sql, *args):
        self._fault.meet(sql)
        return self._conn.executemany(sql, *args)

    def __getattr__(self, name):
        return getattr(self._conn, name)


class Fault:
    def __init__(self, prefix, error, after=1, times=1):
        self.prefix, self.error = prefix, error
        self.after, self.times = after, times
        self.seen = self.raised = 0
        self.lock = threading.Lock()

    def meet(self, sql):
        if not sql.lstrip().startswith(self.prefix):
            return
        with self.lock:
            self.seen += 1
            if self.seen < self.after or self.raised >= self.times:
                return
            self.raised += 1
        raise self.error


@contextmanager
def fault(store, monkeypatch, prefix, error, after=1, times=1):
    """Every connection the store opens meets the fault (the seam the request
    routes and the downloader write through), until the block ends."""
    original = store.connect
    happening = Fault(prefix, error, after, times)

    @contextmanager
    def connect():
        with original() as conn:
            yield Faulty(conn, happening)

    with monkeypatch.context() as patch:
        patch.setattr(store, "connect", connect)
        yield happening
    assert happening.raised, f"no statement starting {prefix!r} was met"


@pytest.fixture
def place(tmp_path, monkeypatch):
    household = Household(tmp_path, monkeypatch)
    with household.client:
        yield household


def queue_rows(store):
    with store.connect() as conn:
        return conn.execute("SELECT id,status,payload FROM queue").fetchall()


# The request path.


def test_a_locked_database_at_the_start_of_a_request_answers_busy_unconfirmed(
    place, monkeypatch
):
    with fault(place.store, monkeypatch, "BEGIN IMMEDIATE", LOCKED):
        answer = place.client.post("/api/v1/requests", json={"tracks": [track(1)]})
    assert (answer.status_code, answer.json()) == (503, UNCONFIRMED)
    assert "retry-after" not in answer.headers
    assert queue_rows(place.store) == []
    again = place.client.post("/api/v1/requests", json={"tracks": [track(1)]})
    assert again.json()["created"] == 1, again.text


def test_an_album_whose_second_song_meets_the_lock_leaves_no_job(place, monkeypatch):
    # Nothing half-made: the first song's job goes with the second's.
    songs = [track(1, release(1, 1)), track(1, release(1, 1), number=2, title="Other")]
    with fault(place.store, monkeypatch, "INSERT INTO queue", LOCKED, after=2):
        answer = place.client.post("/api/v1/requests", json={"tracks": songs})
    assert (answer.status_code, answer.json()) == (503, UNCONFIRMED)
    assert queue_rows(place.store) == []
    again = place.client.post("/api/v1/requests", json={"tracks": songs})
    assert again.json()["created"] == 2, again.text


def test_joining_a_job_that_meets_the_lock_changes_nothing(place, monkeypatch):
    member = place.clients[place.member]
    assert member.post("/api/v1/requests", json={"tracks": [track(1)]}).json()[
        "created"
    ]
    before = queue_rows(place.store)
    with fault(place.store, monkeypatch, "UPDATE queue SET payload", LOCKED):
        answer = place.client.post("/api/v1/requests", json={"tracks": [track(1)]})
    assert (answer.status_code, answer.json()) == (503, UNCONFIRMED)
    assert queue_rows(place.store) == before
    joined = place.client.post("/api/v1/requests", json={"tracks": [track(1)]})
    assert joined.json()["existing"] == 1, joined.text
    ((_, _, payload),) = queue_rows(place.store)
    assert sorted(json.loads(payload)["requested_by"]) == sorted(
        [place.owner, place.member]
    )


def test_reading_requests_from_a_locked_database_says_try_again(place, monkeypatch):
    place.client.post("/api/v1/requests", json={"tracks": [track(1)]})
    with fault(place.store, monkeypatch, "SELECT", LOCKED, times=1000):
        answer = place.client.get("/api/v1/requests")
    assert (answer.status_code, answer.json()) == (503, BUSY)
    assert answer.headers["retry-after"] == "2"
    assert place.client.get("/api/v1/requests").status_code == 200


def test_a_retry_that_meets_the_lock_leaves_the_job_failed(place, monkeypatch):
    place.client.post("/api/v1/requests", json={"tracks": [track(1)]})
    ((job_id, _, _),) = queue_rows(place.store)
    place.set_status(job_id, "failed")
    with fault(place.store, monkeypatch, "UPDATE queue SET status", LOCKED):
        answer = place.client.post(f"/api/v1/requests/{job_id}/retry")
    assert (answer.status_code, answer.json()) == (503, UNCONFIRMED)
    assert [r[1] for r in queue_rows(place.store)] == ["failed"]


def test_a_playlist_whose_request_meets_the_lock_is_saved_and_can_be_added_again(
    place, monkeypatch
):
    # The playlist is its own transaction, committed before the songs are
    # asked for: "may have been saved" is the truth. Adding it again (what the
    # answer invites) asks for the songs and keeps one playlist.
    body = {
        "apple_playlist_id": "pl.faults",
        "name": "Faults",
        "songs": [{"title": "Matrix Song 1", "artist": "Matrix Artist 1"}],
        "download_missing": True,
    }
    with fault(place.store, monkeypatch, "INSERT INTO queue", LOCKED):
        answer = place.client.post("/api/v1/imports/apple-music", json=body)
    assert (answer.status_code, answer.json()) == (503, UNCONFIRMED)
    assert queue_rows(place.store) == []
    again = place.client.post("/api/v1/imports/apple-music", json=body)
    assert again.status_code == 200, again.text
    assert again.json()["counts"]["requested"] == 1
    names = [
        p["name"] for p in place.client.get("/api/v1/playlists").json()["playlists"]
    ]
    assert names.count("Faults") == 1


@pytest.mark.server_error_expected
@pytest.mark.parametrize(
    "error",
    [DISK_FULL, OSError(errno.ENOSPC, "No space left on device")],
    ids=["sqlite", "os"],
)
def test_a_full_disk_answers_the_json_500_and_makes_nothing(place, monkeypatch, error):
    songs = [track(1, release(1, 1)), track(1, release(1, 1), number=2, title="Other")]
    with fault(place.store, monkeypatch, "INSERT INTO queue", error, after=2):
        answer = place.client.post(
            "/api/v1/requests",
            json={"tracks": songs},
            headers={"X-Request-ID": "phone-full-disk"},
        )
    assert answer.status_code == 500
    assert answer.json() == {
        "detail": "Something went wrong on the server.",
        "code": "server_error",
        "request_id": "phone-full-disk",
    }
    assert queue_rows(place.store) == []
    assert (
        place.client.post("/api/v1/requests", json={"tracks": songs}).json()["created"]
        == 2
    )


# Many at once.


def test_many_people_asking_for_the_same_songs_at_once_make_one_job_each(place):
    # Three songs: new, failed under a release (#235's state), and removed.
    store = place.store
    place.clients[place.owner].post(
        "/api/v1/requests", json={"tracks": [track(2, release(2, 1))]}
    )
    place.clients[place.member].post(
        "/api/v1/requests", json={"tracks": [track(3, release(3, 1))]}
    )
    jobs = {n: place.jobs(track(n))[0][0] for n in (2, 3)}
    place.set_status(jobs[2], "failed")
    place.publish(jobs[3], 3)
    place.remove(place.member, 3)

    def variants(n):
        return [
            track(n),
            track(n, release(n, 1)),
            track(n, release(n, 2)),
            track(n, release(n, 1), explicit=True),
        ]

    def ask(i):
        n = (1, 2, 3)[i % 3]
        song = variants(n)[i // 3 % 4]
        who = (place.owner, place.member)[i % 2]
        answer = place.clients[who].post("/api/v1/requests", json={"tracks": [song]})
        return answer.status_code, answer.json()

    def monitor(i):
        # Artist monitoring runs beside the routes, on its own connection.
        n = (1, 2, 3)[i % 3]
        Store(store.directory).submit_catalogue(
            [{**track(n, release(n, 1)), "status": "missing"}], user_id=place.owner
        )
        return 200, None

    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = [pool.submit(ask, i) for i in range(48)]
        futures += [pool.submit(monitor, i) for i in range(6)]
        answers = [f.result() for f in futures]
    for status, body in answers:
        assert status < 500 or (
            status == 503 and body["code"] in {"server_busy", "busy_unconfirmed"}
        ), (status, body)
    for n in (1, 2, 3):
        rows = place.jobs(track(n))
        assert not unfinished_twins(rows), rows
        # Every song is wanted; an ordinary request that came after an "only
        # this edition" one joined it, so which jobs there are depends on
        # the order the threads ran in.
        assert any(j[1] in ACTIVE for j in rows), rows


# The download path.


class Staged(Downloader):
    """Writes part of a file into staging, then meets `error`."""

    error = None

    def transfer(self, job_id, candidate, path):
        path.write_bytes(b"partial")
        if self.error:
            raise self.error
        shutil.copyfile(self.audio, path)


CANDIDATE = {
    "title": "Test Track",
    "channel": "Test Artist",
    "duration": "0:03",
    "source_url": "fixture",
    "quality": "320kbps",
}


def leftovers(store, worker):
    return sorted(p.name for p in worker.staging.iterdir()) + sorted(
        str(p) for p in worker.library_root.rglob("*") if p.is_file()
    )


def test_a_full_disk_during_a_transfer_fails_the_job_and_leaves_nothing(tmp_path):
    store = Store(tmp_path / "data")
    job = queued(store)
    worker = Staged(store, {"freemp3cloud": lambda *_: [CANDIDATE]})
    worker.error = OSError(errno.ENOSPC, "No space left on device")
    worker.process(job)
    (item,) = store.queue()["items"]
    assert item["status"] == "failed"
    assert leftovers(store, worker) == []
    assert store.library["stats"]["tracks"] == 0


def test_a_full_disk_when_the_file_moves_in_fails_the_job_and_leaves_nothing(
    tmp_path, monkeypatch, audio
):
    import os

    from server import store as store_module

    store = Store(tmp_path / "data")
    job = queued(store)
    worker = Staged(store, {"freemp3cloud": lambda *_: [CANDIDATE]})
    worker.audio = audio

    def full(source, target):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(store_module.os, "replace", full)
    worker.process(job)
    monkeypatch.setattr(store_module.os, "replace", os.replace)
    (item,) = store.queue()["items"]
    assert item["status"] == "failed"
    assert store.library["stats"]["tracks"] == 0
    # A journal written before the move is recovery's to clear, at the next
    # start: then nothing is left.
    store.recover_requests()
    store.sweep_staged()
    assert leftovers(store, worker) == []


def test_a_locked_database_during_a_download_fails_the_job_not_the_worker(
    tmp_path, monkeypatch
):
    store = Store(tmp_path / "data")
    job = queued(store)
    worker = Staged(store, {"freemp3cloud": lambda *_: [CANDIDATE]})
    with fault(store, monkeypatch, "UPDATE queue SET status", LOCKED):
        worker.process(job)
    (item,) = store.queue()["items"]
    assert item["status"] == "failed"
    assert leftovers(store, worker) == []


def test_the_download_worker_carries_on_after_a_locked_database_and_a_full_disk(
    tmp_path, monkeypatch
):
    # Its loop meets the lock (claiming a job) and is restarted; the job it
    # then claims meets a full disk and fails; the worker is still running.
    store = Store(tmp_path / "data")
    queued(store)
    worker = Staged(store, {"freemp3cloud": lambda *_: [CANDIDATE]})
    worker.error = OSError(errno.ENOSPC, "No space left on device")
    status = WorkerStatus()
    claim = store.claim_request
    calls = []

    def claim_once_locked():
        calls.append(1)
        if len(calls) == 1:
            raise sqlite3.OperationalError("database is locked")
        return claim()

    monkeypatch.setattr(store, "claim_request", claim_once_locked)

    async def scenario():
        task = asyncio.create_task(
            supervise(
                "downloads",
                worker.run,
                worker.stopping,
                status,
                backoff_start=0.01,
            )
        )
        for _ in range(500):
            await asyncio.sleep(0.01)
            items = await asyncio.to_thread(lambda: store.queue()["items"])
            if items[0]["status"] == "failed":
                break
        running = status.snapshot()["downloads"]
        worker.stopping.set()
        worker.wake.set()
        await asyncio.wait_for(task, 10)
        return running

    running = asyncio.run(scenario())
    assert store.queue()["items"][0]["status"] == "failed"
    assert running["restarts"] == 1 and running["running"], running
    assert "database is locked" in running["last_error"]
    assert leftovers(store, worker) == []
