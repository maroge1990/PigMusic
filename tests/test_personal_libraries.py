import json
import time

from fastapi.testclient import TestClient
import pytest

from server.playlists import Playlists
from test_playlists import export
from test_security import protected, PASSWORD


def household(tmp_path, monkeypatch):
    track = {
        "id": "song",
        "title": "One",
        "artist": "Artist",
        "album_artist": "Artist",
        "album": "Studio",
        "duration": 200,
        "relative_path": "private/song.mp3",
        "absolute_path": "/nas/private/song.mp3",
    }
    (tmp_path / "library-snapshot.json").write_text(json.dumps({"tracks": [track]}))
    app, owner = protected(tmp_path, monkeypatch)
    code = app.state.auth.setup_code(owner, "member", "Member")
    member = app.state.auth.activate(code["code"], PASSWORD, "fixture")
    with app.state.store.connect() as conn:
        member_id = conn.execute(
            "SELECT id FROM users WHERE username='member'"
        ).fetchone()[0]
    token = app.state.auth.login(
        "member", PASSWORD, "device", "phone", "ios", "fixture"
    )["token"]
    return app, owner, member_id, token, track


def test_private_imports_favourites_and_shared_membership(tmp_path, monkeypatch):
    app, owner, member, token, track = household(tmp_path, monkeypatch)
    store = app.state.store
    assert store.user_libraries.summary(owner)["stats"]["tracks"] == 1
    assert store.user_libraries.summary(member)["stats"]["tracks"] == 0
    store.user_libraries.import_playlists(owner, export())
    with TestClient(
        app,
        base_url="https://private.ts.net",
        headers={"Authorization": "Bearer " + token},
    ) as client:
        assert client.post("/api/v1/imports/apple", content=export()).status_code == 200
        personal = client.get("/api/v1/playlists").json()["playlists"]
        assert len(personal) == 1
        owner_playlist = Playlists(store, owner).list()[0]["id"]
        assert client.get("/api/v1/playlists/" + owner_playlist).status_code == 404
        assert (
            client.post(
                "/api/v1/playlists/" + owner_playlist + "/songs",
                json={
                    k: track[k]
                    for k in ("title", "artist", "album", "album_artist", "duration")
                },
            ).status_code
            == 404
        )
        assert store.queue()["items"] == []
        search = client.get("/api/v1/search", params={"q": "One"})
        assert search.json()["tracks"] == []
        assert search.json()["household_tracks"][0]["household_available"] is True
        assert "relative_path" not in search.text and "absolute_path" not in search.text
        result = client.post(
            "/api/v1/requests",
            json={
                "tracks": [
                    {
                        k: track[k]
                        for k in (
                            "title",
                            "artist",
                            "album",
                            "album_artist",
                            "duration",
                        )
                    }
                ]
            },
        )
        assert result.status_code == 200, result.text
        assert store.queue()["items"] == []
        assert client.get("/api/v1/library").json()["stats"]["tracks"] == 1
        search = client.get("/api/v1/search", params={"q": "One"}).json()
        assert len(search["tracks"]) == 1 and search["household_tracks"] == []
        response = client.get("/api/v1/playlists/" + personal[0]["id"])
        assert (
            "relative_path" not in response.text
            and "absolute_path" not in response.text
        )
        new = client.post("/api/v1/playlists", json={"name": "Private"}).json()
        response = client.post(
            "/api/v1/playlists/" + new["id"] + "/songs",
            json={
                k: track[k]
                for k in ("title", "artist", "album", "album_artist", "duration")
            },
        )
        assert response.status_code == 200 and "relative_path" not in response.text
        assert (
            client.post(
                "/api/v1/favourites",
                json={
                    "track": {"title": "One", "artist": "Artist", "album": "Studio"},
                    "favourite": True,
                },
            ).status_code
            == 200
        )
        assert Playlists(store, owner).favourite(track)["favourited"] is False


def test_shared_request_beneficiaries_and_atomic_history(tmp_path, monkeypatch):
    app, owner, member, token, track = household(tmp_path, monkeypatch)
    store = app.state.store
    missing = {
        **track,
        "title": "Missing",
        "album": "Other",
        "year": "",
        "disc_number": 1,
        "track_number": 1,
    }
    assert store.user_libraries.request(owner, [missing])["created"] == 1
    assert store.user_libraries.request(member, [missing])["existing"] == 1
    job = store.queue()["items"][0]
    assert set(job["requested_by"]) == {owner, member}
    assert "requested_by" not in store.user_libraries.queue(member)["items"][0]
    with store.connect() as conn:
        store._grant_request_membership(conn, json.dumps(job), "arrived")
    assert store.user_libraries.contains(member, "arrived")
    assert store.user_libraries.contains(owner, "arrived")
    store.user_libraries.add(member, ["song"])
    event = {
        "track_id": "song",
        "event_id": "event",
        "played_at": int(time.time()),
        "seconds": 10,
    }
    assert store.user_libraries.history(member, [event], "session")["recorded"] == 1
    assert store.user_libraries.history(member, [event], "session")["recorded"] == 0
    with pytest.raises(ValueError):
        store.user_libraries.history(
            member,
            [{**event, "event_id": "rollback"}, {**event, "track_id": "outside"}],
            "session",
        )
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM play_events").fetchone()[0] == 1


