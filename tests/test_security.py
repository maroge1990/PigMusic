import contextlib
import re
from concurrent.futures import ThreadPoolExecutor
import json
import os
import stat
import threading

from fastapi.testclient import TestClient
import pytest

from server import app as server_app, media
from server.app import create_app
from server.auth import AuthError
from server.security import PUBLIC, api_routes, audit_routes, secret_file

PASSWORD = "security fixture password"


def protected(tmp_path, monkeypatch, owner=True):
    monkeypatch.delenv("PIG_MUSIC_TEST_LEGACY_API", raising=False)
    monkeypatch.setenv("PIG_MUSIC_LIBRARY", str(tmp_path / "nas"))
    app = create_app(tmp_path)
    if owner:
        identity = app.state.auth.create_owner("mark", PASSWORD)
    else:
        identity = None
    return app, identity


def request_paths(app):
    for route in api_routes(app):
        path = route.path
        for name in route.param_convertors:
            path = path.replace("{" + name + "}", "fixture")
        for method in route.methods:
            yield method, path


def test_every_api_route_is_anonymous_denied_and_bootstrap_closed(
    tmp_path, monkeypatch
):
    app, _ = protected(tmp_path, monkeypatch, owner=False)
    with TestClient(app, base_url="https://private.ts.net") as client:
        for method, path in request_paths(app):
            response = client.request(
                method, path, json={} if method not in {"GET", "HEAD"} else None
            )
            if path == "/api/health":
                assert response.status_code in {200, 405}
            elif path == "/api/v1/setup/owner":
                assert response.status_code == (200 if method == "GET" else 403)
            else:
                assert response.status_code == 503, (method, path, response.text)
    owner = app.state.auth.create_owner("mark", PASSWORD)
    with TestClient(app, base_url="https://private.ts.net") as client:
        for method, path in request_paths(app):
            if (method, path) in PUBLIC:
                continue
            response = client.request(
                method, path, json={} if method not in {"GET", "HEAD"} else None
            )
            assert response.status_code == 401, (method, path, response.text)
        health = client.get("/api/health").json()
        assert set(health) == {"status", "https", "storage_available"}


def test_member_cannot_see_unscoped_legacy_routes(tmp_path, monkeypatch):
    app, owner = protected(tmp_path, monkeypatch)
    setup = app.state.auth.setup_code(owner, "member", "Member")
    app.state.auth.activate(setup["code"], PASSWORD, "fixture")
    token = app.state.auth.login(
        "member", PASSWORD, "device", "phone", "ios", "fixture"
    )["token"]
    with TestClient(
        app,
        base_url="https://private.ts.net",
        headers={"Authorization": "Bearer " + token},
    ) as client:
        personal_paths = {
            route.path
            for route in api_routes(app)
            if "personal-library" in (getattr(route, "tags", None) or [])
        }
        for method, path in request_paths(app):
            if any(
                path == re.sub(r"\{[^}]+\}", "fixture", route.path)
                for route in api_routes(app)
                if route.path in personal_paths
                for name in route.param_convertors
            ):
                continue
            if path in personal_paths:
                continue
            if path.startswith(
                (
                    "/api/v1/playback/",
                    "/api/v1/playback/files/",
                    "/api/v1/covers/",
                    "/api/v1/library-artwork/",
                )
            ):
                assert client.request(method, path, json={}).status_code == 404
                continue
            if (method, path) in PUBLIC or path.startswith(
                ("/api/v1/me", "/api/v1/auth/logout")
            ):
                continue
            response = client.request(
                method, path, json={} if method not in {"GET", "HEAD"} else None
            )
            assert response.status_code == 403, (method, path, response.text)
        assert client.get("/api/v1/me").json()["username"] == "member"


def test_route_audit_detects_an_unprotected_api(tmp_path, monkeypatch):
    app, _ = protected(tmp_path, monkeypatch)
    route = next(
        r for r in app.routes if getattr(r, "path", "") == "/api/v1/admin/library"
    )
    route.dependant.dependencies = []
    with pytest.raises(RuntimeError, match="missing its default-deny guard"):
        audit_routes(app, app.state.security.current_user)


