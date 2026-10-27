"""NAS1: playback asks the NAS once per open, and never for an address."""

import json
import os

from fastapi.testclient import TestClient

from server import media
from test_security import PASSWORD, protected


def setup(tmp_path, monkeypatch):
    nas = tmp_path / "nas"
    (nas / "Artist").mkdir(parents=True)
    (nas / "Artist" / "song.mp3").write_bytes(b"fixture audio bytes")
    (tmp_path / "library-snapshot.json").write_text(
        json.dumps(
            {
                "tracks": [
                    {
                        "id": "song",
                        "title": "Song",
                        "artist": "Artist",
                        "album": "Album",
                        "relative_path": "Artist/song.mp3",
                    }
                ]
            }
        )
    )
    app, _ = protected(tmp_path, monkeypatch)
    token = app.state.auth.login("mark", PASSWORD, "device", "phone", "ios", "f")[
        "token"
    ]
    return app, token, nas / "Artist" / "song.mp3"


def test_an_address_comes_from_the_snapshot_and_a_deleted_file_answers_404(
    tmp_path, monkeypatch
):
    app, token, path = setup(tmp_path, monkeypatch)
    with TestClient(
        app,
        base_url="https://private.ts.net",
        headers={"Authorization": "Bearer " + token},
    ) as client:
        url = client.post("/api/v1/playback/song/url").json()["url"]
        opened = client.get(url, headers={"Range": "bytes=0-6"})
        assert opened.status_code == 206 and opened.content == b"fixture"
        path.unlink()
        # The address trusts the snapshot; opening finds the file gone, even
        # with its path remembered from the open a moment ago.
        url = client.post("/api/v1/playback/song/url").json()["url"]
        assert client.get(url).status_code == 404
        assert client.get(url, headers={"Range": "bytes=0-6"}).status_code == 404
        assert client.post("/api/v1/playback/other/url").status_code == 404


def test_a_file_replaced_by_a_symlink_is_refused(tmp_path, monkeypatch):
    app, token, path = setup(tmp_path, monkeypatch)
    secret = tmp_path / "outside.mp3"
    secret.write_bytes(b"not for playback")
    with TestClient(
        app,
        base_url="https://private.ts.net",
        headers={"Authorization": "Bearer " + token},
    ) as client:
        url = client.post("/api/v1/playback/song/url").json()["url"]
        assert client.get(url).status_code == 200  # Its path is remembered.
        path.unlink()
        os.symlink(secret, path)
        assert client.get(url).status_code == 404


def test_a_folder_replaced_by_a_symlink_outside_is_refused(tmp_path, monkeypatch):
    app, token, path = setup(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "song.mp3").write_bytes(b"not for playback")
    with TestClient(
        app,
        base_url="https://private.ts.net",
        headers={"Authorization": "Bearer " + token},
    ) as client:
        url = client.post("/api/v1/playback/song/url").json()["url"]
        assert client.get(url).status_code == 200
        path.parent.rename(path.parent.with_name("Moved"))
        os.symlink(outside, path.parent)
        assert client.get(url).status_code == 404
        assert client.get(url, headers={"Range": "bytes=0-6"}).status_code == 404
        assert client.head(url).status_code == 404


def test_opens_never_walk_the_path_to_check_it(tmp_path, monkeypatch):
    app, token, _ = setup(tmp_path, monkeypatch)
    resolved, looked = [], []
    track_file, lstat = media.track_file, os.lstat
    monkeypatch.setattr(
        media, "track_file", lambda *a: resolved.append(1) or track_file(*a)
    )
    monkeypatch.setattr(
        media.os, "lstat", lambda p, *a, **k: looked.append(str(p)) or lstat(p, *a, **k)
    )
    with TestClient(
        app,
        base_url="https://private.ts.net",
        headers={"Authorization": "Bearer " + token},
    ) as client:
        url = client.post("/api/v1/playback/song/url").json()["url"]
        for _ in range(3):
            answer = client.get(url, headers={"Range": "bytes=8-12"})
            assert answer.status_code == 206 and answer.content == b"audio"
        assert client.head(url).headers["content-length"] == "19"
        assert client.get(url).content == b"fixture audio bytes"
    assert resolved == []
    assert not [p for p in looked if p.endswith("song.mp3")]


def test_every_descriptor_is_closed(tmp_path, monkeypatch):
    app, token, path = setup(tmp_path, monkeypatch)
    opened, closed = [], []
    open_, close = os.open, os.close
    monkeypatch.setattr(
        media.os,
        "open",
        lambda *a, **k: (opened.append(fd := open_(*a, **k)), fd)[1],
    )
    monkeypatch.setattr(media.os, "close", lambda fd: (closed.append(fd), close(fd)))
    with TestClient(
        app,
        base_url="https://private.ts.net",
        headers={"Authorization": "Bearer " + token},
    ) as client:
        url = client.post("/api/v1/playback/song/url").json()["url"]
        client.get(url)
        client.head(url)
        client.get(url, headers={"Range": "bytes=0-1,4-5"})
        assert client.get(url, headers={"Range": "bytes=99-"}).status_code == 416
    assert opened and sorted(opened) == sorted(closed)


def test_a_cancelled_response_leaves_no_descriptor_open(tmp_path, monkeypatch):
    import anyio

    app, token, path = setup(tmp_path, monkeypatch)
    store = app.state.store
    before = set(os.listdir("/dev/fd"))

    async def cancelled(headers):
        fd, info, _ = media.open_playback(store, "song")
        response = media.DescriptorFileResponse(fd, info, media_type="audio/mpeg")
        scope = {"type": "http", "method": "GET", "headers": headers}

        sent = []

        async def send(message):
            sent.append(message["type"])

        async def receive():
            return {"type": "http.request"}

        # Cancelled once the headers are out, before the file is opened.
        with anyio.CancelScope() as scope_:
            scope_.cancel()
            await response(scope, receive, send)
        assert sent and "http.response.body" not in sent

    for headers in ([], [(b"range", b"bytes=0-3")]):
        anyio.run(cancelled, headers)
    assert set(os.listdir("/dev/fd")) <= before
