import json
import plistlib
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from server.app import create_app
from server.apple_export import build_migration, parse_apple
from server.library import build_library
from server.store import Store


def test_queue_requires_conflict_decisions_and_survives_restart(tmp_path):
    (tmp_path / "library-snapshot.json").write_text(json.dumps({"tracks": [track()]}))
    store = Store(tmp_path)
    result = store.import_apple(
        apple_export(
            [track(id="different", album="Live"), track(id="new", title="Two")]
        )
    )
    items = [t for a in result["albums"] for t in a["tracks"]]
    store.update_selection([t["id"] for t in items], True)
    try:
        store.submit_migration()
        assert False, "Conflicts must block the entire batch"
    except ValueError:
        assert store.queue()["items"] == []
    conflict = next(t for t in items if t["status"] == "review")
    store.decide(conflict["id"], "skip")
    assert store.submit_migration() == {
        "created": 1,
        "existing": 0,
        "skipped": 1,
        "downloads_enabled": False,
    }
    restarted = Store(tmp_path)
    assert restarted.submit_migration()["existing"] == 1
    queued = restarted.queue()["items"][0]
    assert queued["title"] == "Two"
    assert queued["status"] == "waiting_for_sources"
    restarted.cancel_request(queued["id"])
    assert Store(tmp_path).queue()["items"][0]["status"] == "cancelled"
    assert restarted.submit_migration()["created"] == 1


def test_simultaneous_import_submissions_do_not_duplicate_requests(tmp_path):
    store = Store(tmp_path)
    result = store.import_apple(apple_export([track(id="new", title="New song")]))
    store.update_selection([result["albums"][0]["tracks"][0]["id"]], True)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(lambda _: Store(tmp_path).submit_migration(), range(2))
        )
    assert sum(r["created"] for r in results) == 1
    assert sum(r["existing"] for r in results) == 1
    assert len(store.queue()["items"]) == 1


def test_import_replacement_clears_decisions_but_preserves_queue(tmp_path):
    (tmp_path / "library-snapshot.json").write_text(json.dumps({"tracks": [track()]}))
    store = Store(tmp_path)
    result = store.import_apple(apple_export([track(id="different", album="Live")]))
    item = result["albums"][0]["tracks"][0]
    store.update_selection([item["id"]], True)
    store.decide(item["id"], "requested")
    assert store.submit_migration()["created"] == 1
    store.import_apple(apple_export([track(id="different", album="Live")]))
    assert store.migration()["albums"][0]["tracks"][0]["decision"] is None
    assert len(store.queue()["items"]) == 1


def track(id="local-1", title="One", artist="Artist A", album="Studio", **extra):
    return {
        "id": id,
        "title": title,
        "artist": artist,
        "album": album,
        "album_artist": artist,
        "duration": 200,
        "compilation": False,
        **extra,
    }


def apple_export(tracks):
    source = {
        "Library Persistent ID": "fixture",
        "Tracks": {},
        "Playlists": [{"Name": "Private favourites"}],
    }
    for index, t in enumerate(tracks):
        source["Tracks"][str(index)] = {
            "Name": t["title"],
            "Artist": t["artist"],
            "Album": t["album"],
            "Album Artist": t.get("album_artist", t["artist"]),
            "Compilation": t.get("compilation", False),
            "Persistent ID": t["id"],
            "Total Time": int(t.get("duration", 200) * 1000),
        }
    return plistlib.dumps(source)


def test_only_multi_artist_compilations_are_segregated():
    library = build_library(
        {
            "tracks": [
                track(album="Greatest Hits", compilation=True),
                track(
                    id="local-2",
                    artist="Artist B",
                    album="Summer",
                    album_artist="Various Artists",
                    compilation=True,
                ),
                track(
                    id="local-3",
                    artist="Artist C",
                    album="Summer",
                    album_artist="Various Artists",
                    compilation=True,
                ),
            ]
        }
    )
    assert library["stats"] == {
        "tracks": 3,
        "artists": 1,
        "albums": 1,
        "compilations": 1,
        "soundtracks": 0,
        "musicals": 0,
    }
    assert (
        next(a for a in library["artists"] if a["name"] == "Artist A")[
            "compilation_only"
        ]
        is False
    )
    assert (
        next(a for a in library["artists"] if a["name"] == "Artist B")[
            "compilation_only"
        ]
        is True
    )
    assert (
        next(a for a in library["albums"] if a["title"] == "Greatest Hits")[
            "compilation"
        ]
        is False
    )


