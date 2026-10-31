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


def test_server_results_are_playable_and_open_for_a_member_without_them(
    tmp_path, monkeypatch
):
    """Codex round 1: unplayable server music is never a result, and a server
    album or artist found by search opens for someone who hasn't added it."""
    app, owner, member, token, track = household(tmp_path, monkeypatch)
    tracks = [
        {
            **track,
            "id": "ok",
            "title": "Song",
            "artist": "Band",
            "album_artist": "Band",
            "album": "Live",
        },
        {
            **track,
            "id": "gone",
            "title": "Other",
            "artist": "Ghost",
            "album_artist": "Ghost",
            "album": "Missing",
            "available": False,
        },
    ]
    (tmp_path / "library-snapshot.json").write_text(json.dumps({"tracks": tracks}))
    app.state.store.refresh_library()
    with TestClient(
        app,
        base_url="https://private.ts.net",
        headers={"Authorization": "Bearer " + token},
    ) as client:
        ghost = client.get("/api/v1/search/results", params={"q": "ghost"}).json()
        assert ghost["albums"] == [] and ghost["artists"] == []
        assert ghost["top"] == []
        missing = client.get("/api/v1/search/results", params={"q": "missing"}).json()
        assert missing["albums"] == []

        found = client.get("/api/v1/search/results", params={"q": "live"}).json()
        album = found["albums"][0]
        assert album["in_library"] is False
        assert album["server_state"] == "household_available"
        opened = client.get("/api/v1/albums/" + album["id"])
        assert opened.status_code == 200, opened.text
        assert [t["id"] for t in opened.json()["tracks"]] == ["ok"]
        assert opened.json()["tracks"][0]["server_state"] == "household_available"
        artist = client.get("/api/v1/search/results", params={"q": "band"}).json()
        page = client.get("/api/v1/artists/" + artist["artists"][0]["id"])
        assert page.status_code == 200, page.text
        assert [a["id"] for a in page.json()["albums"]] == [album["id"]]
        ghost_id = next(
            a["id"] for a in app.state.store.library["artists"] if a["name"] == "Ghost"
        )
        assert client.get("/api/v1/artists/" + ghost_id).status_code == 404
        assert "relative_path" not in opened.text + page.text


def test_a_compilation_only_artist_is_found_and_opens(tmp_path, monkeypatch):
    """#102: a performer only on a Various Artists album owns no album, but
    their playable songs make them a search result with a page."""
    app, owner, member, token, track = household(tmp_path, monkeypatch)
    tracks = [
        {
            **track,
            "id": "u2",
            "title": "One",
            "artist": "U2",
            "album_artist": "Various Artists",
            "album": "Hits",
            "compilation": True,
        },
        {
            **track,
            "id": "other",
            "title": "Two",
            "artist": "Someone",
            "album_artist": "Various Artists",
            "album": "Hits",
            "compilation": True,
        },
    ]
    (tmp_path / "library-snapshot.json").write_text(json.dumps({"tracks": tracks}))
    app.state.store.refresh_library()
    with TestClient(
        app,
        base_url="https://private.ts.net",
        headers={"Authorization": "Bearer " + token},
    ) as client:
        found = client.get("/api/v1/search/results", params={"q": "u2"}).json()
        assert [a["name"] for a in found["artists"]] == ["U2"]
        page = client.get("/api/v1/artists/" + found["artists"][0]["id"])
        assert page.status_code == 200, page.text
        assert [t["id"] for t in page.json()["compilation_tracks"]] == ["u2"]
        assert page.json()["albums"] == []


def test_a_server_artist_found_by_search_has_a_catalogue_identity(
    tmp_path, monkeypatch
):
    """Opening a server artist outside the library asks for its identity;
    that must not be a 404 (D-61)."""
    from server.routes import discover

    app, member, token = library(tmp_path, monkeypatch)
    asked = []

    async def resolve_artist(store, catalogue, client, artist, albums):
        asked.append((artist["name"], [a["title"] for a in albums]))
        return {"id": "mb-coldplay", "name": artist["name"]}

    monkeypatch.setattr(discover, "resolve_artist", resolve_artist)
    with TestClient(
        app,
        base_url="https://private.ts.net",
        headers={"Authorization": "Bearer " + token},
    ) as client:
        found = client.get("/api/v1/search/results", params={"q": "coldplay"}).json()
        artist = found["artists"][0]
        assert artist["in_library"] is False
        identity = client.get("/api/v1/catalogue/library-artists/" + artist["id"])
        assert identity.status_code == 200, identity.text
        assert identity.json()["id"] == "mb-coldplay"
        assert asked == [("Coldplay", ["Parachutes", "X&Y"])]
        assert client.get("/api/v1/catalogue/library-artists/nobody").status_code == 404


def test_names_are_normalised_once_not_on_every_search():
    """#54: normalising every server name on every search cost ~140 ms at
    20,000 songs; the forms are remembered."""
    search_rank.normalise.cache_clear()
    for _ in range(3):
        search_rank.named("yellow", "Yellow Submarine", "The Beatles")
    info = search_rank.normalise.cache_info()
    assert info.hits > info.misses
