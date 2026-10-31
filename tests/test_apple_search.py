"""Apple's search for music not on the server (D-61, #53), against a fake."""

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from server.apple_search import CALLS_PER_MINUTE, AppleSearch, apple_artwork
from test_personal_libraries import household

COVER = "https://is1-ssl.mzstatic.com/image/thumb/Music/x/cover.jpg/100x100bb.jpg"
SONGS = [
    {
        "wrapperType": "track",
        "kind": "song",
        "trackId": 11,
        "trackName": "With or Without You",
        "artistName": "U2",
        "collectionName": "The Joshua Tree",
        "collectionId": 21,
        "trackTimeMillis": 296000,
        "artworkUrl100": COVER,
    },
    {"wrapperType": "track", "kind": "podcast-episode", "trackId": 12},
    {
        "wrapperType": "track",
        "kind": "song",
        "trackId": 13,
        "trackName": "Song",
        "artistName": "Band",
        "collectionId": 22,
    },
]
ALBUMS = [
    {
        "wrapperType": "collection",
        "collectionId": 21,
        "collectionName": "The Joshua Tree",
        "artistName": "U2",
        "trackCount": 11,
        "releaseDate": "1987-03-09T08:00:00Z",
        "artworkUrl100": COVER,
    }
]


def fake_apple(seen, status=200):
    def answer(request):
        seen.append(request)
        if status != 200:
            return httpx.Response(status)
        entity = request.url.params.get("entity")
        if request.url.path == "/lookup":
            return httpx.Response(200, json={"results": ALBUMS})
        return httpx.Response(
            200, json={"results": SONGS if entity == "song" else ALBUMS}
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(answer))


def test_results_keep_apples_order_and_skip_music_on_the_server():
    seen = []
    apple = AppleSearch()

    async def run():
        async with fake_apple(seen) as client:
            first = await apple.search(
                client, "  u2   joshua ", "au", {("song", "band")}
            )
            again = await apple.search(client, "U2 Joshua", "au")
            return first, again

    first, again = asyncio.run(run())
    assert first["state"] == "ready"
    assert [s["apple_id"] for s in first["songs"]] == [11]  # Band's "Song" is here.
    assert first["songs"][0]["duration"] == 296
    assert first["albums"][0] == {
        "apple_id": 21,
        "title": "The Joshua Tree",
        "artist": "U2",
        "track_count": 11,
        "explicit": False,
        "year": "1987",
    }
    # Asked once per kind; the same search again comes from the cache.
    assert len(seen) == 2 and [s["apple_id"] for s in again["songs"]] == [11, 13]
    params = seen[0].url.params
    assert params["country"] == "au" and params["media"] == "music"
    assert params["term"] == "u2 joshua"


def test_past_apples_budget_answers_busy_and_failures_unavailable():
    now = [0.0]
    apple = AppleSearch(clock=lambda: now[0])

    async def run():
        async with fake_apple([]) as client:
            answers = [
                await apple.search(client, f"query {i}", "au")
                for i in range(CALLS_PER_MINUTE // 2 + 1)
            ]
            now[0] = 61
            answers.append(await apple.search(client, "later", "au"))
        async with fake_apple([], status=503) as client:
            answers.append(await apple.search(client, "broken", "au"))
        return answers

    answers = asyncio.run(run())
    assert [a["state"] for a in answers[:-3]] == ["ready"] * (CALLS_PER_MINUTE // 2)
    assert answers[-3]["state"] == "busy" and answers[-3]["songs"] == []
    assert answers[-2]["state"] == "ready"
    assert answers[-1] == {"songs": [], "albums": [], "state": "unavailable"}
    assert asyncio.run(AppleSearch().search(None, "a", "au"))["state"] == "ready"


def test_covers_come_only_from_apples_image_hosts():
    assert apple_artwork(COVER).endswith("/600x600bb.jpg")
    for bad in (
        "http://is1-ssl.mzstatic.com/a/100x100bb.jpg",
        "https://evil.example/a.jpg",
        "https://mzstatic.com.evil.example/a.jpg",
        "https://user@is1-ssl.mzstatic.com/a.jpg",
        None,
    ):
        with pytest.raises(ValueError):
            apple_artwork(bad)
    seen = []
    apple = AppleSearch()

    async def run():
        async with fake_apple(seen) as client:
            looked_up = await apple.cover_url(client, 21)
            remembered = await apple.cover_url(client, 21)
            return looked_up, remembered

    looked_up, remembered = asyncio.run(run())
    assert looked_up == remembered == apple_artwork(COVER)
    assert len(seen) == 1 and seen[0].url.path == "/lookup"


def test_online_search_and_resolve_routes(tmp_path, monkeypatch):
    app, owner, member, token, track = household(tmp_path, monkeypatch)
    tracks = [{**track, "id": "band", "title": "Song", "artist": "Band"}]
    (tmp_path / "library-snapshot.json").write_text(json.dumps({"tracks": tracks}))
    app.state.store.refresh_library()
    seen = []
    with TestClient(
        app,
        base_url="https://private.ts.net",
        headers={"Authorization": "Bearer " + token},
    ) as client:
        app.state.http = fake_apple(seen)
        found = client.get("/api/v1/search/online", params={"q": "joshua"})
        assert found.status_code == 200, found.text
        value = found.json()
        assert [s["apple_id"] for s in value["songs"]] == [11]
        assert value["albums"][0]["apple_id"] == 21
        assert "mzstatic" not in found.text  # Devices never reach Apple.

        matched = []

        async def match_entry(http, kind, entry):
            matched.append(entry)
            return {
                "id": "mb-recording",
                "name": entry["name"],
                "artist": entry["artistName"],
                "artist_id": "mb-artist",
                "kind": kind,
            }

        monkeypatch.setattr(app.state.home, "match_entry", match_entry)
        # Codex (high): a device's title and artist are never trusted, since
        # the match is cached for everyone (Home's chart included).
        body = {"kind": "song", "apple_id": 11, "title": "Wrong", "artist": "Other"}
        resolved = client.post("/api/v1/search/resolve", json=body)
        assert resolved.status_code == 200, resolved.text
        assert resolved.json()["id"] == "mb-recording"
        assert matched[-1] == {
            "id": 11,
            "name": "With or Without You",
            "artistName": "U2",
        }
        # An id search never returned is asked of Apple by id.
        lookups = len([r for r in seen if r.url.path == "/lookup"])
        album = client.post(
            "/api/v1/search/resolve", json={"kind": "album", "apple_id": 21}
        )
        assert album.status_code == 200, album.text
        # The search above already knew album 21: no lookup.
        assert len([r for r in seen if r.url.path == "/lookup"]) == lookups
        unknown = client.post(
            "/api/v1/search/resolve", json={"kind": "song", "apple_id": 999}
        )
        assert unknown.status_code == 404
        assert [r.url.params["id"] for r in seen if r.url.path == "/lookup"][
            -1
        ] == "999"
        assert len(matched) == 2
        assert (
            client.post(
                "/api/v1/search/resolve", json={**body, "kind": "artist"}
            ).status_code
            == 422
        )
        for bad in ("0", "abc", "1" * 16):
            assert (
                client.get("/api/v1/catalogue/artwork/apple-album/" + bad).status_code
                == 404
            )
    signed_out = TestClient(app, base_url="https://private.ts.net")
    assert signed_out.get("/api/v1/search/online", params={"q": "x"}).status_code == 401
