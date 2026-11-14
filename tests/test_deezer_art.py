"""Deezer first for catalogue covers and artist photos (#194)."""

import asyncio
import io
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from server.app import create_app
from server.artwork import Artwork
from server.deezer_art import DeezerArt, deezer_image
from server.portraits import Portraits

pytestmark = pytest.mark.deezer_art

RELEASE = "33333333-3333-4333-8333-333333333333"
GROUP = "22222222-2222-4222-8222-222222222222"
ARTIST = "11111111-1111-4111-8111-111111111111"
HASH = "1caa88583f24c9477fbb680cf7b1b8e8"
COVER = f"https://cdn-images.dzcdn.net/images/cover/{HASH}/1000x1000-000000-80-0-0.jpg"
PHOTO = f"https://cdn-images.dzcdn.net/images/artist/{HASH}/1000x1000-000000-80-0-0.jpg"
BLANK = "https://cdn-images.dzcdn.net/images/cover//1000x1000-000000-80-0-0.jpg"


def image_bytes():
    stream = io.BytesIO()
    Image.new("RGB", (60, 40), "blue").save(stream, "PNG")
    return stream.getvalue()


class Catalogue:
    """MusicBrainz answers by path; records what was asked."""

    def __init__(self, answers):
        self.answers, self.asked = answers, []

    async def get(self, client, path, **params):
        self.asked.append(path)
        return self.answers[path]


def deezer(routes, asked):
    """A transport answering api.deezer.com paths from `routes`."""

    def handle(request):
        asked.append(request.url.host + request.url.path)
        if request.url.host == "api.deezer.com":
            key = request.url.path.removeprefix("/")
            if key in routes:
                return httpx.Response(200, json=routes[key](request))
            return httpx.Response(200, json={"error": {"code": 800}})
        if request.url.host == "cdn-images.dzcdn.net":
            return httpx.Response(200, content=image_bytes())
        if request.url.host == "coverartarchive.org":
            return httpx.Response(200, content=image_bytes())
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


def run(coroutine):
    return asyncio.run(coroutine)


RELEASE_ENTITY = {
    "title": "Album",
    "barcode": "",
    "artist-credit": [{"name": "Artist"}],
    "relations": [],
}


def search(*albums):
    return lambda request: {"data": list(albums)}


def album(name="Artist", title="Album", cover=COVER):
    return {"title": title, "artist": {"name": name}, "cover_xl": cover}


def cover_of(entity, routes):
    asked = []
    art = DeezerArt(Catalogue({"release/" + RELEASE: entity}))

    async def ask():
        async with deezer(routes, asked) as client:
            return await art.cover(client, "release", RELEASE)

    return run(ask()), asked


def test_a_barcode_names_the_deezer_album_before_any_search():
    found, asked = cover_of(
        {**RELEASE_ENTITY, "barcode": "075679922298"},
        {"album/upc:075679922298": lambda r: {"title": "Other title", **album()}},
    )
    assert found == COVER
    assert asked == ["api.deezer.com/album/upc:075679922298"]


def test_a_musicbrainz_link_names_the_deezer_album():
    entity = {
        **RELEASE_ENTITY,
        "relations": [{"url": {"resource": "https://www.deezer.com/album/11244086"}}],
    }
    found, asked = cover_of(entity, {"album/11244086": lambda r: album()})
    assert found == COVER and asked == ["api.deezer.com/album/11244086"]


def test_search_refuses_another_artist_and_another_version():
    found, _ = cover_of(
        RELEASE_ENTITY,
        {
            "search/album": search(
                album(name="Artist Tribute Band"), album(title="Album (Live)")
            )
        },
    )
    assert found is None
    found, _ = cover_of(
        RELEASE_ENTITY,
        {"search/album": search(album(title="Album (Live)"), album())},
    )
    assert found == COVER


def test_deezers_blank_cover_is_no_cover():
    found, _ = cover_of(RELEASE_ENTITY, {"search/album": search(album(cover=BLANK))})
    assert found is None
    with pytest.raises(ValueError):
        deezer_image(BLANK)
    with pytest.raises(ValueError):
        deezer_image(COVER.replace("dzcdn.net", "dzcdn.net.example.org"))


def test_front_falls_back_to_the_archive_when_deezer_fails_or_has_nothing(tmp_path):
    class Store:
        cover_dir = tmp_path

    def front(routes):
        asked = []
        artwork = Artwork(Store(), Catalogue({"release/" + RELEASE: RELEASE_ENTITY}))

        async def ask():
            async with deezer(routes, asked) as client:
                return await artwork.front(client, "release", RELEASE)

        assert run(ask()) == image_bytes()
        return asked

    # Deezer refuses (quota): the archive answers.
    asked = front({"search/album": lambda r: {"error": {"code": 4}}})
    assert asked[-1] == f"coverartarchive.org/release/{RELEASE}/front-500"
    # Deezer has it: the archive is never asked.
    asked = front({"search/album": search(album())})
    assert not any(a.startswith("coverartarchive.org") for a in asked)
    assert asked[-1].startswith("cdn-images.dzcdn.net/images/cover/")


