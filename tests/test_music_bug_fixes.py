import json

from fastapi.testclient import TestClient

from server.catalogue import Catalogue
from server.media import save_settings
from server.playlists import Playlists
from server.remote import RemoteAudio
from test_personal_libraries import household
from test_catalogue import ARTIST, RELEASE, OTHER, fixture_release, release_tracks


def client_for(app, token):
    return TestClient(
        app,
        base_url="https://private.ts.net",
        headers={"Authorization": "Bearer " + token},
    )


def test_album_request_preserves_year_and_edition(tmp_path, monkeypatch):
    app, _, _, token, _ = household(tmp_path, monkeypatch)
    track = release_tracks(fixture_release())[0]
    # The catalogue's numeric year was rejected before album requests used songBody.
    with client_for(app, token) as client:
        assert (
            client.post("/api/v1/requests", json={"tracks": [track]}).status_code == 422
        )
        track["year"] = str(track["year"])
        response = client.post("/api/v1/requests", json={"tracks": [track]})
        assert response.status_code == 200, response.text
        item = app.state.store.queue()["items"][0]
        assert item["year"] == "2020"
        assert item["musicbrainz_release_id"] == RELEASE
        assert item["album"] == "Live at Home"


def test_clear_downloads_preserves_active_shared_jobs_and_library(
    tmp_path, monkeypatch
):
    app, owner, member, token, track = household(tmp_path, monkeypatch)
    store = app.state.store
    with store.connect() as conn:
        for status in [
            "completed",
            "failed",
            "cancelled",
            "downloading",
            "importing",
            "waiting_for_sources",
        ]:
            conn.execute(
                "INSERT INTO queue(id,payload,status) VALUES (?,?,?)",
                (
                    status,
                    json.dumps({"title": status, "requested_by": [owner, member]}),
                    status,
                ),
            )
    before = store.user_libraries.summary(owner)["stats"]
    with client_for(app, token) as client:
        assert client.delete("/api/v1/requests").json() == {"cleared": 3}
        assert {i["id"] for i in client.get("/api/v1/requests").json()["items"]} == {
            "downloading",
            "importing",
            "waiting_for_sources",
        }
        assert client.delete("/api/v1/requests").json() == {"cleared": 0}
    assert store.user_libraries.queue(owner)["total"] == 6
    assert store.user_libraries.summary(owner)["stats"] == before
    assert len(store.queue()["items"]) == 6


def test_saved_artist_reuses_identity_and_scopes_access(tmp_path, monkeypatch):
    app, owner, member, token, track = household(tmp_path, monkeypatch)
    store = app.state.store
    artist = store.user_libraries.projection(owner)["artists"][0]
    store.write_document(
        "library-artist:" + artist["id"], {"id": ARTIST, "name": artist["name"]}
    )

    async def unexpected(*args, **kwargs):
        raise AssertionError("Known identity must not search names again")

    monkeypatch.setattr(Catalogue, "get", unexpected)
    with client_for(app, token) as client:
        path = "/api/v1/catalogue/library-artists/" + artist["id"]
        assert client.get(path).status_code == 404
        store.user_libraries.add(member, [track["id"]])
        assert client.get(path).json() == {"id": ARTIST, "name": artist["name"]}


def test_namesake_artist_resolved_by_owned_album_credits(tmp_path, monkeypatch):
    app, owner, member, token, track = household(tmp_path, monkeypatch)
    store = app.state.store
    store.user_libraries.add(member, [track["id"]])
    artist = store.user_libraries.projection(member)["artists"][0]

    async def metadata(self, client, path, **params):
        if path == "artist/":
            return {
                "count": 2,
                "artists": [
                    {"id": ARTIST, "name": "Artist"},
                    {"id": OTHER, "name": "Artist"},
                ],
            }
        assert path == "release-group/"
        return {
            "count": 1,
            "release-groups": [
                {
                    "title": "Studio",
                    "artist-credit": [{"artist": {"id": ARTIST, "name": "Artist"}}],
                }
            ],
        }

    monkeypatch.setattr(Catalogue, "get", metadata)
    with client_for(app, token) as client:
        assert client.get(
            "/api/v1/catalogue/library-artists/" + artist["id"]
        ).json() == {"id": ARTIST, "name": "Artist"}


