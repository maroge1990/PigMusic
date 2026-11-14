import hashlib
import json
import time
import pytest
from server.store import Store
from server.recovery import Recovery


def setup(tmp_path, monkeypatch):
    root = tmp_path / "music"
    root.mkdir()
    monkeypatch.setenv("PIG_MUSIC_LIBRARY", str(root))
    path = root / "Artist" / "Album" / "Song.mp3"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"original music")
    track = {
        "id": "saved",
        "title": "Song",
        "artist": "Artist",
        "album_artist": "Artist",
        "album": "Album",
        "relative_path": "Artist/Album/Song.mp3",
        "duration": 100,
        "year": 2020,
        "disc_number": 1,
        "track_number": 1,
    }
    data = tmp_path / "data"
    data.mkdir()
    (data / "library-snapshot.json").write_text(json.dumps({"tracks": [track]}))
    store = Store(data)
    return store, root, path, track


def test_remove_restore_and_monitor_exclusion(tmp_path, monkeypatch):
    store, root, path, track = setup(tmp_path, monkeypatch)
    recovery = Recovery(store)
    record = recovery.remove("saved")
    assert not path.exists() and not store.library["albums"]
    result = store.submit_catalogue([{**track, "status": "missing"}])
    assert result["skipped"] == 1 and result["created"] == 0
    recovery.restore(record["id"])
    assert path.read_bytes() == b"original music" and store.library["albums"]
    assert store.submit_catalogue([{**track, "status": "missing"}])["created"] == 1


def test_restore_refuses_collision_and_keeps_recoverable_file(tmp_path, monkeypatch):
    store, root, path, track = setup(tmp_path, monkeypatch)
    recovery = Recovery(store)
    record = recovery.remove("saved")
    path.write_bytes(b"another file")
    with pytest.raises(ValueError):
        recovery.restore(record["id"])
    assert path.read_bytes() == b"another file" and (root / record["trash"]).exists()


def test_recovery_finishes_interrupted_removal(tmp_path, monkeypatch):
    store, root, path, track = setup(tmp_path, monkeypatch)
    recovery = Recovery(store)
    record = recovery.remove("saved")
    record["status"] = "removing"
    recovery.save("removed:" + record["id"], record)
    Recovery(Store(store.directory)).recover()
    assert recovery.list()[0]["status"] == "removed"


def test_removal_keeps_completed_download_history(tmp_path, monkeypatch):
    store, root, path, track = setup(tmp_path, monkeypatch)
    store.submit_catalogue([{**track, "status": "missing"}])
    with store.connect() as c:
        c.execute("UPDATE queue SET status='completed'")
    Recovery(store).remove("saved")
    assert store.queue()["items"][0]["status"] == "completed"


def test_removal_rejects_changed_library_mount(tmp_path, monkeypatch):
    store, root, path, track = setup(tmp_path, monkeypatch)
    (store.directory / "library-export.json").write_text(
        json.dumps({"library_id": "expected"})
    )
    with pytest.raises(ValueError):
        Recovery(store).remove("saved")
    assert path.read_bytes() == b"original music" and not Recovery(store).list()


def two_removed(tmp_path, monkeypatch):
    """Two songs in Trash: "old" removed 40 days ago, "new" 5 days ago."""
    store, root, path, track = setup(tmp_path, monkeypatch)
    other = root / "Artist" / "Album" / "Other.mp3"
    other.write_bytes(b"other music!")
    second = {
        **track,
        "id": "other",
        "title": "Other",
        "track_number": 2,
        "relative_path": "Artist/Album/Other.mp3",
    }
    (store.directory / "library-snapshot.json").write_text(
        json.dumps({"tracks": [track, second]})
    )
    store.refresh_library()
    recovery = Recovery(store)
    old = recovery.remove("saved")
    new = recovery.remove("other")
    now = time.time()
    for record, age in ((old, 40), (new, 5)):
        record["removed_at"] = int(now - age * 86400)
        recovery.save("removed:" + record["id"], record)
    return store, root, recovery, old, new