def photo_of(catalogue, routes, name="New Order", artist_id=ARTIST):
    asked = []
    art = DeezerArt(Catalogue(catalogue))

    async def ask():
        async with deezer(routes, asked) as client:
            return await art.photo(client, name, artist_id)

    return run(ask()), asked


NAMESAKES = {
    "search/artist": lambda r: {
        "data": [
            {"name": "New Order", "picture_xl": PHOTO},
            {"name": "New Order", "picture_xl": PHOTO},
        ]
    }
}


def test_an_artist_photo_comes_from_the_musicbrainz_link_not_a_namesake():
    linked = {
        "artist/"
        + ARTIST: {
            "name": "New Order",
            "relations": [
                {"url": {"resource": "https://www.deezer.com/artist/3998"}},
            ],
        }
    }
    found, asked = photo_of(
        linked, {**NAMESAKES, "artist/3998": lambda r: {"picture_xl": PHOTO}}
    )
    assert found == PHOTO and asked == ["api.deezer.com/artist/3998"]


def test_an_ambiguous_name_finds_no_photo_and_a_sole_one_does():
    unlinked = {"artist/" + ARTIST: {"name": "New Order", "relations": []}}
    assert photo_of(unlinked, NAMESAKES)[0] is None
    sole = {
        "search/artist": lambda r: {
            "data": [
                {"name": "New Order", "picture_xl": PHOTO},
                {"name": "New Order Tribute", "picture_xl": PHOTO},
            ]
        }
    }
    assert photo_of(unlinked, sole)[0] == PHOTO
    # A name that folds to nothing is never searched.
    assert photo_of({}, sole, name="!!!", artist_id=None) == (None, [])


def test_catalogue_cover_endpoint_uses_deezer_and_counts_the_source(tmp_path):
    asked = []

    def handle(request):
        asked.append(request.url.host + request.url.path)
        if request.url.host == "musicbrainz.org":
            return httpx.Response(200, json=RELEASE_ENTITY)
        if request.url.host == "api.deezer.com":
            return httpx.Response(200, json={"data": [album()]})
        if request.url.host == "cdn-images.dzcdn.net":
            return httpx.Response(200, content=image_bytes())
        return httpx.Response(500)

    app = create_app(tmp_path)
    with TestClient(app) as client:
        original = app.state.http
        app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        url = "/api/v1/catalogue/artwork/release/" + RELEASE
        assert client.get(url).status_code == 404
        client.portal.call(app.state.enrichment.queue.join)
        assert client.get(url).status_code == 200
        app.state.http = original
        report = app.state.store.performance.report()
    assert not any(a.startswith("coverartarchive.org") for a in asked)
    assert report["artwork"]["sources"] == {"cover": {"deezer": 1}}


def test_portrait_prefers_deezer_and_a_deezer_failure_is_retried_soon(tmp_path):
    from server.store import Store

    (tmp_path / "media-settings.json").write_text(
        json.dumps({"portraits": True}), encoding="utf-8"
    )
    store = Store(tmp_path)
    linked = {
        "artist/"
        + ARTIST: {
            "name": "New Order",
            "relations": [{"url": {"resource": "https://www.deezer.com/artist/3998"}}],
        }
    }
    portraits = Portraits(store, Catalogue(linked), Artwork(store, Catalogue(linked)))
    portraits.artwork.deezer.catalogue = Catalogue(linked)

    async def get(routes):
        async with deezer(routes, []) as client:
            return await portraits.get(client, "New Order", ARTIST)

    # Deezer refuses: no photo now, and the miss rests five minutes, not a day.
    assert run(get({"artist/3998": lambda r: {"error": {"code": 4}}})) is None
    with store.connect() as conn:
        misses = [
            json.loads(payload)["retry_after"]
            for (payload,) in conn.execute(
                "SELECT payload FROM documents WHERE name LIKE 'portrait-missing:%'"
            )
        ]
    import time

    assert misses and max(misses) < time.time() + 600
    with store.connect() as conn:
        conn.execute("DELETE FROM documents WHERE name LIKE 'portrait-missing:%'")
    found = run(get({"artist/3998": lambda r: {"picture_xl": PHOTO}}))
    assert found is not None and found.is_file()


def test_a_corrupt_deezer_image_falls_back_to_the_archive(tmp_path):
    """Codex round 1 (#194): bytes that are not an image must not block CAA."""

    class Store:
        cover_dir = tmp_path

    asked = []

    def handle(request):
        asked.append(request.url.host)
        if request.url.host == "api.deezer.com":
            return httpx.Response(200, json={"data": [album()]})
        if request.url.host == "cdn-images.dzcdn.net":
            return httpx.Response(200, content=b"not an image")
        return httpx.Response(200, content=image_bytes())

    artwork = Artwork(Store(), Catalogue({"release/" + RELEASE: RELEASE_ENTITY}))

    async def ask():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            return await artwork.front(client, "release", RELEASE)

    assert run(ask()) == image_bytes()
    assert asked[-1] == "coverartarchive.org"
