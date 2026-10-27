import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from server.artist_identity import Ambiguous, resolve_artist
from server.catalogue import Catalogue
from server.store import Store
from test_catalogue import ARTIST, OTHER, RELEASE
from test_personal_libraries import household

ARTIST_ROW = {"id": "lib-artist", "name": "Artist"}
ALBUMS = [
    {"id": "alb", "title": "Studio", "musicbrainz_release_id": RELEASE},
]
NAMESAKES = [
    {"id": ARTIST, "name": "Artist", "disambiguation": "rock band"},
    {"id": OTHER, "name": "Artist", "disambiguation": "dj"},
]


class Fake:
    def __init__(self, **handlers):
        self.handlers, self.paths = handlers, []

    async def get(self, client, path, **params):
        self.paths.append(path)
        return self.handlers[path.split("/")[0]](path, params)


def resolve(store, catalogue, albums=ALBUMS):
    return asyncio.run(resolve_artist(store, catalogue, None, ARTIST_ROW, albums))


def test_release_credit_resolves_without_name_search(tmp_path):
    store = Store(tmp_path)
    catalogue = Fake(
        release=lambda path, params: {
            "artist-credit": [{"artist": {"id": ARTIST, "name": "Artist"}}]
        }
    )
    assert resolve(store, catalogue) == {"id": ARTIST, "name": "Artist"}
    assert catalogue.paths == ["release/" + RELEASE]
    assert store.read_document("library-artist:lib-artist")["id"] == ARTIST


def test_cached_identity_short_circuits(tmp_path):
    store = Store(tmp_path)
    store.write_document("library-artist:lib-artist", {"id": ARTIST, "name": "Artist"})
    assert resolve(store, Fake()) == {"id": ARTIST, "name": "Artist"}


def test_cached_identity_for_another_name_is_ignored(tmp_path):
    store = Store(tmp_path)
    store.write_document("library-artist:lib-artist", {"id": OTHER, "name": "Renamed"})
    catalogue = Fake(
        artist=lambda path, params: {"count": 1, "artists": [NAMESAKES[0]]}
    )
    assert resolve(store, catalogue, [])["id"] == ARTIST


def test_namesakes_resolved_by_owned_album_overlap(tmp_path):
    catalogue = Fake(
        artist=lambda path, params: {"count": 2, "artists": NAMESAKES},
        release=lambda path, params: {"artist-credit": []},
        **{
            "release-group": lambda path, params: {
                "count": 1,
                "release-groups": [
                    {
                        "title": "Studio",
                        "artist-credit": [{"artist": {"id": OTHER, "name": "Artist"}}],
                    }
                ],
            }
        },
    )
    assert resolve(Store(tmp_path), catalogue)["id"] == OTHER


def test_ambiguous_namesakes_carry_candidates(tmp_path):
    store = Store(tmp_path)
    catalogue = Fake(
        artist=lambda path, params: {"count": 2, "artists": NAMESAKES},
        release=lambda path, params: {"artist-credit": []},
        **{"release-group": lambda path, params: {"count": 0, "release-groups": []}},
    )
    with pytest.raises(Ambiguous) as caught:
        resolve(store, catalogue)
    assert caught.value.candidates == [
        {"id": ARTIST, "name": "Artist", "disambiguation": "rock band"},
        {"id": OTHER, "name": "Artist", "disambiguation": "dj"},
    ]
    assert store.read_document("library-artist:lib-artist") == {}


def test_no_match_is_ambiguous_with_no_candidates(tmp_path):
    catalogue = Fake(artist=lambda path, params: {"count": 0, "artists": []})
    with pytest.raises(Ambiguous) as caught:
        resolve(Store(tmp_path), catalogue, [])
    assert caught.value.candidates == []


def client_for(app, token):
    return TestClient(
        app,
        base_url="https://private.ts.net",
        headers={"Authorization": "Bearer " + token, "X-Pig-Request": "1"},
    )


def prepared(tmp_path, monkeypatch):
    from test_security import PASSWORD

    app, owner, member, member_token, track = household(tmp_path, monkeypatch)
    app.state.store.user_libraries.add(member, [track["id"]])
    owner_token = app.state.auth.login(
        "mark", PASSWORD, "device", "laptop", "web", "fixture"
    )["token"]
    artist = app.state.store.user_libraries.projection(member)["artists"][0]
    return app, owner_token, member_token, artist["id"]


def test_ambiguous_saved_artist_returns_candidates_and_owner_can_link(
    tmp_path, monkeypatch
):
    app, owner_token, member_token, artist_id = prepared(tmp_path, monkeypatch)
    path = "/api/v1/catalogue/library-artists/" + artist_id

    async def metadata(self, client, path, **params):
        if path == "artist/" + OTHER:
            return {"id": OTHER, "name": "Artist"}
        if path.startswith("artist/") and path != "artist/":
            request = httpx.Request("GET", "https://musicbrainz.org/")
            raise httpx.HTTPStatusError(
                "missing", request=request, response=httpx.Response(404)
            )
        return {"count": 2, "artists": NAMESAKES, "release-groups": []}

    monkeypatch.setattr(Catalogue, "get", metadata)
    with client_for(app, member_token) as client:
        as_member = {"Authorization": "Bearer " + member_token}
        as_owner = {"Authorization": "Bearer " + owner_token}
        response = client.get(path, headers=as_member)
        assert response.status_code == 409
        body = response.json()
        assert body["code"] == "conflict" and isinstance(body["detail"], str)
        assert [c["id"] for c in body["candidates"]] == [ARTIST, OTHER]
        assert body["candidates"][0]["disambiguation"] == "rock band"
        forbidden = client.put(path, json={"id": OTHER}, headers=as_member)
        assert forbidden.status_code == 403
        for bad in ("nope", ARTIST):
            rejected = client.put(path, json={"id": bad}, headers=as_owner)
            assert rejected.status_code == 400
        linked = client.put(path, json={"id": OTHER}, headers=as_owner)
        assert linked.json()["id"] == OTHER
        stored = app.state.store.read_document("library-artist:" + artist_id)
        assert stored["id"] == OTHER
        assert client.get(path, headers=as_member).json()["id"] == OTHER


