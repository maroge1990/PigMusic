"""LOOP1: artist artwork routes, library identities, the Home chart and
personal preferences use SQLite off the event loop, so a busy database never
holds up every other request."""

import asyncio
import time
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest

from server.home import HomeDiscovery
from server.media import save_settings
from server.routes.catalogue import catalogue_router
from server.routes.discover import discover_router
from server.routes.personal import personal_router
from server.store import Store
from test_loop_store import (
    ARTIST,
    SLOW,
    client,
    hold_write_lock,
    longest_stall,
    slow_documents,
)
from test_native_sync import fixture

pytestmark = pytest.mark.loop_timing

FANART = "f" * 32


def endpoint(router, path, method="GET"):
    return next(
        route.endpoint
        for route in router.routes
        if route.path == path and method in route.methods
    )


class Stub:
    """An async provider whose every call answers `value`."""

    def __init__(self, value=None):
        self.value = value

    async def get(self, *args, **kwargs):
        return self.value


def artwork_routes(store, monkeypatch, submitted):
    save_settings(store, True, True, fanart_key=FANART)
    store.artists_by_id = {"a1": {"id": "a1", "name": "Band", "album_ids": []}}
    store.albums_by_id = {}

    async def resolved(*args):
        return {"id": ARTIST, "name": "Band"}

    monkeypatch.setattr("server.routes.catalogue.resolve_artist", resolved)

    async def image_response(target, w, soft=False):
        return target

    portrait = store.cover_dir / "portrait.jpg"
    router, library_artist_catalogue, _, _ = catalogue_router(
        store,
        Stub({"id": ARTIST, "name": "Band"}),
        None,
        SimpleNamespace(get=lambda *a, **k: Stub({}).get()),
        Stub(),
        Stub(portrait),
        None,
        SimpleNamespace(submit=lambda key, work: submitted.update({key: work})),
        image_response,
        lambda: "v1",
        lambda: None,
        logos=Stub(),
    )
    return router, library_artist_catalogue


def test_artist_artwork_routes_read_and_save_their_caches_off_the_loop(
    tmp_path, monkeypatch
):
    store = Store(tmp_path)
    submitted = {}
    router, _ = artwork_routes(store, monkeypatch, submitted)
    store.write_document("library-artist:a1", {"id": ARTIST, "name": "Band"})
    background = endpoint(router, "/api/v1/artists/{artist_id}/background")
    logo = endpoint(router, "/api/v1/artists/{artist_id}/logo")
    artwork = endpoint(router, "/api/v1/catalogue/artists/{artist_id}/artwork")

    async def run():
        stalls = {}
        # The portrait search's answer is saved while another writer holds
        # the database.
        await artwork(ARTIST, 0)
        [save] = submitted.values()
        holder = hold_write_lock(store, SLOW * 2)
        try:
            stalls["artwork save"], saved = await longest_stall(save)
        finally:
            holder.join(5)
        assert saved.name == "portrait.jpg"
        slow_documents(store, monkeypatch)
        for name, call in (
            ("background", lambda: background("a1", 0, False)),
            ("logo", lambda: logo("a1")),
            ("artwork", lambda: artwork(ARTIST, 0)),
            ("warm", lambda: router.warm_artist(ARTIST)),
        ):
            stalls[name], _ = await longest_stall(call)
        return stalls

    stalls = asyncio.run(run())
    assert store.read_document("catalogue-artist-image:" + ARTIST + ":v1") == {
        "file": "portrait.jpg"
    }
    assert max(stalls.values()) < 0.2, stalls


def test_library_artist_identity_is_checked_and_saved_off_the_loop(
    tmp_path, monkeypatch
):
    store = Store(tmp_path)
    _, library_artist_catalogue = artwork_routes(store, monkeypatch, {})
    key = "catalogue:artist/" + ARTIST + "[]"

    async def run():
        # The first visit saves the identity while another writer is busy.
        holder = hold_write_lock(store, SLOW * 2)
        try:
            saving, identity = await longest_stall(
                lambda: library_artist_catalogue("a1")
            )
        finally:
            holder.join(5)
        assert identity["id"] == ARTIST
        # Every later visit (and each warmer pass) reads it behind slow SQLite.
        slow_documents(store, monkeypatch)
        reading, _ = await longest_stall(lambda: library_artist_catalogue("a1"))
        return saving, reading

    saving, reading = asyncio.run(run())
    assert store.read_document(key)["data"]["id"] == ARTIST
    assert saving < 0.2, saving
    assert reading < 0.2, reading