def test_ambiguous_album_credits_still_require_choice(tmp_path, monkeypatch):
    app, _, member, token, track = household(tmp_path, monkeypatch)
    store = app.state.store
    store.user_libraries.add(member, [track["id"]])
    artist = store.user_libraries.projection(member)["artists"][0]

    async def metadata(self, client, path, **params):
        namesakes = [{"id": ARTIST, "name": "Artist"}, {"id": OTHER, "name": "Artist"}]
        return {
            "count": 2,
            "artists": namesakes,
            "release-groups": [
                {"title": "Studio", "artist-credit": [{"artist": a}]} for a in namesakes
            ],
        }

    monkeypatch.setattr(Catalogue, "get", metadata)
    with client_for(app, token) as client:
        assert (
            client.get("/api/v1/catalogue/library-artists/" + artist["id"]).status_code
            == 409
        )


def test_cloud_playlist_remote_metadata_does_not_download(tmp_path, monkeypatch):
    app, _, member, token, _ = household(tmp_path, monkeypatch)
    store = app.state.store
    (tmp_path / "media-settings.json").write_text(json.dumps({"remote_playback": True}))
    calls = []

    async def resolve(self, client, track):
        calls.append(track)
        return "https://example.saavncdn.com/song.mp3"

    monkeypatch.setattr(RemoteAudio, "resolve", resolve)
    track = {
        "title": "Cloud song",
        "artist": "Artist",
        "album": "Remote album",
        "duration": 201,
    }
    playlist = Playlists(store, member).create("Mixed")
    Playlists(store, member).add(playlist["id"], track)
    with client_for(app, token) as client:
        response = client.post("/api/v1/remote-playback", json=track)
        assert response.status_code == 200, response.text
        assert response.json()["url"].endswith("/song.mp3")
        assert calls[0]["album"] == "Remote album"
        assert store.queue()["items"] == []
        # Saved as the app saves it: overwriting the file in place raced start-up
        # recovery, which read it half-written (CI run 41036269583).
        save_settings(store, portraits=True, remote_playback=False)
        assert client.post("/api/v1/remote-playback", json=track).status_code == 409


def test_saved_release_credit_resolves_madonna_without_name_search(
    tmp_path, monkeypatch
):
    app, owner, member, token, track = household(tmp_path, monkeypatch)
    track.update(
        artist="Madonna",
        album_artist="Madonna",
        album="Like a Prayer",
        musicbrainz_release_id=RELEASE,
    )
    (tmp_path / "library-snapshot.json").write_text(json.dumps({"tracks": [track]}))
    store = app.state.store
    store.refresh_library()
    store.user_libraries.add(member, [track["id"]])
    artist = store.user_libraries.projection(member)["artists"][0]

    async def metadata(self, client, path, **params):
        assert path == "release/" + RELEASE
        assert params["inc"] == "artist-credits"
        return {
            "id": RELEASE,
            "artist-credit": [{"artist": {"id": ARTIST, "name": "Madonna"}}],
        }

    monkeypatch.setattr(Catalogue, "get", metadata)
    with client_for(app, token) as client:
        assert client.get(
            "/api/v1/catalogue/library-artists/" + artist["id"]
        ).json() == {"id": ARTIST, "name": "Madonna"}


def seed_requests(store, owner, member):
    statuses = [
        "waiting_for_sources",
        "searching",
        "downloading",
        "validating",
        "importing",
        "failed",
        "cancelled",
        "completed",
        "downloaded",
    ]
    with store.connect() as conn:
        for status in statuses:
            conn.execute(
                "INSERT INTO queue(id,payload,status) VALUES (?,?,?)",
                (
                    status,
                    json.dumps({"title": status, "requested_by": [member]}),
                    status,
                ),
            )
        conn.execute(
            "INSERT INTO queue(id,payload,status) VALUES (?,?,?)",
            ("theirs", json.dumps({"title": "x", "requested_by": [owner]}), "failed"),
        )
        conn.execute(
            "INSERT INTO queue(id,payload,status) VALUES (?,?,?)",
            ("theirs-active", json.dumps({"requested_by": [owner]}), "downloading"),
        )


def test_request_list_counts_only_own_active_requests(tmp_path, monkeypatch):
    app, owner, member, token, _ = household(tmp_path, monkeypatch)
    seed_requests(app.state.store, owner, member)
    with client_for(app, token) as client:
        body = client.get("/api/v1/requests", params={"limit": 1}).json()
    assert body["active"] == 5 and body["total"] == 9
    assert app.state.store.user_libraries.queue(owner)["active"] == 1