def test_owner_backfill_once_and_external_scan_stays_unassigned(tmp_path, monkeypatch):
    app, owner, member, _, track = household(tmp_path, monkeypatch)
    store = app.state.store
    baseline = store.user_libraries.projection(owner)["revision"]
    assert store.user_libraries.projection(owner)["revision"] == baseline
    added = {**track, "id": "external", "title": "External"}
    (tmp_path / "library-snapshot.json").write_text(
        json.dumps({"tracks": [track, added]})
    )
    store.refresh_library()
    assert not store.user_libraries.contains(owner, "external")
    assert not store.user_libraries.contains(member, "external")
    assert store.user_libraries.summary(owner)["stats"]["tracks"] == 1


def test_playlist_lifecycle_ownership_and_stale_edits(tmp_path, monkeypatch):
    app, owner, member, token, track = household(tmp_path, monkeypatch)
    foreign = Playlists(app.state.store, owner).create("Owner")
    with TestClient(
        app,
        base_url="https://private.ts.net",
        headers={"Authorization": "Bearer " + token},
    ) as client:
        own = client.post("/api/v1/playlists", json={"name": "Mine"}).json()
        path = "/api/v1/playlists/" + own["id"]
        for title in ("One", "Two"):
            own = client.post(
                path + "/songs",
                json={"title": title, "artist": "Artist", "album": "Studio"},
            ).json()
        assert own["entries"][0]["household_available"] is True
        assert own["entries"][0]["saved_track"] is None
        # The household file travels with the entry, so it plays from the server.
        household_track = own["entries"][0]["household_track"]
        assert household_track["server_state"] == "household_available"
        assert household_track["title"] == "One"
        assert "path" not in household_track
        assert own["entries"][1]["household_track"] is None
        revision = own["revision"]
        edited = client.patch(
            path, json={"revision": revision, "name": "Renamed", "order": [1, 0]}
        )
        assert edited.status_code == 200
        assert edited.json()["name"] == "Renamed"
        assert [t["title"] for t in edited.json()["entries"]] == ["Two", "One"]
        assert (
            client.delete(path + "/songs/0", params={"revision": revision}).status_code
            == 409
        )
        revision = edited.json()["revision"]
        assert (
            client.patch(path, json={"revision": revision, "order": [0, 0]}).status_code
            == 400
        )
        removed = client.delete(path + "/songs/0", params={"revision": revision}).json()
        assert [t["title"] for t in removed["entries"]] == ["One"]
        foreign_path = "/api/v1/playlists/" + foreign["id"]
        assert (
            client.patch(
                foreign_path, json={"revision": 0, "name": "Stolen"}
            ).status_code
            == 404
        )
        assert client.delete(foreign_path, params={"revision": 0}).status_code == 404
        assert (
            client.delete(foreign_path + "/songs/0", params={"revision": 0}).status_code
            == 404
        )
        assert client.delete(path, params={"revision": removed["revision"]}).json() == {
            "deleted": True
        }
        assert client.get(path).status_code == 404
        assert (
            Playlists(app.state.store, owner).detail(foreign["id"])["name"] == "Owner"
        )


def test_personal_artist_monitoring_cancellation_and_shared_download(
    tmp_path, monkeypatch
):
    import asyncio
    from server.artist_jobs import ArtistJobs
    from test_artist_jobs import A, Catalogue

    app, owner, member, token, _ = household(tmp_path, monkeypatch)
    root = ArtistJobs(app.state.store, Catalogue())
    owner_jobs, member_jobs = root.scoped(owner), root.scoped(member)
    owner_jobs.configure(A, ["album"], True, True)
    member_jobs.configure(A, ["single"], False, True)
    assert owner_jobs.state(A)["kinds"] == ["album"]
    assert member_jobs.state(A)["kinds"] == ["single"]
    member_jobs.cancel(A)
    assert owner_jobs.state(A)["status"] == "queued"
    member_jobs.configure(A, ["album"], False, True)
    asyncio.run(owner_jobs.acquire(None, A, owner_jobs.state(A)))
    asyncio.run(member_jobs.acquire(None, A, member_jobs.state(A)))
    queue = app.state.store.queue()["items"]
    assert len(queue) == 1 and set(queue[0]["requested_by"]) == {owner, member}
    with TestClient(
        app,
        base_url="https://private.ts.net",
        headers={"Authorization": "Bearer " + token},
    ) as client:
        path = "/api/v1/artists/" + A + "/acquisition"
        assert client.get(path).json()["monitor"] is False
        assert (
            client.put(path, json={"kinds": ["ep"], "monitor": True}).status_code == 200
        )
        assert client.delete(path).json()["monitor"] is False
        assert owner_jobs.state(A)["monitor"] is True
        assert client.get("/api/v1/artists/invalid/acquisition").status_code == 400
    member_jobs.configure(A, ["album"], True, True)
    with app.state.store.connect() as conn:
        conn.execute("UPDATE users SET disabled=1 WHERE id=?", (member,))
    assert member_jobs.current(A, member_jobs.state(A)["generation"]) is None


