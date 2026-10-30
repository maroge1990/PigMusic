"""LOOP1 (29 Oct live watchdog): metadata caches and artwork presentation never
hold up the event loop while SQLite or the cover store waits."""

import asyncio
import io
import os
import sqlite3
import threading
import time

import httpx
import pytest
from PIL import Image

from server.catalogue import Catalogue
from server.home import HomeDiscovery
from server.media import save_settings
from server.popularity import Popularity
from server.store import Store

pytestmark = pytest.mark.loop_timing

ARTIST = "70248960-cb53-4ea4-943a-edb18f7d336f"
TOKEN = "00000000-0000-4000-8000-000000000000"
SLOW = 0.4


async def longest_stall(operation):
    """Run `operation` while a ticker measures the longest loop stall."""
    stalls = [0.0]
    done = asyncio.Event()

    async def tick():
        last = time.monotonic()
        while not done.is_set():
            await asyncio.sleep(0.01)
            now = time.monotonic()
            stalls[0] = max(stalls[0], now - last)
            last = now

    ticker = asyncio.create_task(tick())
    await asyncio.sleep(0.02)
    try:
        result = await operation()
    finally:
        done.set()
        await ticker
    return stalls[0], result


def slow_documents(store, monkeypatch):
    """Every document read takes SLOW seconds, as behind a busy writer."""
    read, many = store.read_document, store.read_documents

    def read_document(name):
        time.sleep(SLOW)
        return read(name)

    def read_documents(names):
        time.sleep(SLOW)
        return many(names)

    monkeypatch.setattr(store, "read_document", read_document)
    monkeypatch.setattr(store, "read_documents", read_documents)


def hold_write_lock(store, seconds):
    """Another connection holds SQLite's write lock, as an export or scan does."""
    held = threading.Event()

    def hold():
        conn = sqlite3.connect(store.database, timeout=10)
        conn.execute("BEGIN IMMEDIATE")
        held.set()
        time.sleep(seconds)
        conn.rollback()
        conn.close()

    thread = threading.Thread(target=hold)
    thread.start()
    assert held.wait(5)
    return thread


def client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_catalogue_cache_reads_and_writes_leave_the_loop_free(tmp_path, monkeypatch):
    store = Store(tmp_path)
    catalogue = Catalogue(store)

    async def run():
        async with client(lambda r: httpx.Response(200, json={"id": "x"})) as http:
            # A miss writes its answer while another writer holds the database.
            holder = hold_write_lock(store, SLOW * 2)
            try:
                stall, data = await longest_stall(
                    lambda: catalogue.get(http, "artist/" + ARTIST)
                )
            finally:
                holder.join(5)
            assert data == {"id": "x"}
            assert stall < 0.2, stall
            # A cached answer read behind a slow database.
            slow_documents(store, monkeypatch)
            stall, data = await longest_stall(
                lambda: catalogue.get(http, "artist/" + ARTIST)
            )
            assert data == {"id": "x"}
            assert stall < 0.2, stall

    asyncio.run(run())


def test_popularity_cache_reads_and_writes_leave_the_loop_free(tmp_path, monkeypatch):
    store = Store(tmp_path)
    save_settings(store, False, True, listenbrainz_token=TOKEN)
    popularity = Popularity(store)
    popularity.min_interval = 0

    def transport(request):
        return httpx.Response(200, json=[])

    async def run():
        async with client(transport) as http:
            holder = hold_write_lock(store, SLOW * 2)
            try:
                stall, first = await longest_stall(
                    lambda: popularity.get(http, ARTIST, include_albums=False)
                )
            finally:
                holder.join(5)
            assert first["enabled"]
            assert stall < 0.2, stall
            slow_documents(store, monkeypatch)
            stall, again = await longest_stall(
                lambda: popularity.get(http, ARTIST, include_albums=False)
            )
            assert again == first
            assert stall < 0.2, stall

    asyncio.run(run())