def test_member_retry_requeues_only_own_failed_or_cancelled(tmp_path, monkeypatch):
    app, owner, member, token, _ = household(tmp_path, monkeypatch)
    store = app.state.store
    seed_requests(store, owner, member)

    def status(key):
        with store.connect() as conn:
            return conn.execute(
                "SELECT status FROM queue WHERE id=?", (key,)
            ).fetchone()[0]

    with client_for(app, token) as client:
        for key in ("failed", "cancelled"):
            response = client.post(f"/api/v1/requests/{key}/retry")
            assert response.status_code == 200, response.text
            assert response.json() == {"retried": True}
            assert status(key) == "waiting_for_sources"
        for key in ("completed", "downloaded", "downloading", "waiting_for_sources"):
            response = client.post(f"/api/v1/requests/{key}/retry")
            assert response.status_code == 400
            assert "failed or cancelled" in response.json()["detail"]
        for key in ("theirs", "missing"):
            assert client.post(f"/api/v1/requests/{key}/retry").status_code == 404
        assert status("theirs") == "failed"
    with TestClient(app, base_url="https://private.ts.net") as anonymous:
        assert anonymous.post("/api/v1/requests/theirs/retry").status_code in {401, 403}
    assert status("theirs") == "failed"


def test_requested_album_appears_in_full_with_each_songs_state(tmp_path, monkeypatch):
    from server.native_sync import NativeSync

    app, owner, member, token, track = household(tmp_path, monkeypatch)
    store = app.state.store
    store.user_libraries.add(member, ["song"])
    studio = {"artist": "Artist", "album": "Studio", "album_artist": "Artist"}
    jobs = [
        ("two", "waiting_for_sources", {**studio, "title": "Two", "track_number": 2}),
        ("three", "failed", {**studio, "title": "Three", "track_number": 3}),
        (
            "upgrade",
            "downloading",
            {**studio, "title": "One", "library_track_id": "song"},
        ),
        ("done", "completed", {**studio, "title": "Old", "track_number": 4}),
        ("theirs", "downloading", {**studio, "title": "Five", "track_number": 5}),
        (
            "fresh",
            "downloading",
            {
                "title": "New",
                "artist": "Other",
                "album": "Fresh",
                "album_artist": "Other",
                "track_number": 1,
                "musicbrainz_release_id": RELEASE,
            },
        ),
    ]
    with store.connect() as conn:
        for request_id, status, payload in jobs:
            requesters = [owner] if request_id == "theirs" else [member]
            conn.execute(
                "INSERT INTO queue(id,payload,status) VALUES (?,?,?)",
                (
                    request_id,
                    json.dumps({**payload, "requested_by": requesters}),
                    status,
                ),
            )
    projection = store.user_libraries.projection(member)
    albums = {a["title"]: a for a in projection["albums"]}
    # Saved, requested and failed songs share the album, in order; quality
    # replacements, finished and other people's requests are not shown.
    assert [
        (t["title"], t.get("server_state")) for t in albums["Studio"]["tracks"]
    ] == [
        ("One", None),
        ("Two", "requested"),
        ("Three", "failed"),
    ]
    assert albums["Fresh"]["artist"] == "Other"
    assert albums["Fresh"]["tracks"][0]["id"] == "request-fresh"
    other = next(a for a in projection["artists"] if a["name"] == "Other")
    assert other["album_ids"] == [albums["Fresh"]["id"]]
    # Counts stay about saved music.
    assert projection["stats"]["tracks"] == 1
    assert projection["stats"]["albums"] == 1
    assert projection["stats"]["artists"] == 1
    synced = {t["id"]: t for t in NativeSync(store).page(member, "tracks")["items"]}
    assert synced["request-two"]["album_id"] == albums["Studio"]["id"]
    assert synced["request-two"]["server_state"] == "requested"
    with store.connect() as conn:
        conn.execute("UPDATE queue SET status='completed' WHERE id='two'")
    titles = [
        t["title"]
        for a in store.user_libraries.projection(member)["albums"]
        for t in a["tracks"]
    ]
    assert "Two" not in titles and "Three" in titles