def test_purge_lists_exactly_what_it_would_delete(tmp_path, monkeypatch):
    """#96 (owner choice): Trash was kept for good; the owner purges what
    has been there longer than N days, from a list."""
    store, root, recovery, old, new = two_removed(tmp_path, monkeypatch)
    plan = recovery.purge_plan(30)
    assert [(i["id"], i["title"], i["size_bytes"]) for i in plan["items"]] == [
        (old["id"], "Song", len(b"original music"))
    ]
    assert plan["total_bytes"] == len(b"original music")
    assert {i["id"] for i in recovery.purge_plan(1)["items"]} == {old["id"], new["id"]}


def test_purge_deletes_only_the_listed_files_and_keeps_the_song_removed(
    tmp_path, monkeypatch
):
    store, root, recovery, old, new = two_removed(tmp_path, monkeypatch)
    keep = root / "Artist" / "Album" / "Kept.mp3"
    keep.write_bytes(b"still in the library")
    elsewhere = root / ".pig-music-trash" / "organisation" / "x" / "song.mp3"
    elsewhere.parent.mkdir(parents=True)
    elsewhere.write_bytes(b"tidy original")
    plan = recovery.purge_plan(30)
    assert recovery.purge(30, plan["fingerprint"]) == {"purged": 1, "kept": 0}
    assert not (root / old["trash"]).exists()
    assert not (root / ".pig-music-trash" / old["id"]).exists()
    assert (root / new["trash"]).exists() and keep.exists() and elsewhere.exists()
    record = store.read_document("removed:" + old["id"])
    assert record["status"] == "removed" and record["purged_at"]
    with pytest.raises(ValueError):
        recovery.restore(old["id"])
    # Still excluded: a purge never brings a removed song back.
    result = store.submit_catalogue([{**old["track"], "status": "missing"}])
    assert result["created"] == 0
    assert recovery.purge_plan(30)["items"] == []


def test_purge_refuses_a_changed_set(tmp_path, monkeypatch):
    store, root, recovery, old, new = two_removed(tmp_path, monkeypatch)
    plan = recovery.purge_plan(30)
    new["removed_at"] = int(time.time() - 60 * 86400)
    recovery.save("removed:" + new["id"], new)
    with pytest.raises(ValueError):
        recovery.purge(30, plan["fingerprint"])
    with pytest.raises(ValueError):
        recovery.purge(1, plan["fingerprint"])
    assert (root / old["trash"]).exists() and (root / new["trash"]).exists()


def test_purge_keeps_a_changed_file_and_never_leaves_trash(tmp_path, monkeypatch):
    store, root, recovery, old, new = two_removed(tmp_path, monkeypatch)
    # A record pointing outside its Trash folder is never listed.
    library = root / "Artist" / "Album" / "Library.mp3"
    library.write_bytes(b"library file")
    new.update(trash="Artist/Album/Library.mp3", removed_at=0)
    recovery.save("removed:" + new["id"], new)
    trash = root / old["trash"]
    trash.write_bytes(b"replaced content")  # Same name, other content.
    plan = recovery.purge_plan(30)
    assert [i["id"] for i in plan["items"]] == [old["id"]]
    assert recovery.purge(30, plan["fingerprint"]) == {"purged": 0, "kept": 1}
    assert trash.exists() and library.exists()


def test_purge_never_follows_a_linked_trash_path(tmp_path, monkeypatch):
    store, root, recovery, old, new = two_removed(tmp_path, monkeypatch)
    trash = root / old["trash"]
    outside = tmp_path / "outside.mp3"
    outside.write_bytes(trash.read_bytes())
    trash.unlink()
    trash.symlink_to(outside)
    assert recovery.purge_plan(30)["items"] == []
    folder = root / ".pig-music-trash" / new["id"]
    moved = tmp_path / "moved"
    folder.rename(moved)
    folder.symlink_to(moved, target_is_directory=True)
    assert recovery.purge_plan(1)["items"] == []
    assert outside.exists() and any(moved.rglob("*.mp3"))