def test_home_recommendations_read_identities_and_feeds_off_the_loop(
    tmp_path, monkeypatch
):
    store = Store(tmp_path)
    artists = [
        {"id": "a%02d" % n, "name": "Artist %d" % n, "track_count": n}
        for n in range(20)
    ]
    store.write_document("library-artist:a19", {"id": ARTIST, "name": "Artist 19"})
    slow_documents(store, monkeypatch)

    class NoCatalogue:
        async def get(self, http, path, **params):
            raise AssertionError("No catalogue request expected: " + path)

    async def no_resolution(*args):
        from server.artist_identity import Ambiguous

        raise Ambiguous()

    monkeypatch.setattr("server.home.resolve_artist", no_resolution)

    async def run():
        async with client(lambda r: httpx.Response(200, json={})) as http:
            return await longest_stall(
                lambda: HomeDiscovery(store, NoCatalogue()).recommended(
                    http, {"artists": artists}
                )
            )

    stall, result = asyncio.run(run())
    assert result["state"] == "ready"
    # Before LOOP1's fix each library artist cost a database read on the loop.
    assert stall < 0.2, stall


def test_cover_presentation_does_not_wait_for_a_scan_holding_the_cover_lock(
    tmp_path,
):
    store = Store(tmp_path)
    image = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 10, 10)).save(image, "JPEG")
    sha = store.covers.save(image.getvalue())
    store.covers.presentation_cache.clear()
    held, release = threading.Event(), threading.Event()

    def scan():
        # A scan or library refresh holds covers.lock throughout (1-2 s live).
        with store.covers.lock:
            held.set()
            release.wait(5)

    holder = threading.Thread(target=scan)
    holder.start()
    try:
        assert held.wait(5)
        answer = {}
        reader = threading.Thread(
            target=lambda: answer.update(value=store.covers.presentation(sha))
        )
        started = time.monotonic()
        reader.start()
        reader.join(1)
        assert not reader.is_alive(), "presentation waited for the scan"
        assert time.monotonic() - started < 0.2
        assert answer["value"]["sha"] == sha
    finally:
        release.set()
        holder.join(5)
        reader.join(5)


def test_resolving_a_new_artist_reads_and_saves_its_identity_off_the_loop(
    tmp_path, monkeypatch
):
    from server.artist_identity import cached_identity, resolve_artist

    store = Store(tmp_path)
    artist = {"id": "new", "name": "Unique Band", "track_count": 3, "album_ids": []}

    class OneMatch:
        async def get(self, http, path, **params):
            if path == "artist/":
                return {"count": 1, "artists": [{"id": ARTIST, "name": "Unique Band"}]}
            return {"count": 0, "release-groups": []}

    async def run():
        # The resolver's cache read is slow and its save meets another writer.
        read = store.read_document

        def slow_read(name):
            time.sleep(SLOW)
            return read(name)

        monkeypatch.setattr(store, "read_document", slow_read)
        holder = hold_write_lock(store, SLOW * 3)
        try:
            return await longest_stall(
                lambda: resolve_artist(store, OneMatch(), None, artist, [])
            )
        finally:
            holder.join(5)

    stall, identity = asyncio.run(run())
    assert identity["id"] == ARTIST
    assert cached_identity(store, artist)["id"] == ARTIST
    assert stall < 0.2, stall


