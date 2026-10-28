import asyncio
import json
import time

import httpx
import pytest

from server.catalogue import Catalogue, warming
from server.store import Store
from server.warmer import HOME, LIBRARY, SEARCH, Warmer

A = "11111111-1111-1111-1111-111111111111"
B = "22222222-2222-2222-2222-222222222222"
C = "33333333-3333-3333-3333-333333333333"
D = "44444444-4444-4444-4444-444444444444"


class Home:
    def __init__(self, feed):
        self.feed = feed

    async def popular(self, client):
        return self.feed

    async def recommended(self, client, projection):
        return {"artists": [], "albums": [], "songs": []}


def warmer(tmp_path, feed=None):
    store = Store(tmp_path)
    w = Warmer(store, Home(feed or {}), lambda: None)
    calls, covers = [], []

    async def artist(artist_id, full):
        calls.append((artist_id, full))
        return [("release-group", artist_id + "-album")] if full else []

    async def cover(kind, item_id):
        covers.append((kind, item_id))
        return True

    async def identity(library_id):
        return {"lib-a": A}.get(library_id)

    w.warm_artist, w.cover, w.library_identity = artist, cover, identity
    return w, calls, covers


async def drain(w):
    worker = asyncio.create_task(w.work())
    await w.queue.join()
    worker.cancel()


def test_search_prepares_two_artists_and_the_first_completely(tmp_path):
    w, calls, covers = warmer(tmp_path)

    async def run():
        w.search([A, B, C, D])
        await drain(w)

    asyncio.run(run())
    assert calls == [(A, True), (B, False)]
    assert ("release-group", A + "-album") in covers
    assert w.status()["artists_ready"] == 2


def test_recently_prepared_artists_are_not_fetched_again(tmp_path):
    w, calls, _ = warmer(tmp_path)

    async def run():
        w.submit(A, full=True, priority=LIBRARY)
        await drain(w)
        assert not w.submit(A, full=False, priority=SEARCH)
        assert not w.submit(A, full=True, priority=HOME)
        await drain(w)

    asyncio.run(run())
    assert calls == [(A, True)]


def test_urgent_requests_run_before_library_work(tmp_path):
    w, calls, _ = warmer(tmp_path)

    async def run():
        w.submit(A, full=True, priority=LIBRARY)
        w.submit(B, full=True, priority=LIBRARY)
        w.search([C])
        await drain(w)

    asyncio.run(run())
    assert calls[0] == (C, True)


def test_pass_covers_home_and_library_artists(tmp_path):
    feed = {
        "artists": [{"id": B, "artist_id": B}],
        "albums": [{"id": "group", "artist_id": C}],
        "songs": [{"id": "rec", "artist_id": D, "release_id": "release"}],
    }
    w, calls, covers = warmer(tmp_path, feed)
    w.store.library = {
        "artists": [
            {"id": "lib-a", "compilation_only": False},
            {"id": "lib-x", "compilation_only": True},
        ]
    }

    async def run():
        await w.run_pass()
        await drain(w)

    asyncio.run(run())
    assert calls[:3] == [(B, True), (C, True), (D, False)]
    assert (A, True) in calls
    assert ("release-group", "group") in covers and ("release", "release") in covers


def test_warming_renews_catalogue_answers_before_they_expire(tmp_path):
    store = Store(tmp_path)
    key = "catalogue:artist/" + A + "[]"
    store.write_document(key, {"time": time.time() - 22 * 3600, "data": {"n": 1}})
    requests = []

    async def handler(request):
        requests.append(request.url.path)
        return httpx.Response(200, json={"n": 2})

    async def read(renew):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            if renew:
                warming.set(True)
            return await Catalogue(store).get(c, "artist/" + A)

    assert asyncio.run(read(False)) == {"n": 1}
    assert requests == []
    assert asyncio.run(read(True)) == {"n": 2}
    assert json.loads(json.dumps(store.read_document(key)))["data"] == {"n": 2}


def test_home_albums_get_their_recommended_edition_prepared_once(tmp_path):
    w, _, covers = warmer(tmp_path)
    albums = []

    async def album(group_id):
        albums.append(group_id)
        return None if group_id == "empty" else group_id + "-edition"

    w.warm_album = album
    feed = {"albums": [{"id": "group"}, {"id": "empty"}]}

    async def run():
        assert w.prepare_albums(feed) == 2
        await drain(w)
        # Shown again within the reuse window: nothing is queued.
        assert w.prepare_albums(feed) == 0

    asyncio.run(run())
    assert albums == ["group", "empty"]
    assert covers == [("release", "group-edition")]


def test_each_pass_ends_by_showing_new_portraits(tmp_path, monkeypatch):
    from server import warmer as module

    w, calls, _ = warmer(tmp_path)
    w.store.library = {"artists": [{"id": "lib-a", "compilation_only": False}]}
    finished = []
    w.after_pass = lambda: finished.append(len(calls))
    real_sleep = asyncio.sleep

    async def sleep(seconds):
        if seconds == module.PASS_INTERVAL:
            raise asyncio.CancelledError
        await real_sleep(0)

    monkeypatch.setattr(module.asyncio, "sleep", sleep)

    async def run():
        worker = asyncio.create_task(w.work())
        try:
            await w.schedule()
        except asyncio.CancelledError:
            pass
        worker.cancel()

    asyncio.run(run())
    # Called once, after the library artist was prepared.
    assert finished == [1]


@pytest.mark.parametrize("page_size", [1, 240])
def test_replenished_page_covers_cannot_starve_home_or_library(tmp_path, page_size):
    w, _, _ = warmer(tmp_path)
    w.state["running"] = True
    fetched = 0
    served = {}

    async def artist(artist_id, full):
        served[artist_id] = fetched
        return []

    async def cover(kind, item_id):
        nonlocal fetched
        fetched += 1
        if fetched < 300:
            w.covers([("release-group", f"next-{fetched}")])
        await asyncio.sleep(0)  # Provider I/O lets join waiters run between jobs.
        return True

    w.warm_artist, w.cover = artist, cover

    async def run():
        w.covers([("release-group", f"initial-{i}") for i in range(page_size)])
        w.submit(A, priority=HOME)
        w.submit(B, priority=LIBRARY)
        worker = asyncio.create_task(w.work())
        try:
            await asyncio.wait_for(w.queue.join(), 3)
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    asyncio.run(run())
    # At most three urgent batches of at most 24 covers before background work.
    assert served[A] <= min(page_size, 24) * 3
    assert served[B] <= min(page_size, 24) * 6
    assert not w.cover_pending and not w.pending


def test_background_covers_do_not_clear_the_queued_page_cover_deduplication(tmp_path):
    w, _, _ = warmer(tmp_path)
    w.state["running"] = True
    home_started = False
    covers_after_home = 0
    duplicate_requests = []
    item = ("release", "shared-cover")

    async def artist(artist_id, full):
        nonlocal home_started
        if artist_id == D:
            home_started = True
        return []

    async def cover(kind, item_id):
        nonlocal covers_after_home
        if home_started:
            covers_after_home += 1
            if covers_after_home == 2:
                # The page batch is now being fetched after the Home job.
                duplicate_requests.append(w.covers([item]))
        return True

    w.warm_artist, w.cover = artist, cover

    async def run():
        for artist_id in (A, B, C):
            w.submit(artist_id, priority=SEARCH)
        w.covers([item])
        w.submit(D, priority=HOME, covers=[item])
        await drain(w)

    asyncio.run(run())
    assert duplicate_requests == [0]
    assert not w.cover_pending
