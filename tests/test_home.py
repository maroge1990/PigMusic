import asyncio
import httpx
from server.home import HomeDiscovery
from server.store import Store

ARTIST = "83d91898-7763-47d7-b03b-b92132375c47"
RELATED = "83d91898-7763-47d7-b03b-b92132375c48"
GROUP = "83d91898-7763-47d7-b03b-b92132375c49"
RECORD = "83d91898-7763-47d7-b03b-b92132375c50"


def test_failed_feed_keeps_stale_data_and_throttles_retry(tmp_path):
    store = Store(tmp_path)
    home = HomeDiscovery(store, None)
    store.write_document(
        "home-feed:v1:lb-radio/artist/x[]",
        {"until": 0, "data": {"retained": []}},
    )
    calls = []

    def unavailable(request):
        calls.append(request)
        return httpx.Response(503)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(unavailable)
        ) as client:
            data, failed = await home.feed(client, "lb-radio/artist/x")
            assert failed and data == {"retained": []}
            await home.feed(client, "lb-radio/artist/x")
            assert len(calls) == 1

    asyncio.run(run())


def test_recommendations_use_only_personal_seeds_and_do_not_submit_listens(tmp_path):
    store = Store(tmp_path)
    store.write_document("library-artist:mine", {"id": ARTIST, "name": "My Artist"})
    store.write_document("library-artist:theirs", {"id": RELATED, "name": "Other User"})

    class Catalogue:
        async def get(self, client, path, **params):
            if path == "release-group/":
                return {
                    "release-groups": [
                        {
                            "id": GROUP,
                            "title": "Recommended Album",
                            "primary-type": "Album",
                            "secondary-types": [],
                        }
                    ]
                }
            return {
                "title": "Recommended Song",
                "releases": [],
                "artist-credit": [{"name": "Related"}],
            }

    calls = []

    def radio(request):
        calls.append(request)
        assert request.method == "GET" and request.url.path.endswith(ARTIST)
        return httpx.Response(
            200,
            json={
                ARTIST: [{"similar_artist_name": "My Artist"}],
                RELATED: [{"similar_artist_name": "Related", "recording_mbid": RECORD}],
            },
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(radio)) as client:
            result = await HomeDiscovery(store, Catalogue()).recommended(
                client,
                {"artists": [{"id": "mine", "name": "My Artist", "track_count": 1}]},
            )
            assert len(calls) == 1
            assert result["artists"][0]["name"] == "Related"
            assert result["albums"][0]["name"] == "Recommended Album"
            assert result["songs"][0]["name"] == "Recommended Song"
            assert result["artists"][0]["reason"] == "Because you saved My Artist"

    asyncio.run(run())


def uid(n):
    return "83d91898-7763-47d7-b03b-b92132375d%02d" % n


class NoCatalogue:
    async def get(self, client, path, **params):
        raise AssertionError("No catalogue request expected: " + path)


class Namesakes:
    """Every name search finds two exact matches that cannot be told apart."""

    def __init__(self):
        self.paths = []

    async def get(self, client, path, **params):
        self.paths.append(path)
        if path == "artist/":
            name = params["query"].split('"')[1]
            return {
                "count": 2,
                "artists": [
                    {"id": uid(90), "name": name},
                    {"id": uid(91), "name": name},
                ],
            }
        return {"count": 0, "release-groups": []}


def library_artist(key, name, tracks, **extra):
    return {"id": key, "name": name, "track_count": tracks, "album_ids": [], **extra}