class SwapAfterHashing:
    """Stands in for hashlib in server.recovery: once the purge has hashed
    the Trash file, another writer on the share runs `swap` (Codex round 1:
    the check and the delete resolved the path independently)."""

    def __init__(self, swap):
        self.swap, self.done = swap, False

    def after(self):
        if not self.done:
            self.done = True
            self.swap()

    def sha256(self, *args):
        real, outer = hashlib.sha256(*args), self

        class Hash:
            read = False  # A file's content, not the plan's fingerprint.

            def update(self, data):
                self.read = True
                real.update(data)

            def hexdigest(self):
                value = real.hexdigest()
                if self.read:
                    outer.after()
                return value

        return Hash()

    def file_digest(self, *args, **kwargs):
        value = hashlib.file_digest(*args, **kwargs)
        self.after()
        return value

    def __getattr__(self, name):
        return getattr(hashlib, name)


def test_purge_never_follows_a_trash_folder_swapped_for_a_link_after_checking(
    tmp_path, monkeypatch
):
    import server.recovery as module

    store, root, recovery, old, new = two_removed(tmp_path, monkeypatch)
    library = root / "Artist" / "Album" / "Song.mp3"
    library.write_bytes(b"the library's own copy")
    trash = root / old["trash"]

    def swap():
        trash.parent.rename(trash.parent.with_name("Album-moved"))
        trash.parent.symlink_to(root / "Artist" / "Album", target_is_directory=True)

    plan = recovery.purge_plan(30)
    swapper = SwapAfterHashing(swap)
    monkeypatch.setattr(module, "hashlib", swapper)
    recovery.purge(30, plan["fingerprint"])
    assert swapper.done
    assert library.read_bytes() == b"the library's own copy"


def test_purge_never_deletes_a_trash_file_replaced_after_checking(
    tmp_path, monkeypatch
):
    import server.recovery as module

    store, root, recovery, old, new = two_removed(tmp_path, monkeypatch)
    trash = root / old["trash"]

    def swap():
        trash.unlink(missing_ok=True)
        trash.write_bytes(b"another file")

    plan = recovery.purge_plan(30)
    swapper = SwapAfterHashing(swap)
    monkeypatch.setattr(module, "hashlib", swapper)
    recovery.purge(30, plan["fingerprint"])
    assert swapper.done
    assert trash.read_bytes() == b"another file"


class Stop(BaseException):
    """The process stops here."""


def test_a_purge_stopped_after_its_rename_is_put_back_at_the_next_start(
    tmp_path, monkeypatch
):
    """Codex round 2: a stop between the private rename and the delete left
    the only Trash copy under an unrecorded name, out of Restore's reach."""
    import server.recovery as module

    store, root, recovery, old, new = two_removed(tmp_path, monkeypatch)
    plan = recovery.purge_plan(30)

    def stop():
        raise Stop

    monkeypatch.setattr(module, "hashlib", SwapAfterHashing(stop))
    with pytest.raises(Stop):
        recovery.purge(30, plan["fingerprint"])
    monkeypatch.undo()
    monkeypatch.setenv("PIG_MUSIC_LIBRARY", str(root))
    assert not (root / old["trash"]).exists()
    restarted = Recovery(Store(store.directory))
    restarted.recover()
    assert (root / old["trash"]).exists()
    assert not list((root / old["trash"]).parent.glob(".pig-music-purge-*"))
    record = store.read_document("removed:" + old["id"])
    assert "quarantine" not in record and not record.get("purged_at")
    restarted.restore(old["id"])
    assert (root / "Artist" / "Album" / "Song.mp3").read_bytes() == b"original music"


def test_a_purge_stopped_after_deleting_is_recorded_at_the_next_start(
    tmp_path, monkeypatch
):
    store, root, recovery, old, new = two_removed(tmp_path, monkeypatch)
    plan = recovery.purge_plan(30)
    real = Recovery.save

    def save(self, key, value):
        if value.get("purged_at"):
            raise Stop
        return real(self, key, value)

    monkeypatch.setattr(Recovery, "save", save)
    with pytest.raises(Stop):
        recovery.purge(30, plan["fingerprint"])
    monkeypatch.setattr(Recovery, "save", real)
    assert not (root / old["trash"]).exists()
    restarted = Recovery(Store(store.directory))
    restarted.recover()
    record = store.read_document("removed:" + old["id"])
    assert record["purged_at"] and "quarantine" not in record
    assert [i["id"] for i in restarted.purge_plan(1)["items"]] == [new["id"]]
    with pytest.raises(ValueError):
        restarted.restore(old["id"])


