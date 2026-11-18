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
