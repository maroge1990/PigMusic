"""LOOP1: the event loop never waits for a household lock or the NAS."""

import asyncio
import json
import threading
import time

from fastapi.testclient import TestClient

from server.app import create_app
from server.downloader import Downloader
from server.user_library import initialise_owner
from test_security import PASSWORD, protected


def library(tmp_path):
    nas = tmp_path / "nas"
    nas.mkdir()
    (nas / "song.mp3").write_bytes(b"fixture audio bytes")
    song = {"id": "song", "title": "Song", "artist": "Artist", "album": "Album"}
    (tmp_path / "library-snapshot.json").write_text(
        json.dumps({"tracks": [{**song, "relative_path": "song.mp3"}]})
    )


def test_requests_answer_while_another_thread_holds_the_household_locks(
    tmp_path, monkeypatch
):
    library(tmp_path)
    app, _ = protected(tmp_path, monkeypatch)
    store = app.state.store
    token = app.state.auth.login("mark", PASSWORD, "device", "phone", "ios", "f")[
        "token"
    ]
    held, release = threading.Event(), threading.Event()

    def hold():
        # A download publishing (lifecycle_lock) while a library sync captures
        # (library_lock): the downloader's next store call has to wait.
        with store.lifecycle_lock, store.library_lock:
            held.set()
            release.wait(10)

    holder = threading.Thread(target=hold)
    prepare = Downloader.prepare_storage

    def prepare_storage(self):
        # The downloader's first pass meets the held locks in recover_requests.
        if not holder.is_alive() and not release.is_set():
            holder.start()
            held.wait(5)
        return prepare(self)

    monkeypatch.setattr(Downloader, "prepare_storage", prepare_storage)
    try:
        with TestClient(
            app,
            base_url="https://private.ts.net",
            headers={"Authorization": "Bearer " + token},
        ) as client:
            assert held.wait(5)
            # Before LOOP1 the loop itself waited, so startup only finished
            # once the holder gave up.
            assert holder.is_alive(), "startup waited for the household locks"
            time.sleep(0.3)  # The downloader is now waiting on lifecycle_lock.
            # A library page needs library_lock itself (LOCK1/SYNC1): it waits
            # in its worker thread without holding up anyone else.
            page = {}
            waiting = threading.Thread(
                target=lambda: page.update(answer=client.get("/api/v1/library/albums"))
            )
            waiting.start()
            time.sleep(0.1)
            slowest = {}
            for name, call in (
                ("health", lambda: client.get("/api/health")),
                ("address", lambda: client.post("/api/v1/playback/song/url")),
                ("health again", lambda: client.get("/api/health")),
            ):
                started = time.monotonic()
                answer = call()
                slowest[name] = time.monotonic() - started
                assert answer.status_code == 200, (name, answer.text)
            assert max(slowest.values()) < 0.2, slowest
            assert not app.state.downloader.recovered.is_set()
            assert waiting.is_alive()
            release.set()
            holder.join(5)
            waiting.join(10)
            assert page["answer"].status_code == 200
            assert app.state.downloader.recovered.wait(5)
    finally:
        release.set()
        if holder.is_alive():
            holder.join(5)


def test_owner_initialisation_runs_once_then_skips_its_lock(tmp_path, monkeypatch):
    library(tmp_path)
    app, owner = protected(tmp_path, monkeypatch)
    store = app.state.store
    assert store.user_libraries.contains(owner, "song")
    acquired = threading.Event()

    def hold():
        with store.library_lock:
            acquired.set()
            time.sleep(1)

    thread = threading.Thread(target=hold)
    thread.start()
    acquired.wait(5)
    started = time.monotonic()
    initialise_owner(store)
    assert store.user_libraries.contains(owner, "song")
    assert time.monotonic() - started < 0.5
    thread.join()


