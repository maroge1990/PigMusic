import json
import pytest
from server.auth import Auth
from server.store import Store
from server.native_sync import NativeSync, ResyncRequired


def fixture(tmp_path):
    (tmp_path / "library-snapshot.json").write_text(
        json.dumps(
            {
                "tracks": [
                    {
                        "id": str(i),
                        "title": f"Song {i}",
                        "artist": "Artist",
                        "album_artist": "Artist",
                        "album": "Album",
                        "duration": 100,
                        "path": f"/private/song{i}.mp3",
                        "available": True,
                    }
                    for i in range(3)
                ]
            }
        )
    )
    store = Store(tmp_path)
    auth = Auth(store)
    owner = auth.create_owner("mark", "native fixture password")
    member_code = auth.setup_code(owner, "member", "Member")
    member = auth.activate(member_code["code"], "native fixture password", "fixture")[
        "id"
    ]
    return store, owner, member


def test_cursor_scoped_revision_and_durable_deletes(tmp_path):
    store, owner, member = fixture(tmp_path)
    sync = NativeSync(store)
    first = sync.page(owner, "tracks", limit=1)
    assert first["total"] == 3
    assert "/private/" not in json.dumps(first)
    assert first["items"][0]["server_state"] == "in_library"
    assert sync.page(owner, "tracks", first["next_cursor"], 1)["items"][0]["id"] == "1"
    with pytest.raises(ResyncRequired):
        sync.page(member, "tracks", first["next_cursor"], 1)
    with store.connect() as conn:
        conn.execute(
            "DELETE FROM library_members WHERE user_id=? AND track_id='1'", (owner,)
        )
        conn.execute(
            "UPDATE membership_versions SET revision=revision+1 WHERE user_id=?",
            (owner,),
        )
    changed = sync.changes(owner, first["revision"])
    assert changed["deletes"]["tracks"] == ["1"]
    assert NativeSync(Store(tmp_path)).changes(owner, first["revision"]) == changed
    # The collection continues at its own revision (SYNC1); the delta above
    # brings it up to date. Without that revision in memory, it restarts.
    later = sync.page(owner, "tracks", first["next_cursor"], 1)
    assert later["revision"] == first["revision"]
    assert later["items"][0]["id"] == "1"
    with pytest.raises(ResyncRequired):
        NativeSync(store).page(owner, "tracks", first["next_cursor"], 1)
    with pytest.raises(ResyncRequired):
        sync.changes(member, first["revision"])


def test_member_grants_retention_and_invalid_cursor(tmp_path):
    store, owner, member = fixture(tmp_path)
    sync = NativeSync(store)
    first = sync.page(member, "tracks")
    store.user_libraries.add(member, ["0"])
    changes = sync.changes(member, first["revision"])
    assert [t["id"] for t in changes["upserts"]["tracks"]] == ["0"]
    assert len(changes["upserts"]["albums"]) == 1
    with store.connect() as conn:
        conn.execute(
            "UPDATE sync_revisions SET captured_at=0 WHERE user_id=?", (member,)
        )
    with pytest.raises(ResyncRequired):
        sync.changes(member, first["revision"])
    with pytest.raises(ValueError):
        sync.page(owner, "tracks", "bad!", 100)


def test_private_playlist_delta_and_return_to_previous_file_state(tmp_path):
    from server.playlists import Playlists

    store, owner, member = fixture(tmp_path)
    sync = NativeSync(store)
    first = sync.page(owner, "tracks")
    playlist = Playlists(store, owner).create("Private fixture")
    delta = sync.changes(owner, first["revision"])
    assert delta["upserts"]["playlists"][0]["name"] == "Private fixture"
    assert (
        sync.changes(member, sync.page(member, "tracks")["revision"])["upserts"][
            "playlists"
        ]
        == []
    )
    Playlists(store, owner).edit(playlist["id"], 0, delete=True)
    removed = sync.changes(owner, delta["revision"])
    assert removed["deletes"]["playlists"] == [playlist["id"]]
    original_revision = store.library_revision
    store.library_revision = "changed-household"
    sync.page(owner, "tracks")
    store.library_revision = original_revision
    again = sync.page(owner, "tracks")
    assert again["revision"] != first["revision"]
    assert all(t["album_id"] for t in again["items"])


