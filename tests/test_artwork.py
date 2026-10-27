import base64
import io
import json

import httpx
from fastapi.testclient import TestClient
from PIL import Image

from server.app import create_app
from server.store import Store
from server.artwork import artwork_year, artwork_title


def settled_get(client, app, url):
    first = client.get(url)
    client.portal.call(app.state.enrichment.queue.join)
    return client.get(url) if first.status_code == 404 else first


GROUP = "22222222-2222-4222-8222-222222222222"
RELEASE = "33333333-3333-4333-8333-333333333333"


def test_soundtrack_artwork_alias_preserves_version_and_recovers_folder_year():
    title = "Transformers: The Movie (Original Motion Picture Soundtrack)"
    assert artwork_title(title) == artwork_title("The Transformers: The Movie")
    assert artwork_title("Album (Live)") != artwork_title("Album")
    assert (
        artwork_year(
            {
                "title": title,
                "year": 0,
                "tracks": [
                    {
                        "relative_path": "Various Artists/The Transformers - The Movie (Original Motion Picture Soundtrack) (1986)/song.mp3"
                    }
                ],
            }
        )
        == "1986"
    )


def test_catalogue_cover_falls_back_only_to_same_release_group(tmp_path):
    calls = []

    def transport(request):
        calls.append(str(request.url))
        if request.url.host == "musicbrainz.org":
            assert "/release/" + RELEASE in request.url.path
            return httpx.Response(200, json={"release-group": {"id": GROUP}})
        if "/release/" + RELEASE in request.url.path:
            return httpx.Response(404)
        assert "/release-group/" + GROUP in request.url.path
        return httpx.Response(200, content=image_bytes())

    app = create_app(tmp_path)
    with TestClient(app) as client:
        original = app.state.http
        app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        url = "/api/v1/catalogue/artwork/release/" + RELEASE
        assert settled_get(client, app, url).status_code == 200
        assert settled_get(client, app, url).status_code == 200
        assert len(calls) == 3
        assert (
            client.get("/api/v1/catalogue/artwork/release/not-an-id").status_code == 400
        )
        app.state.http = original


def image_bytes():
    stream = io.BytesIO()
    Image.new("RGB", (800, 600), "red").save(stream, "PNG")
    return stream.getvalue()


def snapshot(directory, tracks=None, covers=None):
    (directory / "media-settings.json").write_text(
        json.dumps({"portraits": False}), encoding="utf-8"
    )
    payload = {
        "schema_version": 3,
        "tracks": tracks
        or [
            {
                "id": "song",
                "artist": "Example Artist",
                "title": "Song",
                "album_artist": "Example Artist",
                "album": "Greatest Hits",
            }
        ],
    }
    if covers:
        payload["schema_version"] = 2
        payload["covers"] = covers
    path = directory / "library-snapshot.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_library_artist_and_album_share_persistent_display_only_cover(tmp_path):
    original = snapshot(tmp_path).read_bytes()
    calls = []

    def transport(request):
        calls.append(request)
        if request.url.host == "musicbrainz.org":
            assert 'artistname:"Example Artist"' in request.url.params["query"]
            return httpx.Response(
                200,
                json={
                    "count": 1,
                    "release-groups": [
                        {
                            "id": GROUP,
                            "title": "Greatest Hits",
                            "artist-credit": [{"name": "Example Artist"}],
                        }
                    ],
                },
            )
        if request.url.host == "coverartarchive.org":
            return httpx.Response(
                307,
                headers={"location": "http://archive.org/download/example/front.png"},
            )
        assert request.url.host == "archive.org" and request.url.scheme == "https"
        return httpx.Response(200, content=image_bytes())

    app = create_app(tmp_path)
    with TestClient(app) as client:
        original_client = app.state.http
        app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        library = client.get("/api/v1/admin/library").json()
        result = settled_get(client, app, library["artists"][0]["cover_url"])
        assert (
            result.status_code == 200 and result.headers["content-type"] == "image/jpeg"
        )
        assert result.headers["cache-control"] == "public, max-age=31536000, immutable"
        with Image.open(io.BytesIO(result.content)) as image:
            assert image.size == (800, 600)
        assert client.get(library["albums"][0]["cover_url"]).content == result.content
        assert len(calls) == 3
        assert snapshot_path_bytes(tmp_path) == original
        assert client.get("/api/v1/admin/queue").json()["items"] == []
        app.state.http = original_client
    restarted = create_app(tmp_path)
    with TestClient(restarted) as client:
        library = client.get("/api/v1/admin/library").json()
        assert library["albums"][0]["cover_url"].startswith("/api/v1/covers/")
        assert client.get(library["albums"][0]["cover_url"]).status_code == 200


def snapshot_path_bytes(directory):
    return (directory / "library-snapshot.json").read_bytes()