def radio_transport(responses, calls):
    def respond(request):
        calls.append(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(200, json=responses.get(calls[-1], {}))

    return httpx.MockTransport(respond)


class Similar:
    async def get(self, client, path, **params):
        if path == "release-group/":
            return {"release-groups": []}
        return {"title": "Song", "releases": [], "artist-credit": [{"name": "X"}]}


def test_recommendation_seeds_skip_ineligible_and_resolve_past_ambiguous(tmp_path):
    store = Store(tmp_path)
    store.write_document("library-artist:good", {"id": uid(1), "name": "Good"})
    projection = {
        "albums": [],
        "artists": [
            library_artist("big", "Big Name", 100),
            library_artist("va", "Various", 90, compilation_only=True),
            library_artist("none", "Empty", 80, album_count=0),
            library_artist("good", "Good", 5),
        ],
    }
    calls = []
    catalogue = Namesakes()

    async def run():
        async with httpx.AsyncClient(transport=radio_transport({}, calls)) as client:
            return await HomeDiscovery(store, catalogue).recommended(client, projection)

    result = asyncio.run(run())
    # Only the cached artist seeds Home; the namesake is skipped, others never asked.
    assert calls == [uid(1)]
    assert catalogue.paths.count("artist/") == 1
    assert result["state"] == "ready"


def test_recommendations_report_no_seeds_when_nothing_resolves(tmp_path):
    projection = {
        "albums": [],
        "artists": [library_artist("a", "A", 3), library_artist("b", "B", 2)],
    }
    calls = []

    async def run():
        async with httpx.AsyncClient(transport=radio_transport({}, calls)) as client:
            return await HomeDiscovery(Store(tmp_path), Namesakes()).recommended(
                client, projection
            )

    result = asyncio.run(run())
    assert calls == []
    assert result["state"] == "no_seeds" and result["unavailable"] is False


def test_recommendations_bound_new_resolutions_per_request(tmp_path):
    projection = {
        "albums": [],
        "artists": [library_artist("a%d" % i, "Name %d" % i, 50 - i) for i in range(8)],
    }
    catalogue = Namesakes()

    async def run():
        async with httpx.AsyncClient(transport=radio_transport({}, [])) as client:
            await HomeDiscovery(Store(tmp_path), catalogue).recommended(
                client, projection
            )

    asyncio.run(run())
    assert catalogue.paths.count("artist/") == 4


def test_recommendations_exclude_owned_ids_and_interleave_seeds(tmp_path, monkeypatch):
    # Pin the daily seed rotation so this order assertion is stable on every date.
    class FixedDate:
        @staticmethod
        def today():
            return FixedDate()

        def toordinal(self):
            return 0

    monkeypatch.setattr("server.home.date", FixedDate)
    store = Store(tmp_path)
    store.write_document("library-artist:one", {"id": uid(1), "name": "One"})
    store.write_document("library-artist:two", {"id": uid(2), "name": "Two"})
    # The library holds this artist under a name the radio spells differently.
    store.write_document("library-artist:own", {"id": uid(3), "name": "Own"})
    projection = {
        "albums": [],
        "artists": [
            library_artist("one", "One", 30),
            library_artist("two", "Two", 20),
            library_artist("own", "Own", 10),
        ],
    }

    def similar(ids, owned=None):
        return {
            i: [{"similar_artist_name": "Artist " + i[-2:]}]
            for i in ids + ([owned] if owned else [])
        }

    responses = {
        uid(1): similar([uid(11), uid(12)], uid(3)),
        uid(2): similar([uid(21), uid(22)]),
    }

    async def run():
        async with httpx.AsyncClient(
            transport=radio_transport(responses, [])
        ) as client:
            return await HomeDiscovery(store, Similar()).recommended(client, projection)

    result = asyncio.run(run())
    assert result["state"] == "ready"
    assert [a["id"] for a in result["artists"]] == [uid(11), uid(21), uid(12), uid(22)]
    assert uid(3) not in [a["id"] for a in result["artists"]]


APPLE_SONGS = {
    "feed": {
        "results": [
            {"id": "1", "name": "Hit (feat. Guest)", "artistName": "Star & Friend"},
            {"id": "2", "name": "Nothing Matches", "artistName": "Nobody"},
        ]
    }
}
APPLE_ALBUMS = {
    "feed": {
        "results": [{"id": "9", "name": "Big Record - Single", "artistName": "Star"}]
    }
}


class Matching:
    def __init__(self):
        self.queries = []

    async def get(self, client, path, **params):
        self.queries.append(params["query"])
        if path == "recording/":
            hits = []
            if '"Hit"' in params["query"]:
                hits = [
                    {
                        "id": RECORD,
                        "title": "Hit",
                        "artist-credit": [
                            {
                                "name": "Star",
                                "joinphrase": " & ",
                                "artist": {"id": ARTIST, "name": "Star"},
                            },
                            {
                                "name": "Friend",
                                "artist": {"id": RELATED, "name": "Friend"},
                            },
                        ],
                        "releases": [
                            {
                                "id": uid(40),
                                "title": "Live",
                                "status": "Bootleg",
                                "date": "2000",
                            },
                            {
                                "id": uid(41),
                                "title": "Studio",
                                "status": "Official",
                                "date": "2020",
                                "release-group": {"primary-type": "Album"},
                            },
                        ],
                    }
                ]
            return {"recordings": hits}
        hits = []
        if '"Big Record"' in params["query"]:
            hits = [
                {
                    "id": GROUP,
                    "title": "Big Record",
                    "artist-credit": [
                        {"name": "Star", "artist": {"id": ARTIST, "name": "Star"}}
                    ],
                }
            ]
        return {"release-groups": hits}


def apple_transport(requests):
    def respond(request):
        requests.append(str(request.url))
        assert request.url.host == "rss.marketingtools.apple.com"
        kind = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(
            200, json=APPLE_SONGS if kind == "songs.json" else APPLE_ALBUMS
        )

    return httpx.MockTransport(respond)


def test_popular_chart_is_apple_matched_to_musicbrainz_and_cached(tmp_path):
    from server.catalogue import warming

    store = Store(tmp_path)
    (tmp_path / "media-settings.json").write_text('{"chart_region": "nz"}')
    requests, catalogue = [], Matching()

    async def run():
        warming.set(True)
        async with httpx.AsyncClient(transport=apple_transport(requests)) as client:
            home = HomeDiscovery(store, catalogue)
            first = await home.popular(client)
            return first, await home.popular(client)

    first, second = asyncio.run(run())
    assert first == second
    assert first["state"] == "ready" and first["title"] == "Popular in New Zealand"
    assert first["source"] == "Apple Music · New Zealand"
    assert all("/nz/music/most-played/100/" in url for url in requests)
    assert not any("listenbrainz" in url for url in requests)
    assert len(requests) == 2
    assert [s["id"] for s in first["songs"]] == [RECORD]
    assert first["songs"][0]["release_id"] == uid(41)
    assert first["songs"][0]["artist"] == "Star & Friend"
    assert [a["id"] for a in first["albums"]] == [GROUP]
    assert [a["id"] for a in first["artists"]] == [ARTIST]
    # A duet credit opens, and is named as, its first artist.
    assert first["artists"][0]["name"] == "Star"
    assert 'releasegroup:"Big Record"' in catalogue.queries[-1]


def test_popular_chart_reuses_entry_matches_between_builds(tmp_path):
    from server.catalogue import warming

    store = Store(tmp_path)
    catalogue = Matching()

    async def run():
        warming.set(True)
        async with httpx.AsyncClient(transport=apple_transport([])) as client:
            home = HomeDiscovery(store, catalogue)
            await home.popular(client)
            store.write_document("home-chart:v1:au", {"until": 0, "data": {}})
            before = len(catalogue.queries)
            await home.popular(client)
            return before, len(catalogue.queries)

    before, after = asyncio.run(run())
    assert before == after


def test_popular_chart_is_preparing_until_built_in_background(tmp_path):
    store = Store(tmp_path)

    async def run():
        async with httpx.AsyncClient(transport=apple_transport([])) as client:
            home = HomeDiscovery(store, Matching())
            first = await home.popular(client)
            await asyncio.gather(*home.building.values())
            return first, await home.popular(client)

    first, second = asyncio.run(run())
    assert first["state"] == "preparing" and first["songs"] == []
    assert first["title"] == "Popular in Australia"
    assert second["state"] == "ready" and second["songs"]


def test_popular_chart_skips_an_entry_when_musicbrainz_is_busy(tmp_path):
    from server.catalogue import warming

    class Busy(Matching):
        async def get(self, client, path, **params):
            if path == "recording/":
                request = httpx.Request("GET", "https://musicbrainz.org/ws/2/")
                raise httpx.HTTPStatusError(
                    "busy", request=request, response=httpx.Response(503)
                )
            return await super().get(client, path, **params)

    store = Store(tmp_path)

    async def run():
        warming.set(True)
        async with httpx.AsyncClient(transport=apple_transport([])) as client:
            return await HomeDiscovery(store, Busy()).popular(client)

    result = asyncio.run(run())
    # Both song lookups failed, but the album still makes a usable chart.
    assert result["state"] == "ready" and result["songs"] == []
    assert [a["id"] for a in result["albums"]] == [GROUP]
    assert result["artists"][0]["name"] == "Star"


def test_popular_chart_prefers_the_studio_recording_over_a_live_one(tmp_path):
    from server.catalogue import warming

    studio, live = uid(60), uid(61)

    class Versions(Matching):
        async def get(self, client, path, **params):
            if path != "recording/":
                return await super().get(client, path, **params)
            credit = [{"name": "Star", "artist": {"id": ARTIST, "name": "Star"}}]
            return {
                "recordings": [
                    {
                        "id": live,
                        "title": "Hit",
                        "disambiguation": "live, 2026-09-11: Phoenix Concert Theatre",
                        "artist-credit": credit,
                        "releases": [{"id": uid(62), "status": "Bootleg"}],
                    },
                    {
                        "id": studio,
                        "title": "Hit",
                        "artist-credit": credit,
                        "releases": [
                            {
                                "id": uid(63),
                                "title": "Hit",
                                "status": "Official",
                                "release-group": {"primary-type": "Single"},
                            }
                        ],
                    },
                ]
            }

    store = Store(tmp_path)

    async def run():
        warming.set(True)
        async with httpx.AsyncClient(transport=apple_transport([])) as client:
            return await HomeDiscovery(store, Versions()).popular(client)

    result = asyncio.run(run())
    assert [s["id"] for s in result["songs"]] == [studio]
    assert result["songs"][0]["release_id"] == uid(63)


def test_popular_chart_failure_is_unavailable_and_retried_later(tmp_path):
    store = Store(tmp_path)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(500))
        ) as client:
            home = HomeDiscovery(store, Matching())
            await home.popular(client)
            await asyncio.gather(*home.building.values())
            return await home.popular(client)

    result = asyncio.run(run())
    assert result["state"] == "unavailable" and result["unavailable"] is True