def test_a_stopped_purge_whose_name_was_taken_stays_restorable(tmp_path, monkeypatch):
    import server.recovery as module

    store, root, recovery, old, new = two_removed(tmp_path, monkeypatch)
    trash = root / old["trash"]
    plan = recovery.purge_plan(30)

    def stop():
        trash.write_bytes(b"someone else's file")
        raise Stop

    monkeypatch.setattr(module, "hashlib", SwapAfterHashing(stop))
    with pytest.raises(Stop):
        recovery.purge(30, plan["fingerprint"])
    monkeypatch.undo()
    monkeypatch.setenv("PIG_MUSIC_LIBRARY", str(root))
    restarted = Recovery(Store(store.directory))
    restarted.recover()
    assert trash.read_bytes() == b"someone else's file"
    restarted.restore(old["id"])
    assert (root / "Artist" / "Album" / "Song.mp3").read_bytes() == b"original music"


def test_a_stopped_purge_whose_trash_folder_was_replaced_is_never_marked_purged(
    tmp_path, monkeypatch
):
    """D-71 round: after the private rename another writer moves the Trash
    folder away and makes an empty one in its place; both names missing
    there proved nothing, yet the song was marked purged for good."""
    import server.recovery as module

    store, root, recovery, old, new = two_removed(tmp_path, monkeypatch)
    trash = root / old["trash"]
    moved = trash.parent.with_name("Album-moved")
    plan = recovery.purge_plan(30)

    def stop():
        trash.parent.rename(moved)
        trash.parent.mkdir()
        raise Stop

    monkeypatch.setattr(module, "hashlib", SwapAfterHashing(stop))
    with pytest.raises(Stop):
        recovery.purge(30, plan["fingerprint"])
    monkeypatch.undo()
    monkeypatch.setenv("PIG_MUSIC_LIBRARY", str(root))
    restarted = Recovery(Store(store.directory))
    restarted.recover()
    record = store.read_document("removed:" + old["id"])
    assert not record.get("purged_at") and record.get("quarantine")
    assert list(moved.glob(".pig-music-purge-*"))  # The song is still there.
    assert old["id"] not in [i["id"] for i in restarted.purge_plan(1)["items"]]
    with pytest.raises(ValueError, match="needs review"):
        restarted.restore(old["id"])


def test_a_stopped_purge_whose_folder_was_replaced_with_a_same_named_file_needs_review(
    tmp_path, monkeypatch
):
    """D-71 round: the replacement folder held another file under the song's
    name, so recovery cleared the intent without checking the folder, and
    the song's only copy was lost under its private name."""
    import server.recovery as module

    store, root, recovery, old, new = two_removed(tmp_path, monkeypatch)
    trash = root / old["trash"]
    moved = trash.parent.with_name("Album-moved")
    plan = recovery.purge_plan(30)

    def stop():
        trash.parent.rename(moved)
        trash.parent.mkdir()
        trash.write_bytes(b"unrelated content")
        raise Stop

    monkeypatch.setattr(module, "hashlib", SwapAfterHashing(stop))
    with pytest.raises(Stop):
        recovery.purge(30, plan["fingerprint"])
    monkeypatch.undo()
    monkeypatch.setenv("PIG_MUSIC_LIBRARY", str(root))
    restarted = Recovery(Store(store.directory))
    restarted.recover()
    record = store.read_document("removed:" + old["id"])
    assert record.get("quarantine") and record.get("quarantine_folder")
    assert not record.get("purged_at") and record["trash"] == old["trash"]
    assert list(moved.glob(".pig-music-purge-*"))
    assert trash.read_bytes() == b"unrelated content"
    assert old["id"] not in [i["id"] for i in restarted.purge_plan(1)["items"]]
    with pytest.raises(ValueError, match="needs review"):
        restarted.restore(old["id"])


