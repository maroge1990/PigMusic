import json
import shutil
import subprocess

import pytest

from server.auth import Auth
from server.organisation import Organisation
from server.recovery import Recovery
from server.scanner import inspect_file
from server.store import Store
from server.tidy import Tidy, better, edition_title

STANDARD = "Hot Fuss"
DELUXE = "Hot Fuss (Deluxe Edition)"


def song(title, album, number, codec="mp3", bitrate=320, **extra):
    return {
        "id": f"{album}/{title}",
        "title": title,
        "artist": "The Killers",
        "album_artist": "The Killers",
        "album": album,
        "duration": 3,
        "year": 2004,
        "disc_number": 1,
        "track_number": number,
        "codec": codec,
        "bitrate": bitrate,
        "relative_path": f"The Killers/{album}/{number:02} {title}.mp3",
        **extra,
    }


def planned(tmp_path, tracks, monkeypatch):
    monkeypatch.setenv("PIG_MUSIC_LIBRARY", str(tmp_path / "music"))
    data = tmp_path / "data"
    data.mkdir()
    (data / "library-snapshot.json").write_text(json.dumps({"tracks": tracks}))
    store = Store(data)
    recovery = Recovery(store)
    return Tidy(store, recovery, Organisation(store, recovery)).plan()["albums"]


def test_titles_and_quality():
    assert edition_title({"title": DELUXE, "artist": "The Killers"}) == "hot fuss"
    assert edition_title(
        {"title": "The Killers - Hot Fuss", "artist": "The Killers"}
    ) == ("hot fuss")
    # A single or EP is an album of its own.
    assert edition_title({"title": "Hot Fuss - EP", "artist": "x"}) != "hot fuss"
    flac, mp3, low = (
        {"codec": "flac", "bitrate": 900},
        {"codec": "mp3", "bitrate": 320},
        {"codec": "mp3", "bitrate": 128},
    )
    assert better(flac, mp3) and better(mp3, low)
    assert not better(mp3, flac) and not better(mp3, {"codec": "mp3", "bitrate": 300})
    # Unknown quality is never replaced on a guess, nor replaces anything.
    assert not better(flac, {"codec": "unknown"}) and not better({}, low)


def test_plan_keeps_the_fullest_edition_and_lists_what_only_others_have(
    tmp_path, monkeypatch
):
    tracks = [
        song("Jenny", STANDARD, 1),
        song("Mr. Brightside", STANDARD, 2, codec="flac", bitrate=900),
        song("Glamorous Indie Rock & Roll", STANDARD, 3),
        song("Jenny", DELUXE, 1),
        song("Mr. Brightside", DELUXE, 2),
        song("Smile Like You Mean It", DELUXE, 3),
        song("Somebody Told Me", DELUXE, 4),
        # Neither a single nor a live album is an edition.
        song("Mr. Brightside", "Hot Fuss - EP", 1),
        song("Jenny", "Live from the Royal Albert Hall", 1),
        # The same title by the same artist with other songs is another album.
        song("Buddy Holly", "Weezer", 1, artist="Weezer", album_artist="Weezer"),
        song("Island in the Sun", "Weezer", 1, artist="Weezer", album_artist="Weezer")
        | {"id": "green", "relative_path": "Weezer/Green/01.mp3"},
    ]
    (item,) = planned(tmp_path, tracks, monkeypatch)
    assert item["keep"] == {
        "album_id": item["id"],
        "title": DELUXE,
        "year": 2004,
        "songs": 4,
    }
    assert [e["title"] for e in item["editions"]] == [STANDARD]
    assert [(r["title"], r["kept"]["album"]) for r in item["remove"]] == [
        ("Jenny", DELUXE)
    ]
    # The better copy moves into the kept album, replacing its own.
    (replace,) = item["replace"]
    assert replace["id"] == f"{STANDARD}/Mr. Brightside"
    assert replace["replaces"]["id"] == f"{DELUXE}/Mr. Brightside"
    assert (replace["quality"], replace["replaces"]["quality"]) == ("FLAC", "MP3 320")
    assert [(r["title"], r["album"]) for r in item["leftovers"]] == [
        ("Glamorous Indie Rock & Roll", STANDARD)
    ]


