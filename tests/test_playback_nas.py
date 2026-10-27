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


def test_a_second_open_looks_the_file_up_once(tmp_path, monkeypatch):
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
        assert resolved == [] and not any("song.mp3" in p for p in looked)
        for _ in range(3):
            answer = client.get(url, headers={"Range": "bytes=8-12"})
            assert answer.status_code == 206 and answer.content == b"audio"
    assert resolved == [1]
    # Resolving it on the first open, then one lookup per open.
    assert len([p for p in looked if p.endswith("song.mp3")]) == 1 + 3