def test_pruning_metadata_while_the_presentation_cache_evicts(tmp_path):
    """Codex round 2: prune removed cache entries without presentation_lock,
    so an eviction's chosen key could vanish before its pop (KeyError)."""
    store = Store(tmp_path)
    covers = store.covers
    image = io.BytesIO()
    Image.new("RGB", (8, 8), (10, 200, 10)).save(image, "JPEG")
    sha = covers.save(image.getvalue())
    stale = "a" * 64
    metadata = covers.directory / "metadata" / (stale + "-v1.json")
    metadata.write_text("{}")
    old = time.time() - 30 * 86400
    os.utime(metadata, (old, old))

    class Racing(dict):
        armed = True

        def __iter__(self):
            first = next(super().__iter__())
            if self.armed:
                self.armed = False
                # Prune runs between choosing the eviction key and popping it.
                self.pruning = threading.Thread(target=covers.prune, args=(set(),))
                self.pruning.start()
                self.pruning.join(0.3)
            return iter([first])

    cache = Racing({stale: {"sha": stale}})
    cache.update({"%064x" % n: {} for n in range(1, 8192)})
    covers.presentation_cache = cache
    assert covers.presentation(sha)["sha"] == sha
    cache.pruning.join(5)
    assert not metadata.exists()
    assert stale not in cache and sha in cache


def cancelled_mid_write(store, monkeypatch, call):
    """Cancel one call while its cache write is in progress, then make a second
    call. Return (second call's result, the value the cache ends with)."""
    write = store.write_document
    entered, release = threading.Event(), threading.Event()
    finished = []

    def write_document(key, value):
        if not entered.is_set():
            entered.set()
            release.wait(5)
        write(key, value)
        finished.append(value)

    monkeypatch.setattr(store, "write_document", write_document)

    async def run():
        first = asyncio.create_task(call())
        while not entered.is_set():
            await asyncio.sleep(0.01)
        first.cancel()
        second = asyncio.create_task(call())
        await asyncio.sleep(0.3)
        release.set()
        result = await asyncio.wait_for(second, 5)
        try:
            await first
        except asyncio.CancelledError:
            pass
        return result

    result = asyncio.run(run())
    return result, finished[-1]["data"]


def generations():
    count = [0]

    def handler(request):
        count[0] += 1
        return httpx.Response(200, json={"generation": count[0]})

    return handler


def test_a_cancelled_catalogue_write_never_overwrites_a_newer_answer(
    tmp_path, monkeypatch
):
    """Codex round 2: cancellation released Catalogue.turn before the write."""
    store = Store(tmp_path)
    catalogue = Catalogue(store)
    catalogue.min_interval = 0
    http = httpx.AsyncClient(transport=httpx.MockTransport(generations()))
    result, cached = cancelled_mid_write(
        store, monkeypatch, lambda: catalogue.get(http, "artist/" + ARTIST)
    )
    assert cached == result


def test_a_cancelled_home_feed_write_never_overwrites_a_newer_answer(
    tmp_path, monkeypatch
):
    store = Store(tmp_path)
    home = HomeDiscovery(store, None)
    http = httpx.AsyncClient(transport=httpx.MockTransport(generations()))
    (result, failed), cached = cancelled_mid_write(
        store, monkeypatch, lambda: home.feed(http, "lb-radio/artist/" + ARTIST)
    )
    assert not failed
    assert cached == result


def test_mix_planning_reads_artist_identities_and_the_day_off_the_loop(
    tmp_path, monkeypatch
):
    """#42: planning a person's mixes read the mix day's settings and every
    library artist's cached identity from SQLite on the event loop."""
    from test_security import protected

    app, _ = protected(tmp_path, monkeypatch)
    mixes = app.state.store.mixes
    store = mixes.store
    artists = [{"id": "a%d" % n, "name": "Artist %d" % n} for n in range(3)]
    for artist in artists:
        store.write_document(
            "library-artist:" + artist["id"], {"id": ARTIST, "name": artist["name"]}
        )
    slow_documents(store, monkeypatch)

    class NoRecordings:
        async def get(self, http, path, **params):
            return {"recordings": []}

    monkeypatch.setattr(mixes.home, "catalogue", NoRecordings())

    async def run():
        async with client(lambda r: httpx.Response(200, json={})) as http:
            return await longest_stall(
                lambda: mixes.releases(http, {"artists": artists})
            )

    stall, result = asyncio.run(run())
    assert result == []
    assert stall < 0.2, stall