def test_explicit_editions_are_never_tidied(tmp_path, monkeypatch):
    tracks = [song("Jenny", STANDARD, 1), song("Jenny", DELUXE, 1)]
    monkeypatch.setenv("PIG_MUSIC_LIBRARY", str(tmp_path / "music"))
    data = tmp_path / "data"
    data.mkdir()
    (data / "library-snapshot.json").write_text(json.dumps({"tracks": tracks[:1]}))
    store = Store(data)
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO acquired VALUES (?,?)",
            (tracks[1]["id"], json.dumps({**tracks[1], "explicit_edition": True})),
        )
    store.refresh_library()
    recovery = Recovery(store)
    assert Tidy(store, recovery, Organisation(store, recovery)).plan()["albums"] == []


def audio(path, title, album, number, codec):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg required for real audio integration")
    path.parent.mkdir(parents=True, exist_ok=True)
    encode = (
        ["-c:a", "flac"]
        if codec == "flac"
        else ["-c:a", "libmp3lame", "-b:a", codec.removeprefix("mp3_") + "k"]
    )
    subprocess.run(
        [ffmpeg, "-v", "error", "-f", "lavfi", "-i", "sine=duration=3", *encode]
        + [
            arg
            for key, value in (
                ("artist", "The Killers"),
                ("album_artist", "The Killers"),
                ("album", album),
                ("title", title),
                ("track", str(number)),
            )
            for arg in ("-metadata", f"{key}={value}")
        ]
        + [str(path)],
        check=True,
        timeout=20,
    )


def library(tmp_path, monkeypatch):
    root = tmp_path / "music"
    monkeypatch.setenv("PIG_MUSIC_LIBRARY", str(root))
    files = [
        (STANDARD, "Jenny", 1, "flac"),
        (STANDARD, "Mr. Brightside", 2, "mp3_320"),
        (DELUXE, "Jenny", 1, "mp3_128"),
        (DELUXE, "Mr. Brightside", 2, "mp3_320"),
        (DELUXE, "Smile Like You Mean It", 3, "mp3_320"),
    ]
    tracks = []
    for album, title, number, codec in files:
        suffix = ".flac" if codec == "flac" else ".mp3"
        relative = f"The Killers/{album}/{number:02} {title}{suffix}"
        audio(root / relative, title, album, number, codec)
        tracks.append(inspect_file(root / relative, relative)[0])
    data = tmp_path / "data"
    data.mkdir()
    (data / "library-snapshot.json").write_text(json.dumps({"tracks": tracks}))
    store = Store(data)
    auth = Auth(store)
    owner = auth.create_owner("mark", "tidy fixture password")
    code = auth.setup_code(owner, "member", "Member")
    member = auth.activate(code["code"], "tidy fixture password", "f")["id"]
    ids = {(t["album"], t["title"]): t["id"] for t in tracks}
    store.user_libraries.add(
        member, [ids[STANDARD, "Jenny"], ids[STANDARD, "Mr. Brightside"]]
    )
    recovery = Recovery(store)
    return Tidy(store, recovery, Organisation(store, recovery)), root, member, ids


def songs(store, user_id=None):
    if user_id:
        tracks = store.user_libraries.projection(user_id)["tracks"]
        return sorted((t["album"], t["title"]) for t in tracks)
    return sorted(
        (a["title"], t["title"], t["codec"])
        for a in store.library["albums"]
        for t in a["tracks"]
    )