CAST = "Anthony Ramos, Okieriete Onaodowan, Daveed Diggs, Lin-Manuel Miranda & Leslie Odom Jr."
LIN = {"id": "44444444-4444-4444-8444-444444444444", "name": "Lin-Manuel Miranda"}


def credit_row(name):
    return {"id": "lib-credit", "name": name}


def resolve_credit(store, catalogue, name, albums):
    return asyncio.run(resolve_artist(store, catalogue, None, credit_row(name), albums))


def test_a_cast_albums_song_credit_files_under_the_album_artist(tmp_path):
    store = Store(tmp_path)
    catalogue = Fake(
        release=lambda path, params: {
            "artist-credit": [{"artist": LIN, "name": "Lin-Manuel Miranda"}]
        }
    )
    albums = [{**ALBUMS[0], "title": "Hamilton", "category": "musical"}]
    assert resolve_credit(store, catalogue, CAST, albums) == LIN
    # No catalogue search ever names the whole credit.
    assert catalogue.paths == ["release/" + RELEASE]
    # Remembered for that credit: asked again, nothing is fetched.
    again = Fake()
    assert resolve_credit(store, again, CAST, albums)["id"] == LIN["id"]
    assert again.paths == []


def test_a_featured_credit_resolves_to_its_lead(tmp_path):
    catalogue = Fake(
        release=lambda path, params: {
            "artist-credit": [
                {"artist": {"id": ARTIST, "name": "Coldplay"}, "joinphrase": " & "},
                {"artist": {"id": OTHER, "name": "Rihanna"}},
            ]
        }
    )
    found = resolve_credit(Store(tmp_path), catalogue, "Coldplay feat. Rihanna", ALBUMS)
    assert found == {"id": ARTIST, "name": "Coldplay"}
    assert catalogue.paths == ["release/" + RELEASE]


def test_without_releases_the_lead_is_found_by_exactly_their_name(tmp_path):
    searched = []

    def artist(path, params):
        searched.append(params["query"])
        if "Lin-Manuel Miranda &" in params["query"]:
            return {"count": 0, "artists": []}
        return {"count": 1, "artists": [LIN]}

    found = resolve_credit(
        Store(tmp_path), Fake(artist=artist), "Lin-Manuel Miranda & Leslie Odom Jr.", []
    )
    assert found == LIN
    assert searched[-1] == 'artist:"Lin-Manuel Miranda"'


def test_a_band_named_with_an_ampersand_stays_one_artist(tmp_path):
    band = {"id": ARTIST, "name": "Simon & Garfunkel"}
    catalogue = Fake(release=lambda path, params: {"artist-credit": [{"artist": band}]})
    assert (
        resolve_credit(Store(tmp_path), catalogue, "Simon & Garfunkel", ALBUMS) == band
    )


def test_an_identity_already_stored_is_unchanged(tmp_path):
    store = Store(tmp_path)
    store.write_document(
        "library-artist:lib-credit", {"id": ARTIST, "name": "Coldplay feat. Rihanna"}
    )
    found = resolve_credit(store, Fake(), "Coldplay feat. Rihanna", ALBUMS)
    assert found == {"id": ARTIST, "name": "Coldplay feat. Rihanna"}


VARIOUS = {"id": "89ad4ac3-39f7-470e-963a-56509c546377", "name": "Various Artists"}


def test_a_band_on_a_various_artists_release_is_found_by_its_whole_name(tmp_path):
    band = {"id": ARTIST, "name": "Hall & Oates"}
    searched = []

    def artist(path, params):
        searched.append(params["query"])
        if params["query"] == 'artist:"Hall & Oates"':
            return {"count": 1, "artists": [band]}
        return {"count": 1, "artists": [{"id": OTHER, "name": "Hall"}]}

    catalogue = Fake(
        release=lambda path, params: {"artist-credit": [{"artist": VARIOUS}]},
        artist=artist,
    )
    store = Store(tmp_path)
    assert resolve_credit(store, catalogue, "Hall & Oates", ALBUMS) == band
    assert searched == ['artist:"Hall & Oates"']
    assert store.read_document("library-artist:lib-credit")["id"] == ARTIST


def test_various_artists_is_never_a_performers_identity(tmp_path):
    def artist(path, params):
        return {"count": 0, "artists": []}

    catalogue = Fake(
        release=lambda path, params: {"artist-credit": [{"artist": VARIOUS}]},
        artist=artist,
    )
    store = Store(tmp_path)
    albums = [{**ALBUMS[0], "title": "Film", "category": "soundtrack"}]
    with pytest.raises(Ambiguous):
        resolve_credit(store, catalogue, "Singer A & Singer B", albums)
    assert store.read_document("library-artist:lib-credit") == {}
