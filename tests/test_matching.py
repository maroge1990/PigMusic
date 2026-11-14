import json

from server.catalogue import Catalogue
from server.library import build_library, build_migration
from server.matching import LibraryIndex
from server.playlists import Playlists
from server.recovery import Recovery
from server.store import Store


def song(**extra):
    return {
        "id": "saved",
        "artist": "Artist",
        "album_artist": "Artist",
        "title": "Song",
        "album": "Album",
        "duration": 100,
        "year": 2020,
        "disc_number": 1,
        "track_number": 1,
        "musicbrainz_release_id": "edition-a",
        "musicbrainz_recording_id": "recording-a",
        **extra,
    }


def test_all_consumers_agree_on_conflicting_editions_and_recordings(tmp_path):
    (tmp_path / "library-snapshot.json").write_text(json.dumps({"tracks": [song()]}))
    store = Store(tmp_path)
    playlists = Playlists(store)
    for requested in (
        song(musicbrainz_release_id="edition-b", explicit_edition=True),
        song(musicbrainz_recording_id="recording-b", explicit_edition=True),
    ):
        migration = build_migration({"tracks": [requested]}, store.library, set())
        assert migration["albums"][0]["tracks"][0]["status"] == "review"
        assert Catalogue(store).presence([dict(requested)])[0]["status"] == "review"
        playlist = playlists.create("Test")
        assert (
            playlists.add(playlist["id"], requested)["entries"][0]["status"] == "review"
        )


def test_all_consumers_find_the_same_song_on_another_edition(tmp_path):
    (tmp_path / "library-snapshot.json").write_text(json.dumps({"tracks": [song()]}))
    store = Store(tmp_path)
    playlists = Playlists(store)
    for requested in (
        song(musicbrainz_release_id="edition-b"),
        song(album="Album (Deluxe Edition)", musicbrainz_release_id=None),
        song(album="Greatest Hits", album_artist="Various Artists", duration=103),
        song(title="Song - 2011 Remaster", album="Album (Remastered)"),
        song(title="Song (feat. Guest)", artist="Artist feat. Guest"),
    ):
        migration = build_migration({"tracks": [requested]}, store.library, set())
        assert migration["albums"][0]["tracks"][0]["status"] == "downloaded"
        presence = Catalogue(store).presence([dict(requested)])[0]
        assert presence["status"] == "downloaded" and presence["saved_id"] == "saved"
        playlist = playlists.create("Test " + requested["title"] + requested["album"])
        entry = playlists.add(playlist["id"], requested)["entries"][0]
        assert entry["status"] == "downloaded" and entry["saved_id"] == "saved"


def test_other_recordings_stay_distinct_across_editions():
    index = LibraryIndex(build_library({"tracks": [song()]}))
    for requested in (
        song(title="Song (Live)", musicbrainz_release_id=None),
        song(title="Song - Acoustic Version"),
        song(title="Song (Remix)"),
        song(album="Live at Leeds"),
        song(album="Demos"),
        song(duration=160),
        song(artist="Someone Else", album_artist="Someone Else"),
        # A known, different recording is another recording.
        song(album="Greatest Hits", musicbrainz_recording_id="recording-b"),
        # A live release type names a live recording, whatever the album title.
        song(album="Wembley 1986", recording_kind=["live"]),
    ):
        assert index.match(requested).status != "downloaded", requested


def test_edition_match_prefers_the_requested_album_then_the_artists_own():
    tracks = [
        song(id="hits", album="Greatest Hits", album_artist="Various Artists"),
        song(id="studio", album="Album", musicbrainz_release_id="edition-a"),
        song(id="deluxe", album="Album (Deluxe)", musicbrainz_release_id="edition-d"),
        song(id="other", album="Other Album", track_number=2),
    ]
    index = LibraryIndex(build_library({"tracks": tracks}))
    deluxe = song(album="Album (Super Deluxe)", musicbrainz_release_id="x")
    assert index.match(deluxe).track["id"] in {"studio", "deluxe"}
    compiled = song(album="Now 45", album_artist="Various Artists")
    assert index.match(compiled).track["id"] in {"studio", "deluxe", "other"}