def test_apply_keeps_the_best_copies_in_the_fullest_edition_and_undo_restores(
    tmp_path, monkeypatch
):
    tidy, root, member, ids = library(tmp_path, monkeypatch)
    store = tidy.store
    before = songs(store)
    (item,) = tidy.plan()["albums"]
    with pytest.raises(ValueError):
        tidy.apply(item["id"], "0" * 64)
    record = tidy.apply(item["id"], item["fingerprint"])
    assert record["status"] == "applied"
    # One album, with the FLAC Jenny moved in and retagged to it.
    assert songs(store) == [
        (DELUXE, "Jenny", "flac"),
        (DELUXE, "Mr. Brightside", "mp3"),
        (DELUXE, "Smile Like You Mean It", "mp3"),
    ]
    moved = root / "The Killers" / DELUXE / f"The Killers - {DELUXE} - 01 - Jenny.flac"
    assert inspect_file(moved, "x")[0]["album"] == DELUXE
    assert not (root / "The Killers" / STANDARD / "01 Jenny.flac").exists()
    assert not (root / "The Killers" / DELUXE / "01 Jenny.mp3").exists()
    assert not (root / "The Killers" / STANDARD / "02 Mr. Brightside.mp3").exists()
    # The member's songs are now the kept album's.
    assert songs(store, member) == [(DELUXE, "Jenny"), (DELUXE, "Mr. Brightside")]
    # Nothing was excluded: the song can still be requested and found.
    with store.connect() as conn:
        assert not conn.execute(
            "SELECT 1 FROM documents WHERE name LIKE 'excluded:%'"
        ).fetchone()
    assert tidy.plan()["albums"] == []
    (entry,) = tidy.history()
    tidy.undo(entry["id"])
    assert songs(store) == before
    assert (root / "The Killers" / STANDARD / "01 Jenny.flac").exists()
    assert (root / "The Killers" / DELUXE / "01 Jenny.mp3").exists()
    assert not moved.exists()
    assert songs(store, member) == [(STANDARD, "Jenny"), (STANDARD, "Mr. Brightside")]
    assert tidy.history() == []


def test_a_file_changed_since_the_scan_stops_the_tidy_up(tmp_path, monkeypatch):
    tidy, root, _, _ = library(tmp_path, monkeypatch)
    (item,) = tidy.plan()["albums"]
    (root / "The Killers" / STANDARD / "02 Mr. Brightside.mp3").write_bytes(b"edited")
    with pytest.raises(ValueError):
        tidy.apply(item["id"], item["fingerprint"])
    assert (root / "The Killers" / STANDARD / "01 Jenny.flac").exists()
    assert len(tidy.store.library["albums"]) == 2


@pytest.mark.parametrize("change", ["corrupted", "missing"])
def test_a_kept_copy_changed_since_the_scan_stops_the_tidy_up(
    tmp_path, monkeypatch, change
):
    """The kept Deluxe "Mr. Brightside" justifies sending the Standard one to
    Trash; changed or gone after the review, nothing moves."""
    tidy, root, member, _ = library(tmp_path, monkeypatch)
    store = tidy.store
    before = songs(store)
    (item,) = tidy.plan()["albums"]
    kept = root / "The Killers" / DELUXE / "02 Mr. Brightside.mp3"
    duplicate = root / "The Killers" / STANDARD / "02 Mr. Brightside.mp3"
    assert any(r["kept"]["title"] == "Mr. Brightside" for r in item["remove"])
    if change == "corrupted":
        kept.write_bytes(b"corrupted externally after the review")
    else:
        kept.unlink()
    with pytest.raises(ValueError, match="Files changed"):
        tidy.apply(item["id"], item["fingerprint"])
    assert duplicate.exists()
    assert (root / "The Killers" / STANDARD / "01 Jenny.flac").exists()
    assert (root / "The Killers" / DELUXE / "01 Jenny.mp3").exists()
    assert songs(store) == before
    assert songs(store, member) == [(STANDARD, "Jenny"), (STANDARD, "Mr. Brightside")]
    assert tidy.history() == []
    with store.connect() as conn:
        assert not conn.execute(
            "SELECT 1 FROM documents WHERE name LIKE 'tidy:%'"
        ).fetchone()


def test_uncertain_quality_never_sends_a_copy_to_trash(tmp_path, monkeypatch):
    tracks = [
        # The kept copy's quality is unknown: the FLAC on the other edition
        # might be the better file, so it stays for review.
        song("Jenny", DELUXE, 1, codec="unknown", bitrate=None),
        song("Mr. Brightside", DELUXE, 2),
        song("Smile Like You Mean It", DELUXE, 3),
        song("Somebody Told Me", DELUXE, 4, bitrate=None),
        song("All These Things That I've Done", DELUXE, 5),
        song("Jenny", STANDARD, 1, codec="flac", bitrate=900),
        # The other copy's quality is unknown: it might be the better file.
        song("Mr. Brightside", STANDARD, 2, codec="", bitrate=None),
        # Lossy copies without a bitrate cannot be compared.
        song("Somebody Told Me", STANDARD, 4),
        # As good, with evidence: this one goes.
        song("Smile Like You Mean It", STANDARD, 3, bitrate=256),
    ]
    (item,) = planned(tmp_path, tracks, monkeypatch)
    assert [r["title"] for r in item["remove"]] == ["Smile Like You Mean It"]
    assert item["replace"] == []
    assert sorted(r["title"] for r in item["leftovers"]) == [
        "Jenny",
        "Mr. Brightside",
        "Somebody Told Me",
    ]


