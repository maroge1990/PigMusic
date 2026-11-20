import json
import time

import pytest
import plistlib
from server.store import Store
from server.playlists import Playlists


def export():
    return plistlib.dumps(
        {
            "Library Persistent ID": "library",
            "Tracks": {
                "1": {
                    "Track ID": 1,
                    "Name": "One",
                    "Artist": "Artist",
                    "Album Artist": "Artist",
                    "Album": "Studio",
                    "Total Time": 200000,
                },
                "2": {
                    "Track ID": 2,
                    "Name": "One",
                    "Artist": "Artist",
                    "Album Artist": "Artist",
                    "Album": "Live",
                    "Total Time": 250000,
                },
            },
            "Playlists": [
                {
                    "Name": "Favourites",
                    "Playlist Persistent ID": "playlist",
                    "Playlist Items": [
                        {"Track ID": 2},
                        {"Track ID": 1},
                        {"Track ID": 2},
                    ],
                }
            ],
        }
    )


def test_playlist_order_duplicates_persistence_and_missing_review(tmp_path):
    track = {
        "id": "local",
        "artist": "Artist",
        "album_artist": "Artist",
        "title": "One",
        "album": "Studio",
        "duration": 200,
    }
    (tmp_path / "library-snapshot.json").write_text(json.dumps({"tracks": [track]}))
    store = Store(tmp_path)
    store.import_apple(export())
    playlists = Playlists(store)
    card = playlists.list()[0]
    detail = playlists.detail(card["id"])
    assert [e["album"] for e in detail["entries"]] == ["Live", "Studio", "Live"]
    assert [e["status"] for e in detail["entries"]] == [
        "review",
        "downloaded",
        "review",
    ]
    assert detail["available_count"] == 1
    store.import_apple(export())
    assert len(playlists.list()) == 1
    assert Playlists(Store(tmp_path)).detail(card["id"])["track_count"] == 3
    review = playlists.review(card["id"])
    assert review["stats"]["selected"] == 2
    assert store.queue()["items"] == []
    # Arrival updates the playlist without changing its order or imported membership.
    live = {**track, "id": "live", "album": "Live", "duration": 250}
    (tmp_path / "library-snapshot.json").write_text(
        json.dumps({"tracks": [track, live]})
    )
    store.refresh_library()
    assert playlists.detail(card["id"])["available_count"] == 3


def test_missing_snapshot_files_are_not_playlist_matches(tmp_path):
    (tmp_path / "library-snapshot.json").write_text(
        json.dumps(
            {
                "tracks": [
                    {
                        "id": "lost",
                        "artist": "Artist",
                        "album_artist": "Artist",
                        "title": "One",
                        "album": "Studio",
                        "duration": 200,
                        "available": False,
                    }
                ]
            }
        )
    )
    store = Store(tmp_path)
    store.import_apple(export())
    playlists = Playlists(store)
    assert playlists.detail(playlists.list()[0]["id"])["available_count"] == 0


@pytest.mark.loop_timing
def test_bulk_append_near_the_limit_stays_fast(tmp_path):
    """Codex (#249): 500 songs into a 9,500-entry playlist compared every pair
    and held the lifecycle lock for ~20 s; only songs with the same context
    are compared now."""
    store = Store(tmp_path)
    playlists = Playlists(store)
    created = playlists.create("Big")

    def song(i):
        return {
            "title": f"Song {i}",
            "artist": f"Artist {i % 97}",
            "album": f"Album {i % 400}",
            "duration": 200,
        }

    with store.connect() as conn:
        payload = json.loads(
            conn.execute(
                "SELECT payload FROM playlists WHERE id=?", (created["id"],)
            ).fetchone()[0]
        )
        payload["tracks"] = [song(i) for i in range(9500)]
        conn.execute(
            "UPDATE playlists SET payload=? WHERE id=?",
            (json.dumps(payload), created["id"]),
        )
    # Half are already in (deduplicated), half are new.
    batch = [song(i) for i in range(9250, 9750)]
    started = time.perf_counter()
    playlists.add_many(created["id"], batch)
    elapsed = time.perf_counter() - started
    with store.connect() as conn:
        saved = json.loads(
            conn.execute(
                "SELECT payload FROM playlists WHERE id=?", (created["id"],)
            ).fetchone()[0]
        )
    assert len(saved["tracks"]) == 9750
    assert elapsed < 2, f"add_many took {elapsed:.1f}s"