def test_signed_playback_ranges_tamper_expiry_and_revocation(tmp_path, monkeypatch):
    nas = tmp_path / "nas"
    nas.mkdir()
    (nas / "song.mp3").write_bytes(b"fixture audio bytes")
    (tmp_path / "library-snapshot.json").write_text(
        json.dumps(
            {
                "tracks": [
                    {
                        "id": "song",
                        "title": "Song",
                        "artist": "Artist",
                        "album": "Album",
                        "relative_path": "song.mp3",
                    }
                ]
            }
        )
    )
    app, _ = protected(tmp_path, monkeypatch)
    auth = app.state.auth
    clock = [1700000000]
    auth.clock = lambda: clock[0]
    token = auth.login("mark", PASSWORD, "device", "phone", "ios", "fixture")["token"]
    with TestClient(app, base_url="https://private.ts.net") as client:
        result = client.post(
            "/api/v1/playback/song/url", headers={"Authorization": "Bearer " + token}
        )
        assert result.status_code == 200, result.text
        url = result.json()["url"]
        response = client.get(url, headers={"Range": "bytes=0-6"})
        assert response.status_code == 206 and response.content == b"fixture"
        assert client.get(url.replace("/song?", "/other?")).status_code == 403
        assert client.get(url + "x").status_code == 403
        clock[0] += 6 * 3600
        assert client.get(url).status_code == 403
        clock[0] -= 6 * 3600
        _, session = auth.authenticate(token)
        auth.revoke(session["user_id"], session["public_id"])
        assert client.get(url).status_code == 403


def test_members_stream_household_files_without_adding_them(tmp_path, monkeypatch):
    nas = tmp_path / "nas"
    nas.mkdir()
    (nas / "song.mp3").write_bytes(b"fixture audio bytes")
    monkeypatch.setenv("PIG_MUSIC_LIBRARY", str(nas))
    song = {"id": "song", "title": "Song", "artist": "Artist", "album": "Album"}
    (tmp_path / "library-snapshot.json").write_text(
        json.dumps({"tracks": [{**song, "relative_path": "song.mp3"}]})
    )
    app, owner = protected(tmp_path, monkeypatch)
    setup = app.state.auth.setup_code(owner, "member", "Member")
    member = app.state.auth.activate(setup["code"], PASSWORD, "fixture")["id"]
    token = app.state.auth.login(
        "member", PASSWORD, "device", "phone", "ios", "fixture"
    )["token"]
    assert not app.state.store.user_libraries.contains(member, "song")
    with TestClient(
        app,
        base_url="https://private.ts.net",
        headers={"Authorization": "Bearer " + token},
    ) as client:
        result = client.post("/api/v1/playback/song/url")
        assert result.status_code == 200, result.text
        response = client.get(result.json()["url"], headers={"Range": "bytes=0-6"})
        assert response.status_code == 206 and response.content == b"fixture"
        assert client.post("/api/v1/playback/other/url").status_code == 404
    # Playing never adds the song to the member's library.
    assert not app.state.store.user_libraries.contains(member, "song")


def test_members_sign_a_page_of_playback_addresses_at_once(tmp_path, monkeypatch):
    nas = tmp_path / "nas"
    nas.mkdir()
    (nas / "song.mp3").write_bytes(b"fixture audio bytes")
    monkeypatch.setenv("PIG_MUSIC_LIBRARY", str(nas))
    song = {"id": "song", "title": "Song", "artist": "Artist", "album": "Album"}
    (tmp_path / "library-snapshot.json").write_text(
        json.dumps({"tracks": [{**song, "relative_path": "song.mp3"}]})
    )
    app, owner = protected(tmp_path, monkeypatch)
    setup = app.state.auth.setup_code(owner, "member", "Member")
    app.state.auth.activate(setup["code"], PASSWORD, "fixture")
    token = app.state.auth.login(
        "member", PASSWORD, "device", "phone", "ios", "fixture"
    )["token"]
    with TestClient(
        app,
        base_url="https://private.ts.net",
        headers={"Authorization": "Bearer " + token},
    ) as client:
        result = client.post(
            "/api/v1/playback-urls",
            json={"track_ids": ["song", "song", "missing", "mix:edition:0"]},
        )
        assert result.status_code == 200, result.text
        body = result.json()
        # Unknown songs and mix songs (signed per play) are left out.
        assert list(body["urls"]) == ["song"] and body["expires_in"] == 6 * 3600
        response = client.get(body["urls"]["song"], headers={"Range": "bytes=8-12"})
        assert response.status_code == 206 and response.content == b"audio"
        too_many = client.post(
            "/api/v1/playback-urls", json={"track_ids": ["song"] * 201}
        )
        assert too_many.status_code == 422