def test_an_interrupted_move_is_journaled_before_it_happens(tmp_path, monkeypatch):
    tidy, root, member, ids = library(tmp_path, monkeypatch)
    store = tidy.store
    before = songs(store)
    finish = Organisation.finish

    def interrupted(self, record, root):
        finish(self, record, root)
        raise OSError("interrupted after publishing")

    monkeypatch.setattr(Organisation, "finish", interrupted)
    (item,) = tidy.plan()["albums"]
    with pytest.raises(OSError):
        tidy.apply(item["id"], item["fingerprint"])
    monkeypatch.setattr(Organisation, "finish", finish)
    (entry,) = tidy.history()
    record = store.read_document("tidy:" + entry["id"])
    assert record["status"] == "stopped"
    assert "interrupted after publishing" in record["error"]
    assert len(record["moves"]) == 1 and len(record["removals"]) == 1
    assert not (root / "The Killers" / STANDARD / "01 Jenny.flac").exists()
    assert tidy.undo(entry["id"])["status"] == "undone"
    assert (root / "The Killers" / STANDARD / "01 Jenny.flac").exists()
    assert (root / "The Killers" / DELUXE / "01 Jenny.mp3").exists()
    assert songs(store) == before
    assert songs(store, member) == [(STANDARD, "Jenny"), (STANDARD, "Mr. Brightside")]


def test_an_interrupted_removal_and_membership_are_journaled(tmp_path, monkeypatch):
    tidy, root, member, ids = library(tmp_path, monkeypatch)
    store = tidy.store
    before = songs(store)
    finish = Recovery.finish
    calls = []

    def interrupted(self, record, root):
        finish(self, record, root)
        calls.append(record["id"])
        if len(calls) == 2:  # The second removal: Mr. Brightside's copy.
            raise OSError("interrupted after trashing")

    monkeypatch.setattr(Recovery, "finish", interrupted)
    (item,) = tidy.plan()["albums"]
    with pytest.raises(OSError):
        tidy.apply(item["id"], item["fingerprint"])
    monkeypatch.setattr(Recovery, "finish", finish)
    (entry,) = tidy.history()
    record = store.read_document("tidy:" + entry["id"])
    assert record["removals"] == calls
    assert not (root / "The Killers" / STANDARD / "02 Mr. Brightside.mp3").exists()
    tidy.undo(entry["id"])
    assert (root / "The Killers" / STANDARD / "02 Mr. Brightside.mp3").exists()
    assert songs(store) == before
    assert songs(store, member) == [(STANDARD, "Jenny"), (STANDARD, "Mr. Brightside")]


def test_memberships_are_journaled_with_the_change(tmp_path, monkeypatch):
    tidy, root, member, ids = library(tmp_path, monkeypatch)
    store = tidy.store
    save = Tidy.save

    def failing(self, record):
        # Appdata fails as soon as a listener has been given a kept copy.
        if record["memberships"]:
            raise OSError("appdata unavailable")
        save(self, record)

    monkeypatch.setattr(Tidy, "save", failing)
    (item,) = tidy.plan()["albums"]
    with pytest.raises(OSError):
        tidy.apply(item["id"], item["fingerprint"])
    monkeypatch.setattr(Tidy, "save", save)
    (entry,) = tidy.history()
    record = store.read_document("tidy:" + entry["id"])
    # The member now holds the kept album's song, and the journal says so.
    assert record["memberships"] == [[member, ids[DELUXE, "Mr. Brightside"]]]
    tidy.undo(entry["id"])
    assert songs(store, member) == [(STANDARD, "Jenny"), (STANDARD, "Mr. Brightside")]


