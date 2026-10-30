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
