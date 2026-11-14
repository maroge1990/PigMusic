import hashlib, json
from server.store import Store
from server.lifecycle import coordinated


def test_clear_queue_invalidates_claim_and_preserves_music_and_playlists(tmp_path):
    store = Store(tmp_path / "data")
    song = {
        "id": "track",
        "artist": "Artist",
        "album_artist": "Artist",
        "album": "Album",
        "title": "Song",
        "year": 2020,
        "duration": 200,
        "disc_number": 1,
        "track_number": 1,
        "status": "missing",
    }
    store.save_download_settings({"enabled": True, "sources": ["freemp3cloud"]})
    store.submit_catalogue([song])
    old = store.claim_request()
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO playlists(id,payload) VALUES (?,?)",
            ("kept", json.dumps({"id": "kept", "name": "Playlist", "entries": []})),
        )
    store.clear_queue()
    assert (
        not store.request_active(old["id"], old["claim_token"])
        and not store.queue()["items"]
    )
    store.submit_catalogue([song])
    new = store.claim_request()
    assert new["id"] == old["id"] and new["claim_token"] != old["claim_token"]
    store.request_state(new["id"], "validating", _claim_token=new["claim_token"])
    store.request_state(old["id"], "failed", _claim_token=old["claim_token"])
    assert store.queue()["items"][0]["status"] == "validating"
    stage = store.directory / "downloads" / "staging"
    stage.mkdir(parents=True)
    source = stage / "song.mp3"
    source.write_bytes(b"validated")
    target = store.directory / "downloads" / "library" / "song.mp3"
    probe = {"codec": "mp3", "bitrate_kbps": 320, "duration": 200}
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    store.publish_request(old, source, target, probe, sha)
    assert not target.exists() and source.exists()
    store.publish_request(new, source, target, probe, sha)
    assert target.read_bytes() == b"validated"
    store.clear_queue()
    assert target.exists() and store.library["stats"]["tracks"] == 1
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM playlists").fetchone()[0] == 1


def test_clear_finished_keeps_unfinished_requests(tmp_path):
    store = Store(tmp_path / "data")
    store.save_download_settings({"enabled": True, "sources": ["freemp3cloud"]})
    songs = [
        {
            "id": f"t{i}",
            "artist": "Artist",
            "album_artist": "Artist",
            "album": "Album",
            "title": f"Song {i}",
            "year": 2020,
            "duration": 200,
            "disc_number": 1,
            "track_number": i,
            "status": "missing",
        }
        for i in (1, 2)
    ]
    store.submit_catalogue(songs)
    first = store.claim_request()
    store.request_state(first["id"], "failed", _claim_token=first["claim_token"])
    left = store.clear_queue(finished_only=True)["items"]
    # The failed request is cleared; the one still waiting stays.
    assert [i["title"] for i in left] == [
        t["title"] for t in songs if t["title"] != first["title"]
    ]


def titles(store):
    return [t["title"] for t in store.index.by_id.values()]


def published(tmp_path, monkeypatch):
    """A store with one claimed download ready to publish."""
    monkeypatch.setenv("PIG_MUSIC_LIBRARY", str(tmp_path / "nas"))
    store = Store(tmp_path / "data")
    song = {
        "id": "wanted",
        "title": "Song",
        "duration": 200,
        "artist": "Artist",
        "album": "Album",
        "album_artist": "Artist",
        "year": "2020",
        "disc_number": 1,
        "track_number": 1,
        "status": "missing",
    }
    store.save_download_settings({"enabled": True, "sources": ["freemp3cloud"]})
    store.submit_catalogue([song])
    job = store.claim_request()
    store.request_state(job["id"], "validating", _claim_token=job["claim_token"])
    stage = store.directory / "downloads" / "staging"
    stage.mkdir(parents=True)
    source = stage / "song.mp3"
    source.write_bytes(b"validated")
    target = store.directory / "downloads" / "library" / "song.mp3"
    probe = {"codec": "mp3", "bitrate_kbps": 320, "duration": 200}
    sha = hashlib.sha256(b"validated").hexdigest()
    return store, (job, source, target, probe, sha)


def test_a_download_rebuilds_the_library_outside_the_lifecycle_lock(
    tmp_path, monkeypatch
):
    """#74 (2): each download's publication rebuilt the whole library (1-2 s
    live) while holding the lifecycle lock, stalling Tidy, scans, exports
    and every other locked operation behind it."""
    store, request = published(tmp_path, monkeypatch)
    rebuilt = store.refresh_library
    held = []

    def refresh():
        held.append(store.lifecycle_lock.owned())
        rebuilt()

    monkeypatch.setattr(store, "refresh_library", refresh)
    store.publish_request(*request)
    assert held == [False]
    assert "Song" in titles(store)