def test_a_moved_file_takes_only_the_kept_albums_release_identity(
    tmp_path, monkeypatch
):
    from mutagen.flac import FLAC

    from server.scanner import LibraryScanner

    tidy, root, _, ids = library(tmp_path, monkeypatch)
    store = tidy.store
    donor = root / "The Killers" / STANDARD / "01 Jenny.flac"
    tags = FLAC(donor)
    tags["MUSICBRAINZ_ALBUMID"] = "donor-release"
    tags["DATE"] = "2005"
    tags.save()
    LibraryScanner(store, root).run()
    assert {
        t["musicbrainz_release_id"]
        for a in store.library["albums"]
        for t in a["tracks"]
        if t.get("musicbrainz_release_id")
    } == {"donor-release"}
    (item,) = tidy.plan()["albums"]
    record = tidy.apply(item["id"], item["fingerprint"])
    assert record["status"] == "applied"
    moved = root / "The Killers" / DELUXE / f"The Killers - {DELUXE} - 01 - Jenny.flac"
    # The kept album has no release ID and no year: neither comes along.
    assert "MUSICBRAINZ_ALBUMID" not in FLAC(moved) and "DATE" not in FLAC(moved)
    assert not inspect_file(moved, "x")[0].get("musicbrainz_release_id")
    for check in (store.library, None):
        if check is None:
            LibraryScanner(store, root).run()
        (album,) = store.library["albums"]
        assert album["title"] == DELUXE
        assert not any(t.get("musicbrainz_release_id") for t in album["tracks"])
    (entry,) = tidy.history()
    tidy.undo(entry["id"])
    assert FLAC(donor)["MUSICBRAINZ_ALBUMID"] == ["donor-release"]


@pytest.mark.parametrize("status", ["applying", "stopped", "undone"])
def test_restart_adopts_a_move_an_earlier_version_did_not_journal(
    tmp_path, monkeypatch, status
):
    tidy, root, member, ids = library(tmp_path, monkeypatch)
    store = tidy.store
    before = songs(store)
    record = stop_after_move(tidy, monkeypatch)
    # As an earlier build left it: the move it stopped on was never recorded
    # (and, for "undone", an Undo then put back everything else).
    for removal in record["removals"] if status == "undone" else ():
        tidy.recovery.restore(removal)
    legacy(tidy, record, moves=[], status=status)
    tidy.recover()
    entry = store.read_document("tidy:" + record["id"])
    assert entry["moves"] == record["moves"] and entry["status"] == "stopped"
    assert not entry.get("unresolved")
    assert entry["error"]
    tidy.undo(record["id"])
    assert (root / "The Killers" / STANDARD / "01 Jenny.flac").exists()
    assert songs(store) == before
    # A second restart finds nothing more to adopt.
    tidy.recover()
    assert store.read_document("tidy:" + record["id"])["status"] == "undone"


def test_a_failed_undo_keeps_its_reason_and_can_be_retried(tmp_path, monkeypatch):
    tidy, root, _, _ = library(tmp_path, monkeypatch)
    store = tidy.store
    before = songs(store)
    (item,) = tidy.plan()["albums"]
    record = tidy.apply(item["id"], item["fingerprint"])
    undo = Organisation.undo

    def refused(self, identity):
        raise ValueError("Organised file changed; undo stopped.")

    monkeypatch.setattr(Organisation, "undo", refused)
    with pytest.raises(ValueError):
        tidy.undo(record["id"])
    (entry,) = tidy.history()
    assert entry["status"] == "applied"
    assert entry["error"] == "Undo stopped: Organised file changed; undo stopped."
    monkeypatch.setattr(Organisation, "undo", undo)
    tidy.undo(record["id"])
    assert songs(store) == before


def test_undo_cancels_a_move_stopped_before_publishing(tmp_path, monkeypatch):
    tidy, root, _, _ = library(tmp_path, monkeypatch)
    store = tidy.store
    before = songs(store)
    monkeypatch.setattr(
        "server.organisation.inspect_file",
        lambda path, relative: ({"duration": 999}, None),
    )
    (item,) = tidy.plan()["albums"]
    with pytest.raises(ValueError, match="duration changed"):
        tidy.apply(item["id"], item["fingerprint"])
    monkeypatch.undo()
    monkeypatch.setenv("PIG_MUSIC_LIBRARY", str(root))
    (entry,) = tidy.history()
    assert entry["status"] == "stopped" and "duration changed" in entry["error"]
    (operation,) = store.read_document("tidy:" + entry["id"])["moves"]
    tidy.undo(entry["id"])
    assert store.read_document("organisation:" + operation)["status"] == "cancelled"
    assert not list(root.rglob(".pig-music-organise-*"))
    assert songs(store) == before