def test_a_trash_folder_moved_while_recovery_hashes_keeps_the_intent(
    tmp_path, monkeypatch
):
    """D-71 round: recovery's checks went through its held folder; a folder
    moved and recreated while it hashed left the record pointing at the
    replacement with its intent cleared, so Restore failed and Needs review
    was gone."""
    import server.recovery as module

    store, root, recovery, old, new = two_removed(tmp_path, monkeypatch)
    trash = root / old["trash"]
    moved = trash.parent.with_name("Album-moved")
    plan = recovery.purge_plan(30)

    def stop():
        raise Stop

    monkeypatch.setattr(module, "hashlib", SwapAfterHashing(stop))
    with pytest.raises(Stop):
        recovery.purge(30, plan["fingerprint"])

    def move():
        trash.parent.rename(moved)
        trash.parent.mkdir()

    monkeypatch.setattr(module, "hashlib", SwapAfterHashing(move))
    restarted = Recovery(Store(store.directory))
    restarted.recover()
    record = store.read_document("removed:" + old["id"])
    assert record.get("quarantine") and record.get("quarantine_folder")
    assert not record.get("purged_at")
    assert (moved / "Song.mp3").exists()  # Put back, in the moved folder.
    with pytest.raises(ValueError, match="needs review"):
        restarted.restore(old["id"])


def test_a_trash_folder_moved_while_the_purge_hashes_is_never_recorded_purged(
    tmp_path, monkeypatch
):
    import server.recovery as module

    store, root, recovery, old, new = two_removed(tmp_path, monkeypatch)
    trash = root / old["trash"]
    plan = recovery.purge_plan(30)

    def move():
        trash.parent.rename(trash.parent.with_name("Album-moved"))
        trash.parent.mkdir()

    monkeypatch.setattr(module, "hashlib", SwapAfterHashing(move))
    assert recovery.purge(30, plan["fingerprint"]) == {"purged": 0, "kept": 1}
    record = store.read_document("removed:" + old["id"])
    assert record.get("quarantine") and not record.get("purged_at")


def test_undo_restores_every_song_a_removal_put_in_trash(tmp_path, monkeypatch):
    """D-79, #183: an album, artist or duplicate removal answers with its Trash
    entries, and one request restores them all; one that cannot be restored
    is reported, never hidden, and the others still come back."""
    from fastapi.testclient import TestClient

    from server.app import create_app

    store, root, path, track = setup(tmp_path, monkeypatch)
    second = root / "Artist" / "Album" / "Other.mp3"
    second.write_bytes(b"second song")
    other = {
        **track,
        "id": "other",
        "title": "Other",
        "relative_path": "Artist/Album/Other.mp3",
        "track_number": 2,
    }
    (tmp_path / "data" / "library-snapshot.json").write_text(
        json.dumps({"tracks": [track, other]})
    )
    app = create_app(tmp_path / "data")
    with TestClient(app) as client:
        (album,) = app.state.store.library["albums"]
        removed = client.post(f"/api/v1/admin/albums/{album['id']}/remove").json()
        assert removed["removed"] == 2 and len(removed["restorable"]) == 2
        assert not path.exists() and not second.exists()
        restored = client.post(
            "/api/v1/admin/library/trash/restore",
            json={"identities": removed["restorable"] + ["unknown"]},
        )
        assert restored.status_code == 200, restored.text
        body = restored.json()
        assert sorted(body["restored"]) == sorted(removed["restorable"])
        assert body["failed"] == ["unknown"]
        assert path.read_bytes() == b"original music"
        assert second.read_bytes() == b"second song"
        # Restoring again finds nothing to restore, and says so.
        again = client.post(
            "/api/v1/admin/library/trash/restore",
            json={"identities": removed["restorable"]},
        ).json()
        assert again["restored"] == [] and len(again["failed"]) == 2