def apple_charts():
    """Each fetch lists a new entry, so every build matches and saves it."""
    counts = {}

    def handler(request):
        kind = "songs" if "/songs." in request.url.path else "albums"
        counts[kind] = counts.get(kind, 0) + 1
        entry = {"id": kind + str(counts[kind]), "name": "Song", "artistName": "Solo"}
        return httpx.Response(200, json={"feed": {"results": [entry]}})

    return handler


def test_home_chart_reads_and_saves_off_the_loop(tmp_path, monkeypatch):
    store = Store(tmp_path)
    home = HomeDiscovery(store, Stub({}))

    async def run():
        stalls = {}
        async with client(apple_charts()) as http:
            # A build saves each entry's match and the chart while another
            # writer is busy.
            holder = hold_write_lock(store, SLOW * 3)
            try:
                stalls["build saves"], _ = await longest_stall(
                    lambda: home.run_chart(http, "au")
                )
            finally:
                holder.join(5)
            assert store.read_document("home-chart:v1:au")["data"]["state"] == "ready"
            slow_documents(store, monkeypatch)
            # /home/popular reads the fresh chart; a rebuild reads its caches.
            stalls["popular"], chart = await longest_stall(lambda: home.popular(http))
            assert chart["state"] == "ready"
            stalls["build reads"], _ = await longest_stall(
                lambda: home.run_chart(http, "au")
            )
        return stalls

    stalls = asyncio.run(run())
    for entry in ("songs:songs1", "albums:albums1", "songs:songs2"):
        assert (
            store.read_document("home-chart-match:v1:" + entry)["until"] > time.time()
        )
    assert max(stalls.values()) < 0.2, stalls


def slow_connections(store, monkeypatch):
    """Every connection waits SLOW seconds, as behind a busy writer."""
    connect = store.connect

    @contextmanager
    def slow_connect():
        time.sleep(SLOW)
        with connect() as conn:
            yield conn

    monkeypatch.setattr(store, "connect", slow_connect)


def test_play_history_and_preferences_use_sqlite_off_the_loop(tmp_path, monkeypatch):
    store, owner, _ = fixture(tmp_path)
    router = personal_router(store, None, None)
    history = endpoint(router, "/api/v1/play-events")
    preferences = endpoint(router, "/api/v1/preferences")
    save = endpoint(router, "/api/v1/preferences", "PUT")
    request = SimpleNamespace(state=SimpleNamespace(user={"id": owner}))
    model = save.__annotations__["body"]

    async def run():
        stalls = {}
        holder = hold_write_lock(store, SLOW * 2)
        try:
            stalls["save"], saved = await longest_stall(lambda: save(model(), request))
        finally:
            holder.join(5)
        slow_connections(store, monkeypatch)
        stalls["read"], read = await longest_stall(lambda: preferences(request))
        stalls["history"], played = await longest_stall(lambda: history(request))
        assert read == saved.model_dump()
        assert played == {"events": []}
        return stalls

    stalls = asyncio.run(run())
    assert max(stalls.values()) < 0.2, stalls


def test_linking_a_library_artist_saves_off_the_loop(tmp_path, monkeypatch):
    store = Store(tmp_path)
    artist = {"id": "a1", "name": "Band", "album_ids": []}
    monkeypatch.setattr(
        store.user_libraries,
        "projection",
        lambda user: {"artists": [artist], "albums": []},
    )
    router = discover_router(store, Stub({"id": ARTIST, "name": "Band"}))
    link = endpoint(router, "/api/v1/catalogue/library-artists/{artist_id}", "PUT")
    request = SimpleNamespace(
        state=SimpleNamespace(user={"id": "owner", "role": "owner"}),
        app=SimpleNamespace(state=SimpleNamespace(http=None)),
    )

    async def run():
        holder = hold_write_lock(store, SLOW * 2)
        try:
            return await longest_stall(
                lambda: link("a1", SimpleNamespace(id=ARTIST), request)
            )
        finally:
            holder.join(5)

    stall, identity = asyncio.run(run())
    assert identity == {"id": ARTIST, "name": "Band"}
    assert store.read_document("library-artist:a1") == identity
    assert stall < 0.2, stall