def legacy(tidy, record, **changes):
    """A tidy-up as the earlier implementation stored it: no journal marker."""
    record = {k: v for k, v in record.items() if k != "journal"} | changes
    tidy.save(record)
    return record


def stop_after_move(tidy, monkeypatch):
    """Apply until the first move publishes, then stop (as a failure would)."""
    finish = Organisation.finish

    def interrupted(self, record, root):
        finish(self, record, root)
        raise OSError("interrupted after publishing")

    monkeypatch.setattr(Organisation, "finish", interrupted)
    (item,) = tidy.plan()["albums"]
    with pytest.raises(OSError):
        tidy.apply(item["id"], item["fingerprint"])
    monkeypatch.setattr(Organisation, "finish", finish)
    (entry,) = tidy.history()
    return tidy.store.read_document("tidy:" + entry["id"])


def test_an_unjournaled_legacy_removal_is_surfaced_not_guessed(tmp_path, monkeypatch):
    tidy, root, member, ids = library(tmp_path, monkeypatch)
    store = tidy.store
    before = songs(store)
    (item,) = tidy.plan()["albums"]
    record = tidy.apply(item["id"], item["fingerprint"])
    # The earlier build lost the last removal's ID (Mr. Brightside's copy).
    orphan = record["removals"][-1]
    legacy(tidy, record, removals=record["removals"][:-1], status="stopped")
    tidy.recover()
    (entry,) = tidy.history()
    assert [(u["id"], u["kind"], u["title"]) for u in entry["unresolved"]] == [
        (orphan, "removal", "Mr. Brightside")
    ]
    # Undo puts back what was recorded, but does not claim the whole album.
    assert tidy.undo(record["id"])["status"] == "stopped"
    (entry,) = tidy.history()
    assert "1 other change" in entry["error"] and entry["unresolved"]
    trashed = root / "The Killers" / STANDARD / "02 Mr. Brightside.mp3"
    assert not trashed.exists()
    assert store.read_document("removed:" + orphan)["status"] == "removed"
    # A restart and a second Undo change nothing more.
    tidy.recover()
    assert tidy.undo(record["id"])["status"] == "stopped"
    assert not trashed.exists()
    # The owner says it belongs to this tidy-up: Undo now finishes.
    tidy.resolve(record["id"], orphan, include=True)
    assert tidy.undo(record["id"])["status"] == "undone"
    assert trashed.exists() and songs(store) == before
    assert songs(store, member) == [(STANDARD, "Jenny"), (STANDARD, "Mr. Brightside")]
    tidy.recover()
    assert tidy.history() == []


def test_a_removal_the_owner_says_is_not_part_of_it_stays_in_trash(
    tmp_path, monkeypatch
):
    tidy, root, _, _ = library(tmp_path, monkeypatch)
    store = tidy.store
    (item,) = tidy.plan()["albums"]
    record = tidy.apply(item["id"], item["fingerprint"])
    orphan = record["removals"][-1]
    legacy(tidy, record, removals=record["removals"][:-1], status="undone")
    # An undone tidy-up that may have missed a removal is reopened.
    tidy.recover()
    (entry,) = tidy.history()
    assert entry["status"] == "stopped" and entry["unresolved"]
    with pytest.raises(ValueError):
        tidy.resolve(record["id"], "not-a-candidate", include=True)
    tidy.resolve(record["id"], orphan, include=False)
    assert tidy.undo(record["id"])["status"] == "undone"
    assert store.read_document("removed:" + orphan)["status"] == "removed"
    tidy.recover()
    assert tidy.history() == []