def test_ambiguous_or_wrong_artist_does_not_fetch_cover(tmp_path):
    snapshot(tmp_path)
    calls = []

    def transport(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "count": 2,
                "release-groups": [
                    {
                        "id": GROUP,
                        "title": "Greatest Hits",
                        "artist-credit": [{"name": "Another Artist"}],
                    },
                    {
                        "id": GROUP,
                        "title": "Greatest Hits (Live)",
                        "artist-credit": [{"name": "Example Artist"}],
                    },
                ],
            },
        )

    app = create_app(tmp_path)
    with TestClient(app) as client:
        original_client = app.state.http
        app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        url = client.get("/api/v1/admin/library").json()["albums"][0]["cover_url"]
        assert settled_get(client, app, url).status_code == 404
        assert client.get(url).status_code == 404
        # One album search, one look-up of its song; then the miss is cached
        # rather than retried per tile.
        assert len(calls) == 2
        app.state.http = original_client


def test_untrusted_image_redirect_is_not_followed(tmp_path):
    snapshot(
        tmp_path,
        tracks=[
            {
                "id": "song",
                "artist": "Example Artist",
                "album": "Album",
                "title": "Song",
                "musicbrainz_release_id": GROUP,
            }
        ],
    )
    calls = []

    def transport(request):
        calls.append(request)
        return httpx.Response(307, headers={"location": "https://example.com/private"})

    app = create_app(tmp_path)
    with TestClient(app) as client:
        original_client = app.state.http
        app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        url = client.get("/api/v1/admin/library").json()["albums"][0]["cover_url"]
        assert settled_get(client, app, url).status_code == 404
        assert len(calls) == 1 and calls[0].url.host == "coverartarchive.org"
        assert not list((tmp_path / "covers").glob("*.jpg"))
        app.state.http = original_client


def test_artist_uses_another_embedded_album_before_remote_lookup(tmp_path):
    tracks = [
        {"id": "one", "artist": "Example Artist", "album": "First", "title": "One"},
        {"id": "two", "artist": "Example Artist", "album": "Second", "title": "Two"},
    ]
    snapshot(
        tmp_path,
        tracks=tracks,
        covers={
            "Example Artist\x1fSecond": {
                "data": base64.b64encode(image_bytes()).decode()
            }
        },
    )
    library = Store(tmp_path).library
    artist = library["artists"][0]
    second = next(a for a in library["albums"] if a["title"] == "Second")
    assert artist["cover_url"] == second["cover_url"]
    assert artist["cover_url"].startswith("/api/v1/covers/")


def test_artwork_year_uses_exact_album_folder_and_rejects_conflicting_hints():
    album = {
        "title": "Greatest Hits",
        "year": 0,
        "tracks": [{"relative_path": "Artist/Greatest Hits (1995)/Disc 1/01 Song.mp3"}],
    }
    assert artwork_year(album) == "1995"
    assert artwork_year({**album, "title": "Another Album"}) == ""
    assert artwork_year({**album, "year": 2009}) == "2009"
    album["tracks"].append({"relative_path": "Artist/Greatest Hits (1987)/Song.mp3"})
    assert artwork_year(album) == ""


def test_same_title_artwork_requires_unique_year_match(tmp_path):
    snapshot(
        tmp_path,
        tracks=[
            {
                "id": "song",
                "artist": "Example Artist",
                "title": "Song",
                "album": "Greatest Hits",
                "relative_path": "Example Artist/Greatest Hits (1995)/Song.mp3",
            }
        ],
    )
    calls = []

    def transport(request):
        calls.append(request)
        if request.url.host == "musicbrainz.org":
            return httpx.Response(
                200,
                json={
                    "count": 3,
                    "release-groups": [
                        {
                            "id": GROUP,
                            "title": "Greatest Hits",
                            "first-release-date": "1995-02-10",
                            "artist-credit": [{"name": "Example Artist"}],
                        },
                        {
                            "id": "33333333-3333-4333-8333-333333333333",
                            "title": "Greatest Hits",
                            "first-release-date": "1987",
                            "artist-credit": [{"name": "Example Artist"}],
                        },
                        {
                            "id": "44444444-4444-4444-8444-444444444444",
                            "title": "Greatest Hits",
                            "first-release-date": None,
                            "artist-credit": [{"name": "Example Artist"}],
                        },
                    ],
                },
            )
        assert (
            str(request.url)
            == f"https://coverartarchive.org/release-group/{GROUP}/front-500"
        )
        return httpx.Response(200, content=image_bytes())

    app = create_app(tmp_path)
    with TestClient(app) as client:
        original_client = app.state.http
        app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        url = client.get("/api/v1/admin/library").json()["albums"][0]["cover_url"]
        assert settled_get(client, app, url).status_code == 200
        assert len(calls) == 2
        app.state.http = original_client


