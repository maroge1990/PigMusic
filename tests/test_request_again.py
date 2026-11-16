"""A song asked for again after its request finished (#211).

Mark removed a song he had requested, the household's only copy, and asked
for it again: the new request joined the finished job and nothing was
queued. A finished job only stands for a song while its file is in the
household library.
"""

import hashlib
import json

from fastapi.testclient import TestClient

from server.app import create_app
from server.auth import Auth
from server.store import Store
from server.storage import library_root

PASSWORD = "request again fixture password"


def song(**changes):
    return {
        "artist": "Ella Langley",
        "album_artist": "Ella Langley",
        "album": "Choosin' Texas",
        "title": "Choosin' Texas",
        "year": "2025",
        "duration": 200,
        "disc_number": 1,
        "track_number": 1,
        "musicbrainz_release_id": "11111111-1111-4111-8111-111111111111",
        **changes,
    }


def household(tmp_path, monkeypatch, member=False):
    monkeypatch.delenv("PIG_MUSIC_TEST_LEGACY_API", raising=False)
    music = tmp_path / "music"
    music.mkdir()
    monkeypatch.setenv("PIG_MUSIC_LIBRARY", str(music))
    data = tmp_path / "data"
    store = Store(data)
    auth = Auth(store)
    owner = auth.create_owner("mark", PASSWORD)
    other = None
    if member:
        code = auth.setup_code(owner, "member", "Member")
        other = auth.activate(code["code"], PASSWORD, "fixture")["id"]
    token = auth.login("mark", PASSWORD, "device", "phone", "ios", "fixture")
    app = create_app(data)
    client = TestClient(
        app,
        base_url="https://private.ts.net",
        headers={"Authorization": "Bearer " + token["token"]},
    )
    return app, client, owner, other


def hold_downloads(store, monkeypatch):
    """Only the test claims jobs: the app's downloader finds none."""
    claim = store.claim_request
    monkeypatch.setattr(store, "claim_request", lambda: None)
    store.save_download_settings({"enabled": True, "sources": ["freemp3cloud"]})
    return claim


def download(store, claim):
    """The downloader's part: claim the waiting job and publish its file."""
    job = claim()
    assert job, "nothing was queued"
    store.request_state(job["id"], "validating", _claim_token=job["claim_token"])
    stage = store.directory / "downloads" / "staging"
    stage.mkdir(parents=True, exist_ok=True)
    source = stage / (job["id"] + ".mp3")
    source.write_bytes(b"music " + job["id"].encode())
    target = library_root(store) / "Ella Langley" / "Choosin' Texas.mp3"
    target.parent.mkdir(parents=True, exist_ok=True)
    store.publish_request(
        job,
        source,
        target,
        {"codec": "mp3", "bitrate_kbps": 320, "duration": 200},
        hashlib.sha256(source.read_bytes()).hexdigest(),
    )
    return job


def statuses(store):
    with store.connect() as conn:
        return [r[0] for r in conn.execute("SELECT status FROM queue")]


def library_ids(client):
    albums = client.get("/api/v1/library").json()["albums"]
    return [t["id"] for a in albums for t in a.get("tracks", [])]


def remove(client, ids):
    preview = client.post("/api/v1/library/removal-preview", json={"track_ids": ids})
    assert preview.status_code == 200, preview.text
    result = client.post(
        "/api/v1/library/remove",
        json={"track_ids": ids, "confirmation": preview.json()["confirmation"]},
    )
    assert result.status_code == 200, result.text
    return result.json()


def test_a_removed_song_asked_for_again_is_queued_again(tmp_path, monkeypatch):
    # Mark's case: he is the only member; removing the song moved its file to
    # Trash. Asked for again (here from the single's page, another release),
    # it must be queued and shown, not folded into the finished job.
    app, client, owner, _ = household(tmp_path, monkeypatch)
    with client:
        store = app.state.store
        claim = hold_downloads(store, monkeypatch)
        first = client.post("/api/v1/requests", json={"tracks": [song()]})
        assert first.status_code == 200, first.text
        assert first.json()["created"] == 1, first.text
        download(store, claim)
        assert statuses(store) == ["completed"]
        owned = store.user_libraries.projection(owner)["tracks"]
        assert len(owned) == 1
        removed = remove(client, [owned[0]["id"]])
        assert removed["deleted"] == 1 and not store.library["albums"]

        single = song(musicbrainz_release_id="22222222-2222-4222-8222-222222222222")
        again = client.post("/api/v1/requests", json={"tracks": [single]})
        assert again.status_code == 200, again.text
        assert again.json()["created"] == 1, again.json()
        # A new job; the finished one stays as history.
        assert sorted(statuses(store)) == ["completed", "waiting_for_sources"]
        groups = client.get("/api/v1/requests/groups").json()["groups"]
        assert groups[0]["state"] == "waiting", groups
        # The Server page lists it.
        waiting = [
            i for i in store.queue()["items"] if i["status"] == "waiting_for_sources"
        ]
        assert [i["requested_by"] for i in waiting] == [[owner]]
        # Asked for once more meanwhile: still one new job.
        repeat = client.post("/api/v1/requests", json={"tracks": [single]})
        assert repeat.json()["existing"] == 1, repeat.json()
        assert len(statuses(store)) == 2
        # The downloader takes it and the song is back in his library.
        download(store, claim)
        assert statuses(store) == ["completed", "completed"]
        assert len(store.user_libraries.projection(owner)["tracks"]) == 1


def test_monitoring_leaves_a_finished_request_alone(tmp_path, monkeypatch):
    # Without a person asking (artist monitoring), a finished job is never
    # queued again, even when its song has gone.
    app, client, owner, _ = household(tmp_path, monkeypatch)
    with client:
        store = app.state.store
        claim = hold_downloads(store, monkeypatch)
        client.post("/api/v1/requests", json={"tracks": [song()]})
        download(store, claim)
        identity = store.user_libraries.projection(owner)["tracks"][0]["id"]
        remove(client, [identity])
        result = store.submit_catalogue(
            [
                {
                    **song(
                        musicbrainz_release_id="22222222-2222-4222-8222-222222222222"
                    ),
                    "status": "missing",
                }
            ],
            user_id=owner,
        )
        assert result["created"] == 0 and result["existing"] == 1
        assert statuses(store) == ["completed"]


def test_an_edition_asked_for_again_twice_makes_one_job(tmp_path, monkeypatch):
    # "Only this edition will do" keys the job by its release: asked for
    # again after removal, and again, it still makes one new job.
    app, client, owner, _ = household(tmp_path, monkeypatch)
    edition = song(
        musicbrainz_release_id="33333333-3333-4333-8333-333333333333",
        explicit_edition=True,
    )
    with client:
        store = app.state.store
        claim = hold_downloads(store, monkeypatch)
        client.post("/api/v1/requests", json={"tracks": [edition]})
        download(store, claim)
        identity = store.user_libraries.projection(owner)["tracks"][0]["id"]
        remove(client, [identity])
        # Gone without a removal's exclusion (as a file deleted outside
        # PigMusic would be), so the same edition can be asked for.
        with store.connect() as conn:
            conn.execute("DELETE FROM documents WHERE name LIKE 'excluded:%'")
        counts = [
            client.post("/api/v1/requests", json={"tracks": [edition]}).json()
            for _ in range(2)
        ]
        assert [(c["created"], c["existing"]) for c in counts] == [(1, 0), (0, 1)]
        assert sorted(statuses(store)) == ["completed", "waiting_for_sources"]