def test_repeated_attempts_adopt_a_move_by_its_preceding_removal(tmp_path, monkeypatch):
    tidy, root, member, ids = library(tmp_path, monkeypatch)
    store = tidy.store
    before = songs(store)
    stopped = stop_after_move(tidy, monkeypatch)
    (move,) = stopped["moves"]
    base = {k: stopped[k] for k in ("album_id", "artist", "title")}
    legacy(tidy, stopped, moves=[])
    # Earlier attempts for the same album: one refused before changing
    # anything, one undone (its removal put back).
    early = legacy(
        tidy,
        base | {"id": "early", "time": 1, "status": "stopped"},
        memberships=[],
        removals=[],
        moves=[],
    )
    undone = legacy(
        tidy,
        base | {"id": "undone", "time": 2, "status": "undone"},
        memberships=[],
        removals=[],
        moves=[],
    )
    tidy.recover()
    adopted = store.read_document("tidy:" + stopped["id"])
    assert adopted["moves"] == [move] and not adopted.get("unresolved")
    for other in (early, undone):
        assert not store.read_document("tidy:" + other["id"]).get("unresolved")
    assert tidy.undo(stopped["id"])["status"] == "undone"
    assert songs(store) == before
    assert songs(store, member) == [(STANDARD, "Jenny"), (STANDARD, "Mr. Brightside")]


def test_a_move_two_attempts_could_own_is_unresolved_on_both(tmp_path, monkeypatch):
    tidy, root, _, _ = library(tmp_path, monkeypatch)
    store = tidy.store
    before = songs(store)
    stopped = stop_after_move(tidy, monkeypatch)
    (move,) = stopped["moves"]
    (removal,) = stopped["removals"]
    base = {k: stopped[k] for k in ("album_id", "artist", "title")}
    # Neither attempt recorded the removal that led to the move.
    legacy(tidy, stopped, moves=[], removals=[])
    other = legacy(
        tidy,
        base | {"id": "other", "time": 1, "status": "undone"},
        memberships=[],
        removals=[],
        moves=[],
    )
    tidy.recover()
    for identity in (stopped["id"], other["id"]):
        entry = store.read_document("tidy:" + identity)
        assert entry["status"] == "stopped"
        assert {u["id"] for u in tidy.pending(entry)} == {move, removal}
    # Neither Undo claims success while those are unaccounted for.
    assert tidy.undo(other["id"])["status"] == "stopped"
    assert tidy.undo(stopped["id"])["status"] == "stopped"
    # Once one tidy-up owns them, the other no longer lists them.
    tidy.resolve(stopped["id"], move, include=True)
    tidy.resolve(stopped["id"], removal, include=True)
    assert tidy.pending(store.read_document("tidy:" + other["id"])) == []
    assert tidy.undo(other["id"])["status"] == "undone"
    assert tidy.undo(stopped["id"])["status"] == "undone"
    assert songs(store) == before


def test_undo_cancels_a_removal_whose_trash_link_failed(tmp_path, monkeypatch):
    import os

    tidy, root, member, ids = library(tmp_path, monkeypatch)
    store = tidy.store
    before = songs(store)
    link = os.link

    def unsupported(source, target, *args, **kwargs):
        if ".pig-music-trash/" in str(target) and "/organisation/" not in str(target):
            raise OSError(95, "Operation not supported")
        return link(source, target, *args, **kwargs)

    monkeypatch.setattr("server.recovery.os.link", unsupported)
    (item,) = tidy.plan()["albums"]
    with pytest.raises(OSError):
        tidy.apply(item["id"], item["fingerprint"])
    (entry,) = tidy.history()
    assert "Operation not supported" in entry["error"]
    (removal,) = store.read_document("tidy:" + entry["id"])["removals"]
    assert store.read_document("removed:" + removal)["status"] == "removing"
    kept = root / "The Killers" / DELUXE / "01 Jenny.mp3"
    assert kept.exists()
    # A restart retries the removal and fails the same way; nothing is lost.
    tidy.recovery.recover()
    tidy.organisation.recover()
    tidy.recover()
    # Undo, while links still fail, cancels the removal without the link.
    assert tidy.undo(entry["id"])["status"] == "undone"
    assert store.read_document("removed:" + removal)["status"] == "cancelled"
    assert kept.exists() and songs(store) == before
    assert songs(store, member) == [(STANDARD, "Jenny"), (STANDARD, "Mr. Brightside")]
    tidy.recovery.recover()
    assert songs(store) == before


