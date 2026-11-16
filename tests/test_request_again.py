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


def test_a_finished_request_for_a_present_song_is_not_downloaded_again(
    tmp_path, monkeypatch
):
    # The song is still in the household (another member keeps it): asking
    # again puts it back in the person's library, downloads nothing, and
    # tells the phone the song is taken care of (not "excluded").
    app, client, owner, member = household(tmp_path, monkeypatch, member=True)
    with client:
        store = app.state.store
        claim = hold_downloads(store, monkeypatch)
        client.post("/api/v1/requests", json={"tracks": [song()]})
        download(store, claim)
        identity = store.user_libraries.projection(owner)["tracks"][0]["id"]
        store.user_libraries.add(member, [identity])
        removed = remove(client, [identity])
        assert removed["deleted"] == 0
        assert not store.user_libraries.contains(owner, identity)

        again = client.post("/api/v1/requests", json={"tracks": [song()]})
        assert again.status_code == 200, again.text
        body = again.json()
        assert body["created"] == 0 and body["existing"] == 1, body
        assert store.user_libraries.contains(owner, identity)
        assert statuses(store) == ["completed"]


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


def test_asking_for_the_song_you_removed_lifts_your_own_block(tmp_path, monkeypatch):
    # Mark's exact case (D-111): the same edition he removed, asked for again.
    # His removal's exclusion gives way to his own request; the song comes
    # back under a new id (the removal still hides the old one).
    app, client, owner, _ = household(tmp_path, monkeypatch)
    with client:
        store = app.state.store
        claim = hold_downloads(store, monkeypatch)
        client.post("/api/v1/requests", json={"tracks": [song()]})
        download(store, claim)
        old = store.user_libraries.projection(owner)["tracks"][0]["id"]
        remove(client, [old])

        # Monitoring (no person asking) still respects the block.
        automatic = store.submit_catalogue(
            [{**song(), "status": "missing"}], user_id=owner
        )
        assert automatic["skipped"] == 1 and statuses(store) == ["completed"]

        again = client.post("/api/v1/requests", json={"tracks": [song()]})
        assert again.json()["created"] == 1, again.json()
        groups = client.get("/api/v1/requests/groups").json()["groups"]
        # Only the new request shows: the finished one it replaced is hidden.
        assert [g["state"] for g in groups] == ["waiting"], groups
        download(store, claim)
        (back,) = store.user_libraries.projection(owner)["tracks"]
        assert back["id"] != old and back["title"] == "Choosin' Texas"
        # The block stays for automatic paths.
        with store.connect() as conn:
            assert conn.execute(
                "SELECT count(*) FROM documents WHERE name LIKE 'excluded:%'"
            ).fetchone() == (1,)


def test_a_block_someone_else_made_still_refuses(tmp_path, monkeypatch):
    # An administrator's removal (Recovery) is nobody's own to lift.
    from server.recovery import Recovery

    app, client, owner, member = household(tmp_path, monkeypatch, member=True)
    with client:
        store = app.state.store
        claim = hold_downloads(store, monkeypatch)
        client.post("/api/v1/requests", json={"tracks": [song()]})
        download(store, claim)
        identity = store.user_libraries.projection(owner)["tracks"][0]["id"]
        Recovery(store).remove(identity)
        refused = client.post("/api/v1/requests", json={"tracks": [song()]})
        assert refused.json()["skipped"] == 1, refused.json()
        assert statuses(store) == ["completed"]
        # The member's own request would lift only their own removal's block.
        assert store.user_libraries.request(member, [song()])["skipped"] == 1


def test_an_ordinary_job_in_the_chain_never_stands_for_an_edition(
    tmp_path, monkeypatch
):
    # Codex round 1: explicit edition A finished and removed, then the song
    # asked for ordinarily from release B (a new job after A's), then A
    # explicitly again: that must be a job for A, not B's ordinary one.
    # And an ordinary request afterwards may join either.
    app, client, owner, _ = household(tmp_path, monkeypatch)
    a = song(explicit_edition=True)
    b = song(musicbrainz_release_id="22222222-2222-4222-8222-222222222222")
    with client:
        store = app.state.store
        claim = hold_downloads(store, monkeypatch)
        client.post("/api/v1/requests", json={"tracks": [a]})
        download(store, claim)
        remove(client, [store.user_libraries.projection(owner)["tracks"][0]["id"]])
        assert (
            client.post("/api/v1/requests", json={"tracks": [b]}).json()["created"] == 1
        )
        explicit = client.post("/api/v1/requests", json={"tracks": [a]}).json()
        assert (explicit["created"], explicit["existing"]) == (1, 0), explicit
        waiting = [
            i for i in store.queue()["items"] if i["status"] == "waiting_for_sources"
        ]
        assert sorted(
            (bool(i.get("explicit_edition")), i["musicbrainz_release_id"])
            for i in waiting
        ) == [
            (False, b["musicbrainz_release_id"]),
            (True, a["musicbrainz_release_id"]),
        ]
        # Asked for again: each joins its own job.
        for track in (a, b):
            repeat = client.post("/api/v1/requests", json={"tracks": [track]})
            assert repeat.json()["existing"] == 1, repeat.json()
        assert len(statuses(store)) == 3


def test_cleared_history_never_brings_back_a_removed_id(tmp_path, monkeypatch):
    # Codex round 2: with the finished job cleared from the Server queue
    # (before or after the removal), and again for a song a re-download in
    # the chain brought back and that was removed in turn, the request must
    # still publish under a new id that the removal does not hide.
    app, client, owner, _ = household(tmp_path, monkeypatch)
    with client:
        store = app.state.store
        claim = hold_downloads(store, monkeypatch)
        seen = []

        def mine():
            (track,) = store.user_libraries.projection(owner)["tracks"]
            return track["id"]

        client.post("/api/v1/requests", json={"tracks": [song()]})
        download(store, claim)
        for clear_first in (True, False, True):
            seen.append(mine())
            if clear_first:
                store.clear_queue(finished_only=True)
            remove(client, [seen[-1]])
            if not clear_first:
                store.clear_queue(finished_only=True)
            assert statuses(store) == []
            again = client.post("/api/v1/requests", json={"tracks": [song()]})
            assert again.json()["created"] == 1, again.json()
            download(store, claim)
            assert mine() not in seen, seen
        assert len(set(seen)) == 3


def test_a_failed_request_asked_for_again_without_an_edition_is_queued(
    tmp_path, monkeypatch
):
    # Mark's device round, 17 Nov: two songs of a playlist whose earlier
    # requests (from an album, so with its release) had failed were asked for
    # again from the playlist, which names no release. The server answered
    # 500 (KeyError on the missing release) and the iPhone said ServerBusy.
    app, client, owner, _ = household(tmp_path, monkeypatch)
    with client:
        store = app.state.store
        hold_downloads(store, monkeypatch)
        first = client.post("/api/v1/requests", json={"tracks": [song()]})
        assert first.json()["created"] == 1, first.text
        with store.connect() as conn:
            conn.execute("UPDATE queue SET status='failed'")
        plain = song()
        del plain["musicbrainz_release_id"]
        again = client.post("/api/v1/requests", json={"tracks": [plain]})
        assert again.status_code == 200, again.text
        assert again.json()["created"] == 1, again.json()
        assert sorted(statuses(store)) == ["failed", "waiting_for_sources"]
        # Asked for once more: still one new job.
        repeat = client.post("/api/v1/requests", json={"tracks": [plain]})
        assert repeat.status_code == 200, repeat.text
        assert len(statuses(store)) == 2