def test_member_artwork_is_limited_to_personal_library(tmp_path, monkeypatch):
    from io import BytesIO
    from PIL import Image

    app, owner, member, token, track = household(tmp_path, monkeypatch)
    store = app.state.store
    image = BytesIO()
    Image.new("RGB", (20, 20), "red").save(image, format="JPEG")
    sha = store.covers.save(image.getvalue())
    target = store.covers.path(sha)
    assert target
    # Attach the local master to the household album, then grant personal membership.
    album = store.library["albums"][0]
    album["cover_url"] = "/api/v1/covers/" + sha + ".jpg"
    with TestClient(
        app,
        base_url="https://private.ts.net",
        headers={"Authorization": "Bearer " + token},
    ) as client:
        # The downloader's startup recovery refreshes the library off the loop.
        assert app.state.downloader.recovered.wait(10)
        album = store.library["albums"][0]
        album["cover_url"] = "/api/v1/covers/" + sha + ".jpg"
        path = album["cover_url"]
        assert client.get(path).status_code == 404
        assert client.get(path + "/metadata").status_code == 404
        assert (
            client.get("/api/v1/library-artwork/albums/" + album["id"]).status_code
            == 404
        )
        store.user_libraries.add(member, [track["id"]])
        assert client.get(path).status_code == 200
        assert client.get(path + "/metadata").status_code == 200
        assert client.get(path + "?w=128").status_code == 200
        assert (
            client.get(
                path + "/metadata",
                headers={"If-None-Match": '"' + sha + '-metadata-v1"'},
            ).status_code
            == 304
        )


def test_concurrent_playlist_edits_and_reimport_reject_stale_revisions(
    tmp_path, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor
    from server.playlists import PlaylistConflict

    app, owner, member, _, _ = household(tmp_path, monkeypatch)
    playlists = Playlists(app.state.store, member)
    own = playlists.create("Before")

    def edit(name):
        try:
            playlists.edit(own["id"], 0, name=name)
            return "changed"
        except PlaylistConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(edit, ("First", "Second"))) == ["changed", "conflict"]
    app.state.store.user_libraries.import_playlists(member, export())
    imported = next(p for p in playlists.list() if p["id"] != own["id"])
    app.state.store.user_libraries.import_playlists(member, export())
    with pytest.raises(PlaylistConflict):
        playlists.edit(imported["id"], imported["revision"], name="Stale")


def test_legacy_artist_jobs_are_attributed_once_without_changing_original(
    tmp_path, monkeypatch
):
    from server.artist_jobs import ArtistJobs
    from test_artist_jobs import A, Catalogue

    app, _ = protected(tmp_path, monkeypatch, owner=False)
    jobs = ArtistJobs(app.state.store, Catalogue())
    original = jobs.configure(A, ["album"], True, True)
    owner = app.state.auth.create_owner("mark", PASSWORD)
    assert app.state.store.read_document("artist-job:" + A) == original
    assert jobs.state(A)["user_id"] == owner
    assert jobs.state(A)["generation"] == original["generation"]
    jobs.cancel(A)
    app.state.store.user_libraries.projection(owner)
    assert jobs.state(A)["status"] == "cancelled"
    assert app.state.store.read_document("artist-job:" + A) == original


def test_owner_attribution_verifier_preserves_legacy_state(tmp_path, monkeypatch):
    from scripts.verify_state_migration import rows, verify_owner_attribution
    from server.artist_jobs import ArtistJobs
    from test_artist_jobs import A, Catalogue

    app, _ = protected(tmp_path, monkeypatch, owner=False)
    store = app.state.store
    store.import_apple(export())
    Playlists(store).favourite({"title": "One", "artist": "Artist"}, True)
    ArtistJobs(store, Catalogue()).configure(A, ["album"], True, True)
    before = rows(tmp_path / "pig-music.sqlite3")
    owner = app.state.auth.create_owner("mark", PASSWORD)
    verify_owner_attribution(store, before, owner)