def test_a_cancelled_removal_keeps_its_safeguards(tmp_path, monkeypatch):
    import os

    tidy, root, _, ids = library(tmp_path, monkeypatch)
    store = tidy.store
    link = os.link

    def unsupported(source, target, *args, **kwargs):
        if ".pig-music-trash/" in str(target):
            raise OSError(95, "Operation not supported")
        return link(source, target, *args, **kwargs)

    monkeypatch.setattr("server.recovery.os.link", unsupported)
    with pytest.raises(OSError):
        tidy.recovery.remove(ids[DELUXE, "Smile Like You Mean It"], exclude=False)
    (record,) = [r for r in tidy.recovery.list() if r["status"] == "removing"]
    path = root / record["original"]
    # A changed original is neither cancelled nor removed.
    path.write_bytes(b"edited")
    with pytest.raises(ValueError):
        tidy.recovery.settle(record["id"])
    assert store.read_document("removed:" + record["id"])["status"] == "removing"
    # A missing original is not cancelled either.
    path.unlink()
    with pytest.raises(ValueError):
        tidy.recovery.settle(record["id"])
    assert store.read_document("removed:" + record["id"])["status"] == "removing"


@pytest.mark.parametrize("kept", [False, True])
def test_downloads_not_yet_in_the_music_folder(tmp_path, monkeypatch, kept):
    """A downloaded copy not yet published lives in the download workspace
    on the library share. Trashing it reported the SMB mount unavailable
    (the marker is the library's, not the workspace's); now it goes to
    Trash like any copy, export carries on without it, and Undo puts it
    back. It never stands in for a music-folder copy: that stays for review."""
    import hashlib

    from server.exporter import Exporter

    tidy, root, member, ids = library(tmp_path, monkeypatch)
    store = tidy.store
    (store.directory / "library-export.json").write_text(
        json.dumps(
            {
                "enabled": True,
                "library_id": "fixture",
                "marker_path": ".pig-music-library.json",
            }
        )
    )
    (root / ".pig-music-library.json").write_text(json.dumps({"library_id": "fixture"}))
    work = root / ".pig-music-work"
    monkeypatch.setenv("PIG_MUSIC_DOWNLOAD_ROOT", str(work))

    def acquire(identity, title, album, number, published):
        relative = f"The Killers/{album}/{number:02} {title}.mp3"
        path = (root if published else work / "library") / relative
        audio(path, title, album, number, "mp3_320")
        stat = path.stat()
        track = inspect_file(path, relative)[0] | {
            "id": identity,
            "relative_path": relative,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size_bytes": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "available": True,
        }
        if published:
            track["export_relative_path"] = relative
        with store.connect() as conn:
            conn.execute(
                "INSERT INTO acquired VALUES (?,?)", (identity, json.dumps(track))
            )
        return path

    staged = acquire("staged-smile", "Smile Like You Mean It", STANDARD, 3, False)
    if not kept:
        # One more published song makes the deluxe edition the kept one.
        acquire("deluxe-somebody", "Somebody Told Me", DELUXE, 4, True)
    store.refresh_library()
    before = songs(store)
    (item,) = tidy.plan()["albums"]
    assert item["keep"]["title"] == (STANDARD if kept else DELUXE)
    nas_smile = root / "The Killers" / DELUXE / "03 Smile Like You Mean It.mp3"
    if kept:
        smile = {"staged-smile", ids[DELUXE, "Smile Like You Mean It"]}
        assert not smile & {r["id"] for r in item["remove"]}
        (waiting,) = [r for r in item["leftovers"] if r["id"] in smile]
        assert waiting["reason"] == "the kept copy is not in the music folder yet"
        assert tidy.apply(item["id"], item["fingerprint"])["status"] == "applied"
        assert staged.exists() and nas_smile.exists()
        return
    assert "staged-smile" in {r["id"] for r in item["remove"]}
    record = tidy.apply(item["id"], item["fingerprint"])
    assert record["status"] == "applied"
    assert not staged.exists() and nas_smile.exists()
    (removal,) = [r for r in tidy.recovery.list() if r["track_id"] == "staged-smile"]
    assert (root / removal["root"] / removal["trash"]).is_file()
    # The trashed download does not hold up publishing.
    Exporter(store, root).process()
    assert store.read_document("library-export-status")["status"] == "completed"
    tidy.undo(record["id"])
    assert staged.exists() and songs(store) == before