def test_same_title_on_different_release_is_not_downloaded():
    library = build_library({"tracks": [track()]})
    source = parse_apple(
        apple_export(
            [
                track(id="source-saved"),
                track(id="source-other", album="Compilation"),
                track(id="source-live", title="One (Live)"),
                track(id="source-duration", duration=500),
            ]
        )
    )
    migration = build_migration(source, library, set())
    statuses = {
        t["source_track_id"]: t["status"]
        for a in migration["albums"]
        for t in a["tracks"]
    }
    # A compilation copy is the same song; a live title or another length is not.
    assert statuses == {
        "0": "downloaded",
        "1": "downloaded",
        "2": "missing",
        "3": "review",
    }
    assert migration["stats"]["selected"] == 0


def test_album_artist_counts_tracks_with_other_performers_without_duplicates():
    library = build_library(
        {
            "tracks": [
                track(id="a", artist="Performer", album_artist="Composer"),
                track(id="b", artist="Composer", album_artist="Composer"),
            ]
        }
    )
    composer = next(a for a in library["artists"] if a["name"] == "Composer")
    assert composer["album_count"] == 1
    assert composer["track_count"] == 2


def test_albums_sort_when_string_and_integer_years_share_an_artist():
    # "The Band" and "Band" share a sort key; request/mix-cache years are strings.
    library = build_library(
        {
            "tracks": [
                track(id="a", album_artist="The Band", album="Later", year=2021),
                track(id="b", album_artist="Band", album="Earlier", year="2019"),
                track(id="c", album_artist="Band", album="Unknown", year=""),
            ]
        }
    )
    assert [a["title"] for a in library["albums"]] == ["Unknown", "Earlier", "Later"]


def test_soundtracks_are_separate_and_manual_categories_persist(tmp_path):
    snapshot = {
        "tracks": [
            track(
                album="Interstellar: Original Motion Picture Soundtrack",
                artist="Composer",
            ),
            track(id="game", album="Game Medley", artist="Composer"),
        ]
    }
    (tmp_path / "library-snapshot.json").write_text(json.dumps(snapshot))
    store = Store(tmp_path)
    assert store.library["stats"]["soundtracks"] == 1
    assert store.library["stats"]["artists"] == 1
    game = next(a for a in store.library["albums"] if a["title"] == "Game Medley")
    store.categorise(game["id"], "soundtrack")
    restarted = Store(tmp_path)
    assert restarted.library["stats"]["soundtracks"] == 2
    assert restarted.library["stats"]["artists"] == 0
    restarted.categorise(game["id"], "auto")
    assert restarted.library["stats"]["soundtracks"] == 1


def test_import_selection_persists_and_plan_never_enables_downloads(tmp_path):
    snapshot = {"tracks": [track()]}
    source_path = tmp_path / "library-snapshot.json"
    source_path.write_text(json.dumps(snapshot))
    original = source_path.read_bytes()
    store = Store(tmp_path)
    result = store.import_apple(
        apple_export([track(id="missing", title='"Heroes" (2017 Remaster)')])
    )
    item = result["albums"][0]["tracks"][0]
    assert item["title"] == '"Heroes" (2017 Remaster)'
    store.update_selection([item["id"]], True)
    restarted = Store(tmp_path)
    assert restarted.migration()["stats"]["selected"] == 1
    assert restarted.plan()["downloads_enabled"] is False
    assert restarted.plan()["mode"] == "review_only"
    assert restarted.plan()["tracks"][0]["title"] == item["title"]
    assert source_path.read_bytes() == original