def test_a_page_of_playback_addresses_takes_one_database_query(tmp_path, monkeypatch):
    """200 songs: library songs come from the snapshot and downloaded ones
    from one query, not a connection per song."""
    monkeypatch.setenv("PIG_MUSIC_LIBRARY", str(tmp_path / "nas"))
    tracks = [
        {
            "id": f"song-{i}",
            "title": f"Song {i}",
            "artist": "Artist",
            "album": "Album",
            "relative_path": f"{i}.mp3",
        }
        for i in range(198)
    ]
    (tmp_path / "library-snapshot.json").write_text(json.dumps({"tracks": tracks}))
    app, owner = protected(tmp_path, monkeypatch)
    store = app.state.store
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO acquired VALUES (?,?)",
            (
                "downloaded",
                json.dumps(
                    {
                        "id": "downloaded",
                        "title": "Downloaded",
                        "artist": "Artist",
                        "album": "Other",
                        "relative_path": "d.mp3",
                    }
                ),
            ),
        )
    setup = app.state.auth.setup_code(owner, "member", "Member")
    app.state.auth.activate(setup["code"], PASSWORD, "fixture")
    token = app.state.auth.login(
        "member", PASSWORD, "device", "phone", "ios", "fixture"
    )["token"]
    wanted = [t["id"] for t in tracks] + ["downloaded", "missing"]
    with TestClient(
        app,
        base_url="https://private.ts.net",
        headers={"Authorization": "Bearer " + token},
    ) as client:
        lookups = []
        original = media.saved_track_ids

        def counted(store, ids):
            connect = store.connect
            caller = threading.get_ident()

            @contextlib.contextmanager
            def counting():
                # Only this lookup's queries: background workers (the
                # downloader's thread) use the same store meanwhile.
                if threading.get_ident() == caller:
                    lookups.append(1)
                with connect() as conn:
                    yield conn

            store.connect = counting
            try:
                return original(store, ids)
            finally:
                del store.connect

        monkeypatch.setattr(server_app, "saved_track_ids", counted)
        result = client.post("/api/v1/playback-urls", json={"track_ids": wanted})
        assert result.status_code == 200, result.text
        assert list(result.json()["urls"]) == wanted[:-1]
        assert lookups == [1]
        # A page of library songs needs no query at all.
        lookups.clear()
        result = client.post("/api/v1/playback-urls", json={"track_ids": wanted[:5]})
        assert list(result.json()["urls"]) == wanted[:5] and lookups == []


def test_cookie_mutations_require_header_and_https(tmp_path, monkeypatch):
    app, _ = protected(tmp_path, monkeypatch)
    with TestClient(
        app, base_url="https://private.ts.net", headers={"X-Pig-Request": "1"}
    ) as client:
        assert (
            client.post(
                "/api/v1/auth/login", json={"username": "mark", "password": PASSWORD}
            ).status_code
            == 200
        )
        client.headers.pop("X-Pig-Request")
        assert client.post("/api/v1/admin/queue/clear", json={}).status_code == 403
        assert (
            client.post(
                "/api/v1/admin/queue/clear", json={}, headers={"X-Pig-Request": "1"}
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/api/v1/admin/queue/clear",
                json={},
                headers={"X-Pig-Request": "1", "Origin": "https://evil.example"},
            ).status_code
            == 403
        )
        assert client.get("/api/v1/admin/library").status_code == 200


def test_secret_is_atomic_private_persistent_and_rejects_symlinks(tmp_path):
    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(lambda _: secret_file(tmp_path), range(8)))
    assert all(value == values[0] for value in values)
    assert len(values[0]) == 32
    assert stat.S_IMODE((tmp_path / "auth-secret").stat().st_mode) == 0o600
    (tmp_path / "linked").mkdir()
    (tmp_path / "linked/auth-secret").symlink_to(tmp_path / "auth-secret")
    with pytest.raises(OSError):
        secret_file(tmp_path / "linked")
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad/auth-secret").write_bytes(b"bad")
    with pytest.raises(ValueError):
        secret_file(tmp_path / "bad")


