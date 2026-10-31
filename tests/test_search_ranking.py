"""One ranked search over every kind of result (D-61, #53)."""

import json

from fastapi.testclient import TestClient

from server import search_rank
from test_personal_libraries import household


def test_closer_matches_score_higher_and_kinds_match_their_own_names():
    scores = [
        search_rank.score("yellow", text)
        for text in ("Yellow", "Yellow Submarine", "Mellow Yellow", "Yellowcard")
    ]
    assert scores == [
        search_rank.EXACT,
        search_rank.START,
        search_rank.WORDS,
        search_rank.START,
    ]
    assert search_rank.score("yel sub", "Yellow Submarine") == search_rank.ALL_WORDS
    assert search_rank.score("blue", "Yellow") == 0
    # A song found only through its artist ranks below one named like the search.
    assert search_rank.named("coldplay", "Yellow", "Coldplay") == search_rank.BY_ARTIST
    assert search_rank.named("coldplay yellow", "Yellow", "Coldplay") > 0


def test_the_library_only_breaks_ties():
    def result(name, value, mine):
        return {
            "kind": "song",
            "id": name,
            "name": name,
            "score": value,
            "in_library": mine,
        }

    ordered = search_rank.order(
        [
            result("server exact", search_rank.EXACT, False),
            result("mine anywhere", search_rank.ANYWHERE, True),
            result("server anywhere", search_rank.ANYWHERE, False),
        ]
    )
    assert [r["id"] for r in ordered] == [
        "server exact",
        "mine anywhere",
        "server anywhere",
    ]


def library(tmp_path, monkeypatch):
    app, owner, member, token, track = household(tmp_path, monkeypatch)
    songs = [
        ("yellow", "Yellow", "Coldplay", "Parachutes"),
        ("fix", "Fix You", "Coldplay", "X&Y"),
        ("sub", "All Together Now", "The Beatles", "Yellow Submarine"),
        ("card", "Ocean Avenue", "Yellowcard", "Ocean Avenue"),
    ]
    tracks = [
        {**track, "id": i, "title": t, "artist": a, "album_artist": a, "album": al}
        for i, t, a, al in songs
    ]
    (tmp_path / "library-snapshot.json").write_text(json.dumps({"tracks": tracks}))
    app.state.store.refresh_library()
    return app, member, token


def test_a_song_title_search_puts_the_song_first_whatever_its_home(
    tmp_path, monkeypatch
):
    app, member, token = library(tmp_path, monkeypatch)
    with TestClient(
        app,
        base_url="https://private.ts.net",
        headers={"Authorization": "Bearer " + token},
    ) as client:
        found = client.get("/api/v1/search/results", params={"q": "yellow"})
        assert found.status_code == 200, found.text
        value = found.json()
        assert value["top"][0] == {"kind": "song", "id": "yellow"}
        assert {r["kind"] for r in value["top"]} >= {"song", "album", "artist"}
        # Songs, albums and artists each come once, marked, not split by home.
        assert [s["id"] for s in value["songs"]][0] == "yellow"
        assert value["songs"][0]["server_state"] == "household_available"
        assert [a["title"] for a in value["albums"]][0] == "Yellow Submarine"
        assert value["albums"][0]["in_library"] is False
        assert "relative_path" not in found.text

        app.state.store.user_libraries.add(member, ["fix"])
        artist = client.get("/api/v1/search/results", params={"q": "coldplay"}).json()
        assert artist["top"][0]["kind"] == "artist"
        assert artist["artists"][0]["name"] == "Coldplay"
        assert artist["artists"][0]["in_library"] is True
        mine = next(s for s in artist["songs"] if s["id"] == "fix")
        assert mine["server_state"] == "in_library"

        empty = client.get("/api/v1/search/results", params={"q": " "}).json()
        assert empty == {
            "top": [],
            "songs": [],
            "albums": [],
            "artists": [],
            "playlists": [],
        }
        for params in ({"q": "x", "limit": 9}, {"q": "x", "limit": 201}):
            assert (
                client.get("/api/v1/search/results", params=params).status_code == 422
            )