def test_owner_initialisation_waits_for_an_owner(tmp_path, monkeypatch):
    library(tmp_path)
    app, _ = protected(tmp_path, monkeypatch, owner=False)
    store = app.state.store
    initialise_owner(store)
    assert not getattr(store, "owner_initialised", False)
    owner = app.state.auth.create_owner("mark", PASSWORD)
    # Creating the owner migrates the household to them, once.
    assert store.owner_initialised
    assert store.user_libraries.contains(owner, "song")


def test_artist_submission_waits_for_lifecycle_lock_off_the_loop(tmp_path):
    from server.artist_jobs import ArtistJobs
    from server.store import Store
    from test_artist_jobs import A, Catalogue

    store = Store(tmp_path)
    jobs = ArtistJobs(store, Catalogue())
    jobs.configure(A, ["album"], False, True)

    async def run():
        acquired, release = threading.Event(), threading.Event()

        def hold():
            with store.lifecycle_lock:
                acquired.set()
                release.wait(10)

        holder = threading.Thread(target=hold)
        holder.start()
        acquired.wait(5)
        task = asyncio.create_task(jobs.acquire(None, A, jobs.state(A)))
        # submit_catalogue is waiting for the lock; the loop keeps ticking.
        started = time.monotonic()
        for _ in range(10):
            await asyncio.sleep(0.05)
        ticking = time.monotonic() - started
        assert not task.done()
        release.set()
        await task
        holder.join(5)
        return ticking

    assert asyncio.run(run()) < 1
    assert jobs.state(A)["status"] == "completed"
    assert len(store.queue()["items"]) == 1


def test_removing_an_artist_cancels_its_job_off_the_loop(tmp_path, monkeypatch):
    from server.artist_jobs import ArtistJobs
    from server.routes import catalogue
    from test_artist_jobs import A

    library(tmp_path)
    app = create_app(tmp_path)
    entered, release = threading.Event(), threading.Event()

    async def resolve(*args):
        return {"id": A}

    def cancel(self, identity):
        # The artist lock, held by a submission waiting on lifecycle_lock.
        entered.set()
        release.wait(10)

    monkeypatch.setattr(catalogue, "resolve_artist", resolve)
    monkeypatch.setattr(ArtistJobs, "cancel", cancel)
    with TestClient(app) as client:
        artist = app.state.store.library["artists"][0]["id"]
        removal = threading.Thread(
            target=lambda: client.post(f"/api/v1/admin/artists/{artist}/remove")
        )
        removal.start()
        try:
            assert entered.wait(5)
            started = time.monotonic()
            assert client.get("/api/health").status_code == 200
            assert time.monotonic() - started < 0.2
        finally:
            release.set()
            removal.join(10)


def test_periodic_check_never_revives_a_job_cancelled_meanwhile(tmp_path):
    from server.artist_jobs import ArtistJobs
    from server.store import Store
    from test_artist_jobs import A, Catalogue

    store = Store(tmp_path)
    jobs = ArtistJobs(store, Catalogue())
    jobs.configure(A, ["album"], True)
    jobs.save(jobs.key(A), {**jobs.state(A), "checked_at": 0})
    [(scoped, identity)] = jobs.pending()
    jobs.cancel(A)  # Between the listing and the check.
    assert scoped.due(identity) is None
    assert jobs.state(A)["status"] == "cancelled"
    assert jobs.state(A)["monitor"] is False


def test_one_failing_check_never_stops_the_artist_worker(tmp_path, monkeypatch):
    from server.artist_jobs import ArtistJobs
    from server.store import Store
    from test_artist_jobs import A, Catalogue

    store = Store(tmp_path)
    jobs = ArtistJobs(store, Catalogue())
    jobs.configure(A, ["album"], False, True)
    checked = threading.Event()

    def failing(self, identity):
        checked.set()
        raise ValueError("This artist already has a request running.")

    monkeypatch.setattr(ArtistJobs, "due", failing)

    async def run():
        task = asyncio.create_task(jobs.run(None))
        while not checked.is_set():
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        assert not task.done()  # Still waiting for its next pass.
        jobs.stopping.set()
        await task

    asyncio.run(run())