def test_a_locked_operation_never_decides_on_a_library_behind_a_download(
    tmp_path, monkeypatch
):
    """#74 (2), Codex 30 Oct: with the rebuild outside the lock, Tidy (or any
    destructive operation) could take the lock between the publication's
    commit and its rebuild and compare against the old library. Taking the
    lock now rebuilds first."""
    store, request = published(tmp_path, monkeypatch)
    store.commit_request(*request)  # Committed; its rebuild has not run.
    assert "Song" not in titles(store)
    seen = []

    @coordinated
    def destructive(owner):
        seen.extend(titles(owner))

    destructive(store)
    assert "Song" in seen


def test_cancel_all_cancels_only_what_was_on_screen(tmp_path):
    """Codex, #183 round 1 (high) and its confirmation: Cancel all is sent
    after its Undo time; a request submitted meanwhile must survive it, also
    one deleted and submitted again within the same second (same id, same
    created_at)."""
    from fastapi.testclient import TestClient

    from server.app import create_app

    def song(n):
        return {
            "id": f"track-{n}",
            "artist": "Artist",
            "album_artist": "Artist",
            "album": "Album",
            "title": f"Song {n}",
            "year": 2020,
            "duration": 200,
            "disc_number": 1,
            "track_number": n,
            "status": "missing",
        }

    app = create_app(tmp_path / "data")
    store = app.state.store
    store.save_download_settings({"enabled": True, "sources": ["freemp3cloud"]})
    store.submit_catalogue([song(1), song(2)])
    items = store.queue()["items"]
    assert all(i["incarnation"] for i in items)
    shown = [{"id": i["id"], "incarnation": i["incarnation"]} for i in items]
    by_title = {i["title"]: i for i in items}
    store.submit_catalogue([song(3)])  # Submitted during the Undo time.
    # Song 2 is cleared and asked for again at once: the same id, and almost
    # surely the same created_at, but a new entry.
    with store.connect() as conn:
        conn.execute("DELETE FROM queue WHERE id=?", (by_title["Song 2"]["id"],))
    store.submit_catalogue([song(2)])
    again = {i["title"]: i for i in store.queue()["items"]}["Song 2"]
    assert again["id"] == by_title["Song 2"]["id"]
    assert again["incarnation"] != by_title["Song 2"]["incarnation"]
    # Song 1 fails and is asked for again: its row is reused, but it is a new
    # request too.
    with store.connect() as conn:
        conn.execute(
            "UPDATE queue SET status='failed' WHERE id=?", (by_title["Song 1"]["id"],)
        )
    store.submit_catalogue([song(1)])
    revived = {i["title"]: i for i in store.queue()["items"]}["Song 1"]
    assert revived["status"] == "waiting_for_sources"
    assert revived["incarnation"] != by_title["Song 1"]["incarnation"]
    with TestClient(app) as client:
        left = client.post("/api/v1/admin/queue/clear", json={"entries": shown})
        assert left.status_code == 200, left.text
        titles = sorted(i["title"] for i in left.json()["items"])
        assert titles == ["Song 1", "Song 2", "Song 3"]


def test_the_release_verifier_accepts_migration_13_on_a_queue(tmp_path, monkeypatch):
    """Migration 13 (#183) adds only a fresh incarnation to each queue entry;
    the stopped-state verifier the release runs accepts exactly that."""
    import json

    from scripts.verify_state_migration import main

    source = tmp_path / "source"
    source.mkdir()
    (source / "library-snapshot.json").write_text(json.dumps({"tracks": []}))
    store = Store(source)
    store.save_download_settings({"enabled": True, "sources": ["freemp3cloud"]})
    store.submit_catalogue(
        [
            {
                "id": f"track-{n}",
                "artist": "Artist",
                "album_artist": "Artist",
                "album": "Album",
                "title": f"Song {n}",
                "year": 2020,
                "duration": 200,
                "disc_number": 1,
                "track_number": n,
                "status": "missing",
            }
            for n in (1, 2)
        ]
    )
    # The database as release 12 left it.
    with store.connect() as conn:
        conn.execute("DROP TRIGGER queue_incarnation")
        conn.execute("DROP TRIGGER queue_revived")
        conn.execute("ALTER TABLE queue DROP COLUMN incarnation")
        conn.execute("DELETE FROM schema_migrations WHERE version=13")
    expected = tmp_path / "expected.json"
    expected.write_text(json.dumps({"tracks": 0, "album_ids": []}))
    monkeypatch.setattr(
        "sys.argv",
        [
            "verify",
            "--source",
            str(source),
            "--destination",
            str(tmp_path / "copy"),
            "--expected",
            str(expected),
        ],
    )
    main()
    copied = Store(tmp_path / "copy").queue()["items"]
    assert len(copied) == 2 and len({i["incarnation"] for i in copied}) == 2