def seed(store, playlist_id, tracks):
    with store.connect() as conn:
        payload = json.loads(
            conn.execute(
                "SELECT payload FROM playlists WHERE id=?", (playlist_id,)
            ).fetchone()[0]
        )
        payload["tracks"] = tracks
        conn.execute(
            "UPDATE playlists SET payload=? WHERE id=?",
            (json.dumps(payload), playlist_id),
        )


def saved_tracks(store, playlist_id):
    with store.connect() as conn:
        return json.loads(
            conn.execute(
                "SELECT payload FROM playlists WHERE id=?", (playlist_id,)
            ).fetchone()[0]
        )["tracks"]


@pytest.mark.loop_timing
def test_bulk_append_into_one_title_and_artist_stays_fast(tmp_path):
    """Codex (#268): 9,500 entries and 500 new songs, all one title, artist
    and album with distinct recording IDs, held the lifecycle lock for ~18 s;
    within a context only songs whose IDs can agree are compared now."""
    store = Store(tmp_path)
    playlists = Playlists(store)
    created = playlists.create("One song, many recordings")

    def song(i):
        return {
            "title": "Same",
            "artist": "Artist",
            "album": "Album",
            "duration": 200,
            "musicbrainz_release_id": "release",
            "musicbrainz_recording_id": f"recording-{i}",
        }

    seed(store, created["id"], [song(i) for i in range(9500)])
    # Half are already in (deduplicated), half are new.
    batch = [song(i) for i in range(9250, 9750)]
    started = time.perf_counter()
    playlists.add_many(created["id"], batch)
    elapsed = time.perf_counter() - started
    assert len(saved_tracks(store, created["id"])) == 9750
    assert elapsed < 2, f"add_many took {elapsed:.1f}s"


def test_bulk_append_keeps_every_same_song_decision(tmp_path):
    """The indexed append (#268) adds exactly the songs the plain rule would:
    each new song is compared by `same_song` with every entry and every song
    added before it. Mixed inputs share a few contexts, with and without
    release and recording IDs and lengths, and include live versions."""
    import random
    from server.matching import same_song

    rng = random.Random(268)

    def song():
        track = {
            "title": rng.choice(["Same", "Same (Live)", "Same - 2011 Remaster"]),
            "artist": rng.choice(["Artist", "ARTIST", "Other"]),
            "album": rng.choice(["Album", "Live at Leeds"]),
        }
        if rng.random() < 0.7:
            track["duration"] = rng.choice([0, 200, 203, 206, 212, 250])
        for key, pool in (
            ("musicbrainz_release_id", ["rel-a", "rel-b", "", None]),
            ("musicbrainz_recording_id", ["rec-a", "rec-b", "rec-c", "", None]),
        ):
            if rng.random() < 0.75:
                track[key] = rng.choice(pool)
        if rng.random() < 0.2:
            track["album_artist"] = rng.choice(["Artist", "Various Artists"])
        return track

    store = Store(tmp_path)
    playlists = Playlists(store)
    for attempt in range(60):
        existing = [song() for _ in range(rng.randint(0, 30))]
        batch = [song() for _ in range(rng.randint(1, 30))]
        expected = list(existing)
        for track in batch:
            if not any(same_song(t, track) for t in expected):
                expected.append(track)
        created = playlists.create(f"Round {attempt}")
        seed(store, created["id"], existing)
        playlists.add_many(created["id"], batch)
        assert saved_tracks(store, created["id"]) == expected, attempt
