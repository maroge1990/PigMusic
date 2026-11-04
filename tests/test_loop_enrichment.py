"""#96 (event loop): artwork, portrait, backdrop, logo and Fanart caches read
and write SQLite off the event loop, where a busy writer cannot stall it."""

import asyncio
import json

import httpx

from server.artist_backgrounds import Backgrounds
from server.artist_logos import Logos
from server.artwork import Artwork
from server.fanart import Fanart
from server.portraits import Portraits
from server.store import Store

ARTIST = "70248960-cb53-4ea4-943a-edb18f7d336f"


def on_loop(store, monkeypatch):
    """Every database connection opened while an event loop runs in this
    thread, by its caller's name."""
    calls = []
    connect = store.connect

    def watched(*args, **kwargs):
        try:
            asyncio.get_running_loop()
            import inspect

            calls.append(inspect.stack()[1].function)
        except RuntimeError:
            pass
        return connect(*args, **kwargs)

    monkeypatch.setattr(store, "connect", watched)
    return calls


def client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_enrichment_caches_use_the_database_off_the_event_loop(tmp_path, monkeypatch):
    store = Store(tmp_path)
    (store.directory / "media-settings.json").write_text(
        json.dumps({"fanart_key": "key"})
    )
    artwork = Artwork(store, None)

    async def absent(*args):
        request = httpx.Request("GET", "https://coverartarchive.org/")
        raise httpx.HTTPStatusError(
            "missing", request=request, response=httpx.Response(404, request=request)
        )

    monkeypatch.setattr(artwork, "front", absent)

    class NoImages:
        async def get(self, client, artist_id, token):
            return {}

    calls = on_loop(store, monkeypatch)

    async def run():
        async with client(lambda request: httpx.Response(404)) as http:
            for _ in range(2):  # Remember the miss, then read it back.
                assert await artwork.catalogue_cover(http, "release", ARTIST) is None
                assert (
                    await Backgrounds(store, artwork, NoImages()).get(http, ARTIST)
                    is None
                )
                assert await Logos(store, NoImages()).get(http, ARTIST) is None
                assert await Fanart(store).get(http, ARTIST, "key") == {}
        csv = "name\nSomeone Else\n"
        async with client(lambda request: httpx.Response(200, text=csv)) as http:
            portraits = Portraits(store, None, artwork, NoImages())
            (store.directory / "media-settings.json").write_text("{}")
            for _ in range(2):
                assert await portraits.get(http, "Nobody", ARTIST) is None

    asyncio.run(run())
    assert store.read_document("artistgrid-index")["names"] == ["Someone Else"]
    assert calls == [], calls


def test_admin_file_checks_scans_and_artwork_stats_run_off_the_event_loop(
    tmp_path, monkeypatch
):
    """#96: Tidy's pre-checks read the whole queue, a scan request wrote its
    status, and artwork routes stat appdata, all on the event loop."""
    import pathlib

    from fastapi.testclient import TestClient

    from server.app import create_app

    app = create_app(tmp_path)
    store = app.state.store
    calls = on_loop(store, monkeypatch)
    queue, path = store.queue, store.covers.path

    def loop_name(name):
        try:
            asyncio.get_running_loop()
            calls.append(name)
        except RuntimeError:
            pass

    def watched_queue(*args, **kwargs):
        loop_name("queue")
        return queue(*args, **kwargs)

    def watched_path(sha):
        loop_name("covers.path")
        return path(sha)

    is_file = pathlib.Path.is_file

    def watched_is_file(self):
        if self.parent == store.cover_dir:
            loop_name("is_file")
        return is_file(self)

    monkeypatch.setattr(store, "queue", watched_queue)
    monkeypatch.setattr(store.covers, "path", watched_path)
    monkeypatch.setattr(pathlib.Path, "is_file", watched_is_file)
    with TestClient(app) as client:
        sha = "0" * 64
        calls.clear()  # Starting up reads on the loop; only requests count.
        client.post("/api/v1/admin/library/tidy/album", json={"fingerprint": "x"})
        client.post("/api/v1/admin/library/tidy/history/x/undo")
        assert client.get(f"/api/v1/covers/{sha}.jpg").status_code == 404
        assert client.get(f"/api/v1/covers/{sha}.jpg/metadata").status_code == 404
        assert client.get("/api/v1/covers/old.jpg").status_code == 404
        started = client.post("/api/v1/admin/library/scan")
        assert started.status_code == 200
        assert started.json()["status"] == "scanning"
        on_requests = list(calls)
    assert on_requests == [], on_requests