def test_simultaneous_household_requests_keep_both_beneficiaries(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    app, owner, member, _, track = household(tmp_path, monkeypatch)
    missing = {
        **track,
        "title": "New Song",
        "album": "New Album",
        "year": "",
        "disc_number": 1,
        "track_number": 1,
    }
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda user: app.state.store.user_libraries.request(user, [missing]),
                (owner, member),
            )
        )
    assert sum(r["created"] for r in results) == 1
    queue = app.state.store.queue()["items"]
    assert len(queue) == 1 and set(queue[0]["requested_by"]) == {owner, member}


def test_library_validators_are_bound_to_the_person(tmp_path, monkeypatch):
    # A shared browser revalidates its cached library with the previous
    # person's ETag; equal membership counters must not yield a 304.
    app, owner, member, token, track = household(tmp_path, monkeypatch)
    store = app.state.store
    store.user_libraries.add(member, [track["id"]])
    store.user_libraries.projection(owner)
    with store.connect() as conn:
        conn.execute("UPDATE membership_versions SET revision=7")
    assert (
        store.user_libraries.projection(owner)["revision"]
        != store.user_libraries.projection(member)["revision"]
    )
    owner_etag = '"' + store.user_libraries.summary(owner)["revision"] + '"'
    with TestClient(
        app,
        base_url="https://private.ts.net",
        headers={"Authorization": "Bearer " + token},
    ) as client:
        response = client.get("/api/v1/library", headers={"If-None-Match": owner_etag})
        assert response.status_code == 200
        assert response.json()["stats"]["tracks"] == 1


def test_recently_added_skips_bootstrap_attribution(tmp_path, monkeypatch):
    app, owner, member, token, track = household(tmp_path, monkeypatch)
    store = app.state.store
    store.user_libraries.projection(owner)
    assert store.user_libraries.recently_added(owner)["albums"] == []
    store.user_libraries.add(member, [track["id"]])
    with TestClient(
        app,
        base_url="https://private.ts.net",
        headers={"Authorization": "Bearer " + token},
    ) as client:
        recent = client.get("/api/v1/home/recent").json()
    assert [a["title"] for a in recent["albums"]] == ["Studio"]
    assert "tracks" not in recent["albums"][0]


def test_lookup_reports_only_songs_in_the_persons_own_library(tmp_path, monkeypatch):
    app, owner, member, token, track = household(tmp_path, monkeypatch)
    songs = [
        {"title": "one", "artist": "ARTIST"},
        {"title": "Missing", "artist": "Artist"},
    ]
    store = app.state.store
    found = store.user_libraries.lookup(owner, songs)
    assert found[0]["id"] == "song" and found[1] is None
    assert "relative_path" not in json.dumps(found)
    with TestClient(
        app,
        base_url="https://private.ts.net",
        headers={"Authorization": "Bearer " + token, "X-Pig-Request": "1"},
    ) as client:
        response = client.post("/api/v1/library/lookup", json={"songs": songs})
        # The household has the file, but the member has not added it.
        assert response.json() == {"tracks": [None, None]}


def test_playlist_size_limit_is_shared_by_web_and_favourites(tmp_path, monkeypatch):
    from server import playlists

    app, owner, member, token, track = household(tmp_path, monkeypatch)
    monkeypatch.setattr(playlists, "MAX_PLAYLIST_ENTRIES", 2)
    with TestClient(
        app,
        base_url="https://private.ts.net",
        headers={"Authorization": "Bearer " + token},
    ) as client:
        own = client.post("/api/v1/playlists", json={"name": "Mine"}).json()
        path = "/api/v1/playlists/" + own["id"] + "/songs?allow_repeat=true"
        song = {"title": "One", "artist": "Artist", "album": "Studio"}
        assert client.post(path, json=song).status_code == 200
        assert client.post(path, json=song).status_code == 200
        full = client.post(path, json=song)
        assert full.status_code == 409
        assert "10,000" in full.json()["detail"]
        for title in ("A", "B"):
            response = client.post(
                "/api/v1/favourites",
                json={"track": {"title": title, "artist": "X"}, "favourite": True},
            )
            assert response.status_code == 200
        response = client.post(
            "/api/v1/favourites",
            json={"track": {"title": "C", "artist": "X"}, "favourite": True},
        )
        assert response.status_code == 400


def test_server_info_is_for_signed_in_apps(tmp_path, monkeypatch):
    app, owner, member, token, track = household(tmp_path, monkeypatch)
    monkeypatch.setenv("PIG_MUSIC_REVISION", "0123456789abcdef0123")
    with TestClient(app, base_url="https://private.ts.net") as client:
        assert client.get("/api/v1/server").status_code == 401
        info = client.get(
            "/api/v1/server", headers={"Authorization": "Bearer " + token}
        ).json()
        assert info["revision"] == "0123456789ab"
        assert info["api_level"] >= 2
        assert "household_playlist_tracks" in info["capabilities"]