def test_duplicate_unknown_bytes_require_review_and_identical_bytes_match():
    tracks = [song(), song(id="second")]
    assert (
        LibraryIndex(build_library({"tracks": tracks}))
        .match(song(explicit_edition=True))
        .kind
        == "conflict"
    )
    # Without an explicit edition either copy of the same song will do.
    assert LibraryIndex(build_library({"tracks": tracks})).match(song()).track
    tracks = [{**t, "sha256": "same-bytes"} for t in tracks]
    assert (
        LibraryIndex(build_library({"tracks": tracks})).match(song()).status
        == "downloaded"
    )


def test_library_revision_is_content_based_across_restarts(tmp_path):
    snapshot = tmp_path / "library-snapshot.json"
    snapshot.write_text(json.dumps({"tracks": [song()]}))
    store = Store(tmp_path)
    original = store.library_revision
    store.refresh_library()
    assert store.library_revision == original
    assert Store(tmp_path).library_revision == original
    snapshot.write_text(json.dumps({"tracks": [song(title="Changed title")]}))
    assert Store(tmp_path).library_revision != original


def test_edition_anchor_preserves_links_and_preferences_after_restart(tmp_path):
    path = tmp_path / "library-snapshot.json"
    path.write_text(json.dumps({"tracks": [song()]}))
    store = Store(tmp_path)
    original = store.library["albums"][0]["id"]
    store.categorise(original, "soundtrack")
    store.quality_override("album", original, "lossless")
    path.write_text(
        json.dumps(
            {"tracks": [song(id="second", musicbrainz_release_id="edition-b"), song()]}
        )
    )
    restarted = Store(tmp_path)
    assert len(restarted.library["albums"]) == 2
    first = next(a for a in restarted.library["albums"] if a["id"] == original)
    assert first["tracks"][0]["id"] == "saved" and first["category"] == "soundtrack"
    assert restarted.quality_policy(song())["preferred"] == "lossless"
    # A catalogue reference may reuse an ID but cannot inherit another edition's override.
    assert (
        restarted.quality_policy(song(musicbrainz_release_id="edition-b"))["preferred"]
        == "mp3_320"
    )
    assert (
        restarted.quality_policy(song(id="second", musicbrainz_release_id="edition-b"))[
            "preferred"
        ]
        == "mp3_320"
    )


def test_removal_does_not_cancel_other_edition_and_exclusions_restore_independently(
    tmp_path, monkeypatch
):
    root = tmp_path / "music"
    root.mkdir()
    monkeypatch.setenv("PIG_MUSIC_LIBRARY", str(root))
    tracks = [
        song(relative_path="a.mp3"),
        song(id="second", musicbrainz_release_id="edition-b", relative_path="b.mp3"),
    ]
    for track in tracks:
        (root / track["relative_path"]).write_bytes(b"original " + track["id"].encode())
    data = tmp_path / "data"
    data.mkdir()
    (data / "library-snapshot.json").write_text(json.dumps({"tracks": tracks}))
    store = Store(data)
    explicit = [{**t, "status": "missing", "explicit_edition": True} for t in tracks]
    store.submit_catalogue(explicit)
    recovery = Recovery(store)
    first = recovery.remove("saved")
    states = {t["musicbrainz_release_id"]: t["status"] for t in store.queue()["items"]}
    assert states == {"edition-a": "cancelled", "edition-b": "waiting_for_sources"}
    recovery.remove("second")
    recovery.restore(first["id"])
    assert store.submit_catalogue([explicit[1]])["skipped"] == 1


def test_discover_matches_saved_albums_with_the_shared_title_key():
    """#170: discover kept its own brackets-only title_key, so "Rumours -
    2004 Remaster" never matched the release group "Rumours"."""
    from server.routes import discover

    key = discover.title_key
    assert key("Rumours - 2004 Remaster") == key("Rumours (Deluxe)") == key("Rumours")
    assert key("Rumours (Live)") != key("Rumours")
