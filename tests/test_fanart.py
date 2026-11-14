import asyncio

import httpx

from server.fanart import Fanart
from server.store import Store


def test_concurrent_portrait_and_background_share_metadata_but_key_rotation_refreshes(
    tmp_path,
):
    store = Store(tmp_path)
    cache = Fanart(store)
    calls = []

    async def handler(request):
        calls.append(request.headers["api-key"])
        await asyncio.sleep(0)
        return httpx.Response(200, json={"artistthumb": [], "artistbackground": []})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            first, second = await asyncio.gather(
                cache.get(client, "artist", "first-key"),
                cache.get(client, "artist", "first-key"),
            )
            assert first == second
            await Fanart(store).get(client, "artist", "first-key")
            assert calls == ["first-key"]
            await cache.get(client, "artist", "rotated-key")
            assert calls == ["first-key", "rotated-key"]

    asyncio.run(run())
    with store.connect() as conn:
        documents = str(conn.execute("SELECT * FROM documents").fetchall())
    assert "first-key" not in documents and "rotated-key" not in documents


def test_logo_keeps_transparency_and_refuses_untrusted_hosts(tmp_path):
    import io

    from PIL import Image

    from server.artist_logos import Logos
    from server.media import save_settings

    store = Store(tmp_path)
    save_settings(store, True, True, fanart_key="k" * 32)
    picture = io.BytesIO()
    Image.new("RGBA", (1000, 388), (255, 255, 255, 0)).save(picture, "PNG")
    artist = "6fe07aa5-fec0-4eca-a456-f29bff451b04"
    logos = {
        "hdmusiclogo": [
            {"url": "https://assets.fanart.tv/fr.png", "lang": "fr", "likes": "9"},
            {"url": "https://assets.fanart.tv/en.png", "lang": "en", "likes": "1"},
        ]
    }
    requested = []

    async def handler(request):
        requested.append(str(request.url))
        if request.url.host == "webservice.fanart.tv":
            return httpx.Response(200, json=logos)
        return httpx.Response(200, content=picture.getvalue())

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await Logos(store).get(client, artist)

    target = asyncio.run(run())
    assert requested[-1] == "https://assets.fanart.tv/en.png"
    with Image.open(target) as saved:
        assert saved.mode == "RGBA" and saved.width <= 800
        assert saved.getpixel((0, 0))[3] == 0

    other = "5b11f4ce-a62d-471e-81fc-a69a8278c7da"
    logos["hdmusiclogo"] = [{"url": "https://example.com/logo.png"}]

    async def untrusted():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await Logos(store).get(client, other)

    assert asyncio.run(untrusted()) is None
    assert "https://example.com/logo.png" not in requested


def test_album_images_cache_an_empty_answer(tmp_path):
    store = Store(tmp_path)
    calls = []

    async def handler(request):
        calls.append(request.url.path)
        return httpx.Response(404, json={"status": "error"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            first = await Fanart(store).album(client, "group", "key")
            second = await Fanart(store).album(client, "group", "key")
            return first, second

    assert asyncio.run(run()) == ({}, {})
    assert calls == ["/v3.2/music/albums/group"]


def test_every_fanart_request_shares_one_budget(tmp_path):
    """#170: portraits, logos, backgrounds and covers had their own semaphores
    (up to 12 Fanart requests at once); one shared budget now caps them."""
    import asyncio

    import httpx

    from server.fanart import Fanart, FANART_SLOTS
    from server.store import Store

    fanart = Fanart(Store(tmp_path))
    state = {"now": 0, "most": 0}

    async def handle(request):
        state["now"] += 1
        state["most"] = max(state["most"], state["now"])
        await asyncio.sleep(0.02)
        state["now"] -= 1
        if request.url.host == "assets.fanart.tv":
            return httpx.Response(200, content=b"image")
        return httpx.Response(200, json={"artistthumb": []})

    async def scenario():
        transport = httpx.MockTransport(handle)
        async with httpx.AsyncClient(transport=transport) as client:
            reads = [fanart.get(client, f"artist-{n}", "token") for n in range(8)]
            downloads = [
                fanart.download(client, f"https://assets.fanart.tv/{n}.jpg")
                for n in range(8)
            ]
            await asyncio.gather(*reads, *downloads)

    asyncio.run(scenario())
    assert state["most"] == FANART_SLOTS


def test_fanart_file_names_are_unchanged_by_the_shared_key_tag(tmp_path):
    """#170 refactor: cached logos, backgrounds and portraits keep their names."""
    import hashlib
    import json

    from server.artist_backgrounds import background_target
    from server.artist_logos import logo_target
    from server.library import identifier
    from server.portraits import portrait_target

    (tmp_path / "media-settings.json").write_text(
        json.dumps({"portraits": True, "fanart_key": "secret"}), encoding="utf-8"
    )
    store = Store(tmp_path)
    tag = hashlib.sha256(b"secret").hexdigest()[:12]
    artist = "11111111-1111-4111-8111-111111111111"
    assert logo_target(store, artist).stem == identifier("artist-logo", artist, tag)
    assert background_target(store, artist).stem == identifier(
        "artist-background", artist, tag
    )
    assert portrait_target(store, "Name", artist).stem == identifier(
        "portrait-v2", artist + tag
    )