def test_chart_region_is_validated(tmp_path):
    import pytest
    from server.media import public_settings, save_settings

    store = Store(tmp_path)
    assert public_settings(store)["chart_region"] == "au"
    for bad in ("xxx", "A1", "../"):
        with pytest.raises(ValueError):
            save_settings(store, True, True, chart_region=bad)
    assert save_settings(store, True, True, chart_region="gb")["chart_region"] == "gb"


def test_popular_chart_keeps_up_to_100_songs_and_retries_a_flaky_feed(tmp_path):
    """#55 (Mark, 31 Oct): Apple's feed serves at most 100; Home keeps every
    matched song up to 100, and one 502 from the feed is tried again."""
    from server.catalogue import warming

    store = Store(tmp_path)
    entries = [
        {"id": str(i), "name": f"Song {i}", "artistName": "Star"} for i in range(100)
    ]
    seen = []

    def respond(request):
        seen.append(request.url.path)
        if len(seen) == 1:
            return httpx.Response(502)
        kind = request.url.path.rsplit("/", 1)[-1]
        if kind == "songs.json":
            return httpx.Response(200, json={"feed": {"results": entries}})
        return httpx.Response(200, json={"feed": {"results": []}})

    class Every:
        async def get(self, client, path, **params):
            title = params["query"].split('"')[1]
            return {
                "recordings": [
                    {
                        "id": "rec-" + title,
                        "title": title,
                        "artist-credit": [
                            {"name": "Star", "artist": {"id": ARTIST, "name": "Star"}}
                        ],
                        "releases": [
                            {"id": uid(41), "title": "R", "status": "Official"}
                        ],
                    }
                ]
            }

    async def run():
        warming.set(True)
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await HomeDiscovery(store, Every()).popular(client)

    chart = asyncio.run(run())
    assert chart["state"] == "ready"
    assert len(chart["songs"]) == 100
    assert seen.count(seen[0]) == 2  # The 502 was asked again.