def test_unknown_or_owned_track_cannot_be_selected(tmp_path):
    (tmp_path / "library-snapshot.json").write_text(json.dumps({"tracks": [track()]}))
    app = create_app(tmp_path)
    with TestClient(app) as client:
        imported = client.post(
            "/api/v1/admin/migration/import", content=apple_export([track(id="owned")])
        )
        assert imported.status_code == 200
        owned = imported.json()["albums"][0]["tracks"][0]["id"]
        for invalid_id in (owned, "not-a-track"):
            response = client.post(
                "/api/v1/admin/migration/selection",
                json={"track_ids": [invalid_id], "selected": True},
            )
            assert response.status_code == 400
        assert client.get("/api/v1/admin/migration/plan").json()["track_count"] == 0
        assert client.get("/api/health").json()["downloads_enabled"] is False


def test_invalid_import_preserves_review_and_cross_origin_writes_rejected(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/admin/migration/import", content=apple_export([track()])
        )
        assert response.status_code == 200
        failed = client.post(
            "/api/v1/admin/migration/import", content=b"not an Apple export"
        )
        assert failed.status_code == 400
        assert client.get("/api/v1/admin/migration").json()["stats"]["tracks"] == 1
        forbidden = client.post(
            "/api/v1/admin/migration/selection",
            headers={"Origin": "https://unrelated.example"},
            json={"track_ids": [], "selected": True},
        )
        assert forbidden.status_code == 403


def test_search_includes_compilation_only_artists(tmp_path):
    (tmp_path / "library-snapshot.json").write_text(
        json.dumps(
            {
                "tracks": [
                    track(
                        artist="Artist B",
                        album="Summer",
                        album_artist="Various Artists",
                        compilation=True,
                    ),
                ]
            }
        )
    )
    with TestClient(create_app(tmp_path)) as client:
        result = client.get("/api/v1/admin/library").json()
        artist = next(a for a in result["artists"] if a["name"] == "Artist B")
        assert artist["compilation_only"] is True
        assert [a["title"] for a in result["albums"]] == ["Summer"]


def test_owner_examples_classify_into_their_own_sections():
    from server.library import classify

    cases = [
        (
            "Hamilton (Original Broadway Cast Recording)",
            "Lin-Manuel Miranda",
            [],
            "musical",
        ),
        ("Hamilton", "Original Broadway Cast of Hamilton", [], "musical"),
        (
            "The Book of Mormon (Original Broadway Cast Recording)",
            "Trey Parker",
            [],
            "musical",
        ),
        (
            "Westworld: Season 1 (Music from the HBO Series)",
            "Ramin Djawadi",
            [],
            "soundtrack",
        ),
        ("Westworld: Season 2", "Ramin Djawadi", ["Soundtrack"], "soundtrack"),
        ("Persona 5 Original Soundtrack", "Shoji Meguro", [], "soundtrack"),
        ("Persona 5 Royal", "ATLUS Sound Team", ["Game"], "soundtrack"),
        ("Transformers: The Album", "Various Artists", [], "soundtrack"),
        ("Transformers (The Score)", "Steve Jablonsky", [], "soundtrack"),
        ("Transformers", "Steve Jablonsky", ["Score"], "soundtrack"),
    ]
    for album, artist, genres, expected in cases:
        assert classify(album, artist, genres, False) == expected, album


def test_ordinary_albums_are_not_mistaken_for_sections():
    from server.library import classify

    assert classify("The Score", "Fugees", ["Hip-Hop"], False) == "album"
    assert classify("The Album", "ABBA", ["Pop"], False) == "album"
    assert classify("Pass the Dutchie", "Musical Youth", ["Reggae"], False) == "album"
    assert classify("Funeral", "Arcade Fire", ["Indie Rock"], False) == "album"
    assert classify("Now 50", "Various Artists", ["Pop"], True) == "compilation"
