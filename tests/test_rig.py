"""The T3 rig's fake services and the server's test-only switch (#15)."""

import asyncio
import threading
from http.server import ThreadingHTTPServer

import httpx
import pytest

from scripts import fake_services
from server import test_services
from server.media import save_settings
from server.popularity import Popularity
from server.store import Store

ARTIST = "70248960-cb53-4ea4-943a-edb18f7d336f"
TOKEN = "00000000-0000-4000-8000-000000000000"


@pytest.fixture
def fake():
    server = ThreadingHTTPServer(("127.0.0.1", 0), fake_services.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    fake_services.STATS.clear()
    yield "http://127.0.0.1:%d" % server.server_address[1]
    server.shutdown()


def test_the_switch_is_off_unless_the_rig_sets_it(monkeypatch):
    monkeypatch.delenv("PIG_MUSIC_TEST_SERVICES", raising=False)
    assert test_services.transport() is None


def test_the_rig_never_reaches_a_real_provider(fake):
    async def run():
        async with httpx.AsyncClient(
            transport=test_services.FakeServices(fake)
        ) as client:
            with pytest.raises(httpx.ConnectError):
                await client.get("https://example.com/anything")
            found = await client.get(
                "https://musicbrainz.org/ws/2/release-group/" + ARTIST
            )
            return found.json()

    detail = asyncio.run(run())
    assert detail["primary-type"] == "Album" and detail["id"] == ARTIST
    assert fake_services.STATS == {"musicbrainz.org ws/2/release-group": 1}


def test_popularity_runs_end_to_end_against_the_fake(tmp_path, fake):
    store = Store(tmp_path)
    save_settings(store, True, True, listenbrainz_token=TOKEN)

    async def run():
        async with httpx.AsyncClient(
            transport=test_services.FakeServices(fake)
        ) as client:
            return await Popularity(store).get(client, ARTIST)

    result = asyncio.run(run())
    assert len(result["songs"]) == 20 and len(result["albums"]) == 10
