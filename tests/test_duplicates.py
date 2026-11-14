import json

from server.auth import Auth
from server.duplicates import find, remove
from server.recovery import Recovery
from server.store import Store


def song(**extra):
    return {
        "title": "Dreams",
        "artist": "Fleetwood Mac",
        "album_artist": "Fleetwood Mac",
        "album": "Rumours",
        "duration": 257,
        "year": 1977,
        "disc_number": 1,
        "track_number": 2,
        **extra,
    }


def setup(tmp_path, monkeypatch):
    nas = tmp_path / "music"
    (nas / "Fleetwood Mac" / "Rumours").mkdir(parents=True)
    (nas / "Fleetwood Mac" / "Rumours" / "Dreams.mp3").write_bytes(b"nas copy")
    monkeypatch.setenv("PIG_MUSIC_LIBRARY", str(nas))
    data = tmp_path / "data"
    data.mkdir()
    original = song(id="nas", relative_path="Fleetwood Mac/Rumours/Dreams.mp3")
    (data / "library-snapshot.json").write_text(json.dumps({"tracks": [original]}))
    store = Store(data)
    downloads = data / "downloads" / "library"
    with store.connect() as conn:
        for track_id, album, extra in (
            ("hits", "Greatest Hits", {}),
            ("deluxe", "Rumours (Deluxe)", {"explicit_edition": True}),
            ("other", "Tusk", {"title": "Sara"}),
        ):
            path = downloads / "Fleetwood Mac" / album / (track_id + ".mp3")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(track_id.encode())
            relative = path.relative_to(downloads).as_posix()
            payload = song(id=track_id, album=album, relative_path=relative, **extra)
            conn.execute(
                "INSERT INTO acquired VALUES (?,?)", (track_id, json.dumps(payload))
            )
    store.refresh_library()
    auth = Auth(store)
    owner = auth.create_owner("mark", "duplicates fixture password")
    code = auth.setup_code(owner, "member", "Member")
    member = auth.activate(code["code"], "duplicates fixture password", "f")["id"]
    store.user_libraries.add(member, ["hits"])
    return store, member, nas


def test_only_unrequested_downloads_of_a_song_already_kept_are_proposed(
    tmp_path, monkeypatch
):
    store, _, _ = setup(tmp_path, monkeypatch)
    (duplicate,) = find(store)
    # The NAS copy stays; the explicitly chosen edition and other songs stay.
    assert duplicate["track"]["id"] == "hits" and duplicate["keeper"]["id"] == "nas"
    assert duplicate["album"] == "Greatest Hits"


def test_removal_moves_listeners_to_the_kept_copy_and_can_be_restored(
    tmp_path, monkeypatch
):
    store, member, nas = setup(tmp_path, monkeypatch)
    recovery = Recovery(store)
    result = remove(store, recovery, ["hits", "nas", "other"])
    assert result["removed"] == 1 and result["skipped"] == 2
    # Its Trash entries come back with it, for Undo (D-79, #183).
    assert result["restorable"] == [
        r["id"] for r in recovery.list() if r["track_id"] == "hits"
    ]
    titles = {a["title"] for a in store.library["albums"]}
    assert "Greatest Hits" not in titles and "Rumours" in titles
    assert (nas / "Fleetwood Mac" / "Rumours" / "Dreams.mp3").exists()
    saved = {t["id"] for t in store.user_libraries.projection(member)["tracks"]}
    assert "nas" in saved and "hits" not in saved
    (record,) = [r for r in recovery.list() if r["track_id"] == "hits"]
    recovery.restore(record["id"])
    assert "Greatest Hits" in {a["title"] for a in store.library["albums"]}
