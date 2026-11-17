"""The showcase server's library plan (scripts/serve_showcase_server.py):
checked as data, without FFmpeg or a server."""

from collections import Counter

from scripts import serve_showcase_server as showcase
from server.library import build_library
from server.matching import LibraryIndex


def test_the_library_plan_fills_every_collection():
    tracks = showcase.plan()
    assert len({track["id"] for track in tracks}) == len(tracks)
    assert len({track["relative_path"] for track in tracks}) == len(tracks)
    assert 200 <= len(tracks) <= 240
    library = build_library({"tracks": tracks})
    stats = library["stats"]
    assert 20 <= len(library["albums"]) <= 28
    for kind in ("albums", "compilations", "soundtracks", "musicals"):
        assert stats[kind] > 0, kind
    assert max(artist["album_count"] for artist in library["artists"]) >= 4
    assert any(
        len({track["disc_number"] for track in album["tracks"]}) == 2
        for album in library["albums"]
    )
    singles = [album for album in library["albums"] if album["track_count"] == 1]
    assert 2 <= len(singles) <= 4
    long_titles = {track["title"] for track in tracks} | {
        album["title"] for album in library["albums"]
    }
    assert sum(len(title) >= 40 for title in long_titles) >= 3
    assert all(20 <= track["duration"] <= 720 for track in tracks)
    typical = sum(120 <= track["duration"] <= 360 for track in tracks)
    assert typical >= len(tracks) * 0.7
    assert all(1950 <= track["year"] <= 2026 for track in tracks)


def test_the_seeded_account_fits_the_library():
    tracks = showcase.plan()
    assert sum(count for _, count in showcase.PLAYLISTS) <= len(tracks)
    counts = sorted(count for _, count in showcase.PLAYLISTS)
    assert counts[0] == 0 and counts[-1] >= 40
    assert all(len(name) <= 100 for name, _ in showcase.PLAYLISTS)
    assert max(len(name) for name, _ in showcase.PLAYLISTS) >= 60
    assert 20 <= showcase.FAVOURITES <= 30
    members = [t for t in tracks if t["album_artist"] in showcase.MEMBER_ARTISTS]
    assert showcase.MEMBER_PLAYLIST[1] <= len(members) < len(tracks)


def test_downloads_show_every_state_for_songs_not_in_the_library():
    index = LibraryIndex(build_library({"tracks": showcase.plan()}))
    states, kinds = Counter(), Counter()
    for songs, song_states, _ in showcase.request_plan():
        kinds["album" if len(songs) > 1 else "song"] += 1
        for song, state in zip(songs, song_states):
            assert index.match({**song, "status": ""}).track is None, song["title"]
            states[state.split(":")[0]] += 1
    assert set(states) == {
        "waiting",
        "searching",
        "downloading",
        "paused",
        "failed",
        "completed",
    }
    assert kinds["album"] >= 3 and kinds["song"] >= 3