def test_server_fetched_cover_is_addressable_by_hash(tmp_path):
    # Albums without embedded art get catalogue covers from the server. Native
    # clients address artwork by hash, so the stored cover's hash is exposed.
    store, owner, _ = fixture(tmp_path)
    sha = "a" * 64
    album = store.library["albums"][0]
    album["cover_sha"], album["cover_url"] = None, f"/api/v1/covers/{sha}.jpg"
    store.library_revision += "-changed"
    sync = NativeSync(store)
    track = sync.page(owner, "tracks", limit=1)["items"][0]
    assert track["cover_sha"] == sha and track["album_id"] == album["id"]
    assert sync.page(owner, "albums")["items"][0]["cover_sha"] == sha
    personal = store.user_libraries.projection(owner)["tracks"][0]
    assert personal["album_id"] == album["id"] and personal["cover_sha"] == sha
    assert "cover_url" not in album["tracks"][0]  # household data untouched
    album["cover_url"] = "/api/v1/library-artwork/albums/" + album["id"]
    store.library_revision += "-changed"
    assert NativeSync(store).page(owner, "albums")["items"][0]["cover_sha"] is None


def test_artist_portrait_reaches_native_once_fetched(tmp_path):
    from PIL import Image
    from server.library_artwork import portraits_waiting
    from server.portraits import portrait_target

    store, owner, _ = fixture(tmp_path)
    sync = NativeSync(store)
    before = sync.page(owner, "artists")
    assert before["items"][0]["cover_sha"] is None
    assert not portraits_waiting(store)
    # The warmer saves a portrait without refreshing the library.
    target = portrait_target(store, "Artist")
    target.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), "red").save(target, "JPEG")
    assert portraits_waiting(store)
    store.refresh_library()
    assert not portraits_waiting(store)
    artist = sync.page(owner, "artists")["items"][0]
    assert artist["cover_url"] == f"/api/v1/covers/{artist['cover_sha']}.jpg"
    changed = sync.changes(owner, before["revision"])
    assert [a["cover_sha"] for a in changed["upserts"]["artists"]] == [
        artist["cover_sha"]
    ]


def test_existing_clients_get_fields_an_upgraded_server_adds(tmp_path, monkeypatch):
    """An installation captured by a server whose artists had no `cover_sha`
    still sends the portrait hash after upgrading, without a resync."""
    from PIL import Image
    from pydantic import create_model

    from server import native_sync
    from server.api_models import ArtistDTO
    from server.portraits import portrait_target

    store, owner, _ = fixture(tmp_path)
    target = portrait_target(store, "Artist")
    target.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), "red").save(target, "JPEG")
    store.refresh_library()
    fields = {
        name: (field.annotation, field)
        for name, field in ArtistDTO.model_fields.items()
        if name != "cover_sha"
    }
    old = create_model("ArtistDTO", __config__=ArtistDTO.model_config, **fields)
    monkeypatch.setitem(native_sync.MODELS, "artists", old)
    before = NativeSync(store).page(owner, "artists")
    assert "cover_sha" not in before["items"][0]
    monkeypatch.undo()
    # The restarted server: same library, current DTOs.
    sync = NativeSync(Store(tmp_path))
    fresh = sync.page(owner, "artists")["items"][0]
    assert fresh["cover_sha"]
    changed = sync.changes(owner, before["revision"])
    assert changed["revision"] != before["revision"]
    assert [a["cover_sha"] for a in changed["upserts"]["artists"]] == [
        fresh["cover_sha"]
    ]
    # A second read is quiet again.
    assert sync.changes(owner, changed["revision"])["upserts"]["artists"] == []


def test_later_pages_never_wait_for_the_household_locks(tmp_path, monkeypatch):
    import threading
    import time

    from server import native_sync

    store, owner, _ = fixture(tmp_path)
    sync = NativeSync(store)
    first = sync.page(owner, "tracks", limit=1)
    held, release = threading.Event(), threading.Event()

    def hold():
        # A publication and a library change in progress elsewhere.
        with store.lifecycle_lock, store.library_lock:
            held.set()
            release.wait(10)

    holder = threading.Thread(target=hold)
    holder.start()
    try:
        assert held.wait(5)
        started = time.monotonic()
        second = sync.page(owner, "tracks", first["next_cursor"], 1)
        third = sync.page(owner, "tracks", second["next_cursor"], 1)
        assert time.monotonic() - started < 0.5
        assert [second["items"][0]["id"], third["items"][0]["id"]] == ["1", "2"]
        assert third["next_cursor"] is None
    finally:
        release.set()
        holder.join(5)
    # A revision kept too long is captured again, as before.
    monkeypatch.setattr(native_sync, "CURSOR_SECONDS", 0)
    assert sync.page(owner, "tracks", first["next_cursor"], 1)["items"][0]["id"] == "1"