def test_library_cover_falls_back_from_unscanned_edition_to_its_release_group(
    tmp_path,
):
    # Regression: tagged regional editions (e.g. a GB pressing) often have no
    # scan of their own while the release group does.
    snapshot(
        tmp_path,
        tracks=[
            {
                "id": "song",
                "artist": "Example Artist",
                "title": "Song",
                "album_artist": "Example Artist",
                "album": "Greatest Hits",
                "musicbrainz_release_id": RELEASE,
            }
        ],
    )
    calls = []

    def transport(request):
        calls.append(str(request.url))
        if request.url.host == "musicbrainz.org":
            assert "/release/" + RELEASE in request.url.path
            return httpx.Response(200, json={"release-group": {"id": GROUP}})
        if "/release/" + RELEASE in request.url.path:
            return httpx.Response(404)
        assert "/release-group/" + GROUP in request.url.path
        return httpx.Response(200, content=image_bytes())

    app = create_app(tmp_path)
    with TestClient(app) as client:
        original = app.state.http
        app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        library = client.get("/api/v1/admin/library").json()
        album = settled_get(client, app, library["albums"][0]["cover_url"])
        assert album.status_code == 200
        assert client.get(library["artists"][0]["cover_url"]).status_code == 200
        assert len(calls) == 3
        app.state.http = original


def test_a_cover_that_does_not_exist_is_asked_for_again_only_tomorrow(tmp_path):
    import time

    statuses = {GROUP: 404, RELEASE: 503}
    calls = []

    def transport(request):
        calls.append(request.url.path)
        for item, status in statuses.items():
            if item in request.url.path:
                return httpx.Response(status)
        return httpx.Response(500)

    app = create_app(tmp_path)
    with TestClient(app) as client:
        original = app.state.http
        app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        for kind, item in (("release-group", GROUP), ("release", RELEASE)):
            assert (
                settled_get(
                    client, app, f"/api/v1/catalogue/artwork/{kind}/{item}"
                ).status_code
                == 404
            )
        store = app.state.store
        missing = store.read_document("catalogue-artwork:release-group:" + GROUP)
        busy = store.read_document("catalogue-artwork:release:" + RELEASE)
        assert missing["retry_after"] - time.time() > 86000
        assert busy["retry_after"] - time.time() <= 300
        # A pass soon after asks again only for the one that was busy.
        asked = len(calls)
        settled_get(client, app, "/api/v1/catalogue/artwork/release-group/" + GROUP)
        assert len(calls) == asked
        app.state.http = original


def test_opening_a_discography_warms_its_first_covers(tmp_path, monkeypatch):
    from server.warmer import Warmer

    groups = [
        {"id": f"{index:08d}-2222-4222-8222-222222222222", "title": f"Album {index}"}
        for index in range(30)
    ]
    queued = []
    monkeypatch.setattr(Warmer, "covers", lambda self, items: queued.append(items))

    def transport(request):
        return httpx.Response(
            200, json={"release-group-count": 30, "release-groups": groups}
        )

    from test_security import PASSWORD, protected

    app, _ = protected(tmp_path, monkeypatch)
    token = app.state.auth.login("mark", PASSWORD, "device", "phone", "ios", "f")[
        "token"
    ]
    with TestClient(
        app,
        base_url="https://private.ts.net",
        headers={"Authorization": "Bearer " + token},
    ) as client:
        original = app.state.http
        app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        artist = "11111111-1111-4111-8111-111111111111"
        page = client.get(f"/api/v1/catalogue/artists/{artist}/releases")
        assert page.status_code == 200, page.text
        app.state.http = original
    assert queued == [[("release-group", g["id"]) for g in groups[:24]]]


def test_page_covers_wait_once_within_a_bound_and_only_while_warming():
    import asyncio

    from server import warmer as warming
    from server.warmer import Warmer

    warmer = Warmer(None, None, None)
    page = [("release-group", f"group-{index}") for index in range(24)]
    assert warmer.covers(page) == 0  # Warming is off: nothing waits.
    warmer.state["running"] = True
    # An artist page's work already pending never swallows its covers.
    warmer.pending["artist"] = (warming.SEARCH, True)
    assert warmer.covers(page) == 24
    assert warmer.covers(page) == 0  # Opened again: nothing new to wait for.
    for index in range(20):
        warmer.covers([("release-group", f"other-{index}-{n}") for n in range(24)])
    assert len(warmer.cover_pending) == warming.COVER_BACKLOG
    queued = sum(len(job[4]) for job in warmer.queue._queue)
    assert queued == warming.COVER_BACKLOG

    async def fetched():
        # Once fetched, a cover may be asked for again.
        done = []

        async def cover(kind, item_id):
            done.append(item_id)
            return True

        warmer.cover = cover
        task = asyncio.ensure_future(warmer.work())
        await warmer.queue.join()
        task.cancel()
        return done

    assert len(asyncio.run(fetched())) == warming.COVER_BACKLOG
    assert not warmer.cover_pending and warmer.covers(page[:1]) == 1
