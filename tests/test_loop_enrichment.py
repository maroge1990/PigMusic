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
