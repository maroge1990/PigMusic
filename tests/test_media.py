import asyncio
import io
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from server.app import create_app
from server.artwork import Artwork
from server.catalogue import Catalogue
from server.media import save_settings
from server.portraits import Portraits
from server.store import Store


def snapshot(path, relative="album/song.mp3"):
    track = {
        "id": "song",
        "title": "Song",
        "artist": "Artist",
        "album": "Album",
        "relative_path": relative,
    }
    (path / "library-snapshot.json").write_text(
        json.dumps({"tracks": [track]}), encoding="utf-8"
    )
    return track


def test_saved_audio_ranges_and_root_selection(tmp_path, monkeypatch):
    nas = tmp_path / "nas"
    (nas / "album").mkdir(parents=True)
    (nas / "album/song.mp3").write_bytes(b"NAS-audio-0123456789")
    monkeypatch.setenv("PIG_MUSIC_LIBRARY", str(nas))
    track = snapshot(tmp_path)
    app = create_app(tmp_path)
    owned = tmp_path / "downloads/library/album"
    owned.mkdir(parents=True)
    (owned / "song.mp3").write_bytes(b"OWN-audio-0123456789")
    with app.state.store.connect() as conn:
        conn.execute(
            "INSERT INTO acquired VALUES (?,?)",
            ("owned", json.dumps({**track, "id": "owned"})),
        )
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/playback/files/song", headers={"Range": "bytes=0-2"}
        )
        assert response.status_code == 206 and response.content == b"NAS"
        assert response.headers["content-range"] == "bytes 0-2/20"
        assert response.headers["content-type"] == "audio/mpeg"
        assert (
            client.get(
                "/api/v1/playback/files/owned", headers={"Range": "bytes=0-2"}
            ).content
            == b"OWN"
        )
        assert client.head("/api/v1/playback/files/song").content == b""
        assert (
            client.get(
                "/api/v1/playback/files/song", headers={"Range": "bytes=9999-"}
            ).status_code
            == 416
        )
        assert client.get("/api/v1/playback/files/unknown").status_code == 404


@pytest.mark.parametrize(
    "relative",
    [
        "../secret.mp3",
        "/secret.mp3",
        "C:/secret.mp3",
        "..\\secret.mp3",
        "album/../../secret.mp3",
    ],
)
def test_playback_rejects_snapshot_paths_outside_library(
    tmp_path, monkeypatch, relative
):
    nas = tmp_path / "nas"
    nas.mkdir()
    (tmp_path / "secret.mp3").write_bytes(b"private")
    monkeypatch.setenv("PIG_MUSIC_LIBRARY", str(nas))
    snapshot(tmp_path, relative)
    with TestClient(create_app(tmp_path)) as client:
        assert client.get("/api/v1/playback/files/song").status_code == 404


def test_settings_never_return_key_and_blank_preserves_it(tmp_path):
    key = "f" * 32
    with TestClient(create_app(tmp_path)) as client:
        body = {"portraits": True, "remote_playback": False, "fanart_key": key}
        response = client.put("/api/v1/admin/settings/media", json=body)
        assert response.status_code == 200 and key not in response.text
        assert client.get("/api/v1/admin/settings/media").json() == {
            "portraits": True,
            "remote_playback": False,
            "fanart_configured": True,
            "listenbrainz_configured": False,
            "chart_region": "au",
        }
        saved = client.put(
            "/api/v1/admin/settings/media", json={**body, "chart_region": "nz"}
        )
        assert saved.json()["chart_region"] == "nz"
        invalid = client.put(
            "/api/v1/admin/settings/media", json={**body, "chart_region": "n1"}
        )
        assert invalid.status_code == 400
        assert client.get("/api/v1/admin/settings/media").json()["chart_region"] == "nz"
        assert client.put(
            "/api/v1/admin/settings/media", json={**body, "fanart_key": None}
        ).json()["fanart_configured"]
        assert (
            client.put(
                "/api/v1/admin/settings/media", json={**body, "fanart_key": ""}
            ).json()["fanart_configured"]
            is False
        )


def test_fanart_uses_artist_id_private_header_and_persistent_sanitised_photo(tmp_path):
    store = Store(tmp_path)
    save_settings(store, True, False, "f" * 32)
    image = io.BytesIO()
    Image.new("RGB", (900, 900), "blue").save(image, "PNG")
    calls = []

    def transport(request):
        calls.append(request)
        assert "f" * 32 not in str(request.url)
        if request.url.host == "webservice.fanart.tv":
            assert request.headers["api-key"] == "f" * 32
            assert request.url.path.endswith("11111111-1111-4111-8111-111111111111")
            return httpx.Response(
                200,
                json={
                    "artistthumb": [
                        {"url": "https://assets.fanart.tv/photo.png", "likes": "2"}
                    ]
                },
            )
        assert "api-key" not in request.headers
        return httpx.Response(200, content=image.getvalue())

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            portraits = Portraits(
                store, Catalogue(store), Artwork(store, Catalogue(store))
            )
            target = await portraits.get(
                client, "Artist", "11111111-1111-4111-8111-111111111111"
            )
            assert target == await portraits.get(
                client, "Artist", "11111111-1111-4111-8111-111111111111"
            )
            with Image.open(target) as cached:
                assert cached.format == "JPEG" and cached.size == (900, 900)

    asyncio.run(run())
    assert len(calls) == 2