def test_audio_policy_allows_only_the_validated_remote_stream_host(
    tmp_path, monkeypatch
):
    app, _ = protected(tmp_path, monkeypatch)
    with TestClient(app, base_url="https://private.ts.net") as client:
        policy = client.get("/api/health").headers["content-security-policy"]
    media = next(p for p in policy.split(";") if p.strip().startswith("media-src"))
    # Remote listening returns only HTTPS *.saavncdn.com addresses.
    assert media.split() == ["media-src", "'self'", "blob:", "https://*.saavncdn.com"]


def test_members_can_stream_a_catalogue_song_by_its_recording(tmp_path, monkeypatch):
    from server.catalogue import Catalogue
    from server.remote import RemoteAudio

    release = "11111111-1111-1111-1111-111111111111"
    recording = "22222222-2222-2222-2222-222222222222"

    async def catalogue_get(self, client, path, **params):
        return {
            "id": release,
            "title": "Album",
            "artist-credit": [{"name": "Artist", "joinphrase": ""}],
            "media": [
                {
                    "position": 1,
                    "tracks": [
                        {
                            "id": "track-one",
                            "position": 1,
                            "title": "Song",
                            "recording": {"id": recording, "title": "Song"},
                        }
                    ],
                }
            ],
        }

    async def resolve(self, client, track):
        assert track["musicbrainz_recording_id"] == recording
        return "https://aac.saavncdn.com/song.mp4"

    monkeypatch.setattr(Catalogue, "get", catalogue_get)
    monkeypatch.setattr(RemoteAudio, "resolve", resolve)
    app, owner = protected(tmp_path, monkeypatch)
    code = app.state.auth.setup_code(owner, "member", "Member")
    app.state.auth.activate(code["code"], PASSWORD, "fixture")
    token = app.state.auth.login(
        "member", PASSWORD, "device", "phone", "ios", "fixture"
    )["token"]
    with TestClient(
        app,
        base_url="https://private.ts.net",
        headers={"Authorization": "Bearer " + token, "X-Pig-Request": "1"},
    ) as client:
        played = client.post(
            "/api/v1/remote-playback/catalogue",
            json={"release_id": release, "recording_id": recording},
        )
        assert played.status_code == 200
        assert played.json()["url"] == "https://aac.saavncdn.com/song.mp4"
        missing = client.post(
            "/api/v1/remote-playback/catalogue", json={"release_id": release}
        )
        assert missing.status_code == 422


def test_relayed_remote_audio_uses_a_signed_link_bound_to_its_listener(
    tmp_path, monkeypatch
):
    from server.remote import RemoteAudio

    async def no_direct_stream(self, client, track):
        raise ValueError("No unique remote recording matched this release.")

    def find(self, source, track):
        return {"source": source}

    def download(self, source, track, candidate):
        assert source == "zvu4no"
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / "fixture.mp3"
        path.write_bytes(b"relayed audio bytes")
        return path

    monkeypatch.setattr(RemoteAudio, "resolve", no_direct_stream)
    monkeypatch.setattr(RemoteAudio, "find", find)
    monkeypatch.setattr(RemoteAudio, "download", download)
    app, owner = protected(tmp_path, monkeypatch)
    code = app.state.auth.setup_code(owner, "member", "Member")
    app.state.auth.activate(code["code"], PASSWORD, "fixture")
    member = app.state.auth.login(
        "member", PASSWORD, "device", "phone", "ios", "fixture"
    )["token"]
    owner_token = app.state.auth.login(
        "mark", PASSWORD, "device", "phone", "ios", "fixture"
    )["token"]
    song = {"artist": "Artist", "title": "Song", "album": "Album", "duration": 3}
    with TestClient(app, base_url="https://private.ts.net") as client:
        played = client.post(
            "/api/v1/remote-playback",
            json=song,
            headers={"Authorization": "Bearer " + member},
        )
        assert played.status_code == 200, played.text
        assert played.json()["source"] == "Remote · zvu4no"
        url = played.json()["url"]
        assert url.startswith("/api/v1/remote-playback/streams/") and "sig=" in url
        response = client.get(url, headers={"Range": "bytes=0-6"})
        assert response.status_code == 206 and response.content == b"relayed"
        assert client.get(url + "x").status_code == 403
        bare = url.split("?")[0]
        assert client.get(bare).status_code == 401
        owner_view = client.get(
            bare, headers={"Authorization": "Bearer " + owner_token}
        )
        assert owner_view.status_code == 404
