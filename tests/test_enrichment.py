import asyncio, json, io
import httpx
import pytest
from PIL import Image
from server.store import Store
from server.media import save_settings, public_settings
from server.popularity import Popularity
from server.artist_backgrounds import Backgrounds
from server.artwork import Artwork
from server.catalogue import Catalogue

ARTIST = "70248960-cb53-4ea4-943a-edb18f7d336f"
RELEASE = "11111111-1111-4111-8111-111111111111"
TOKEN = "00000000-0000-4000-8000-000000000000"


def test_tokens_are_private_preserved_and_removable(tmp_path):
    store = Store(tmp_path)
    save_settings(store, True, True, fanart_key="a" * 32, listenbrainz_token=TOKEN)
    public = save_settings(store, True, True)
    assert public["listenbrainz_configured"] and public["fanart_configured"]
    assert TOKEN not in json.dumps(public)
    assert not save_settings(store, True, True, listenbrainz_token="")[
        "listenbrainz_configured"
    ]
    assert public_settings(store)["fanart_configured"]


def test_popularity_auth_cache_and_removing_token_hides_cached_feature(tmp_path):
    store = Store(tmp_path)
    provider = Popularity(store)
    requests = []

    def transport(request):
        requests.append(request)
        assert request.headers["Authorization"] == "Token " + TOKEN
        if "top-recordings" in request.url.path:
            return httpx.Response(
                200,
                json=[
                    {
                        "artist_mbids": [ARTIST],
                        "recording_mbid": RELEASE,
                        "release_mbid": RELEASE,
                        "recording_name": "Song",
                        "release_name": "Album",
                        "total_listen_count": 50,
                    },
                    {
                        "artist_mbids": [],
                        "recording_mbid": RELEASE,
                        "release_mbid": RELEASE,
                        "recording_name": "Wrong",
                        "total_listen_count": 100,
                    },
                ],
            )
        return httpx.Response(
            200,
            json=[
                {
                    "release_group_mbid": RELEASE,
                    "release_group": {"type": "Album", "name": "Album", "date": "2020"},
                    "total_listen_count": 100,
                }
            ],
        )

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            assert not (await provider.get(client, ARTIST))["enabled"] and not requests
            save_settings(store, True, True, listenbrainz_token=TOKEN)
            result = await provider.get(client, ARTIST)
            assert [s["title"] for s in result["songs"]] == ["Song"] and result[
                "albums"
            ][0]["title"] == "Album"
            assert TOKEN not in json.dumps(result)
            assert await provider.get(client, ARTIST) == result and len(requests) == 2
            save_settings(store, True, True, listenbrainz_token="")
            assert (
                not (await provider.get(client, ARTIST))["enabled"]
                and len(requests) == 2
            )

    asyncio.run(run())


def test_provider_failure_is_quiet_and_has_no_secret_in_result(tmp_path):
    store = Store(tmp_path)
    save_settings(store, True, True, listenbrainz_token=TOKEN)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(401, json={"error": TOKEN})
            )
        ) as client:
            result = await Popularity(store).get(client, ARTIST)
            assert (
                not result["songs"]
                and not result["albums"]
                and TOKEN not in json.dumps(result)
            )

    asyncio.run(run())


def test_background_preserves_landscape_and_refuses_untrusted_asset(tmp_path):
    store = Store(tmp_path)
    art = Artwork(store, Catalogue(store))
    provider = Backgrounds(store, art)
    requests = []
    image = io.BytesIO()
    Image.new("RGB", (1920, 1080), "pink").save(image, "JPEG")

    def transport(request):
        requests.append(request)
        if request.url.host == "webservice.fanart.tv":
            return httpx.Response(
                200,
                json={
                    "artistbackground": [
                        {"url": "https://assets.fanart.tv/background.jpg", "likes": "5"}
                    ]
                },
            )
        assert (
            request.url.host == "assets.fanart.tv" and "api-key" not in request.headers
        )
        return httpx.Response(200, content=image.getvalue())

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            assert await provider.get(client, ARTIST) is None and not requests
            save_settings(store, True, True, fanart_key="a" * 32)
            path = await provider.get(client, ARTIST)
            assert Image.open(path).size == (1600, 900)
            assert await provider.get(client, ARTIST) == path and len(requests) == 2
            save_settings(store, True, True, fanart_key="")
            assert await provider.get(client, ARTIST) is None
        save_settings(store, True, True, fanart_key="b" * 32)
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json={
                        "artistbackground": [
                            {"url": "https://untrusted.example/image.jpg", "likes": "1"}
                        ]
                    },
                )
            )
        ) as client:
            assert await provider.get(client, ARTIST) is None

    asyncio.run(run())


def test_popularity_accepts_large_artist_catalogues(tmp_path):
    store = Store(tmp_path)
    save_settings(store, True, True, listenbrainz_token=TOKEN)

    def transport(request):
        if "top-recordings" in request.url.path:
            item = {
                "artist_mbids": [ARTIST],
                "recording_mbid": RELEASE,
                "release_mbid": RELEASE,
                "recording_name": "Song",
                "total_listen_count": 50,
                "tags": "x" * 2_100_000,
            }
        else:
            item = {
                "release_group_mbid": RELEASE,
                "release_group": {"type": "Album", "name": "Album"},
                "total_listen_count": 100,
                "tags": "x" * 3_100_000,
            }
        return httpx.Response(200, json=[item])

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            result = await Popularity(store).get(client, ARTIST)
            assert len(result["songs"]) == 1 and len(result["albums"]) == 1

    asyncio.run(run())


def test_partial_provider_failure_keeps_available_rankings(tmp_path):
    store = Store(tmp_path)
    save_settings(store, True, True, listenbrainz_token=TOKEN)

    def transport(request):
        if "top-recordings" in request.url.path:
            return httpx.Response(503)
        return httpx.Response(
            200,
            json=[
                {
                    "release_group_mbid": RELEASE,
                    "release_group": {"type": "Album", "name": "Available album"},
                    "total_listen_count": 100,
                },
                {"release_group": None, "total_listen_count": 20},
                {"total_listen_count": "invalid"},
            ],
        )

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            result = await Popularity(store).get(client, ARTIST)
            assert (
                not result["songs"]
                and result["albums"][0]["title"] == "Available album"
            )

    asyncio.run(run())


def test_popularity_returns_twenty_songs_and_ten_albums_in_rank_order(tmp_path):
    store = Store(tmp_path)
    save_settings(store, True, True, listenbrainz_token=TOKEN)

    def transport(request):
        if "top-recordings" in request.url.path:
            rows = [
                {
                    "artist_mbids": [ARTIST],
                    "recording_mbid": f"{n:08d}-1111-4111-8111-111111111111",
                    "release_mbid": RELEASE,
                    "recording_name": str(n),
                    "total_listen_count": n,
                }
                for n in range(1, 26)
            ]
        else:
            rows = [
                {
                    "release_group_mbid": f"{n:08d}-1111-4111-8111-111111111111",
                    "release_group": {"type": "Album", "name": str(n)},
                    "total_listen_count": n,
                }
                for n in range(1, 16)
            ]
        return httpx.Response(200, json=rows)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            result = await Popularity(store).get(client, ARTIST)
            assert len(result["songs"]) == 20 and len(result["albums"]) == 10
            assert (
                result["songs"][0]["title"] == "25"
                and result["songs"][-1]["title"] == "6"
            )
            assert (
                result["albums"][0]["title"] == "15"
                and result["albums"][-1]["title"] == "6"
            )

    asyncio.run(run())


def test_popular_albums_verify_primary_type_and_exclude_live(tmp_path):
    store = Store(tmp_path)
    save_settings(store, True, True, listenbrainz_token=TOKEN)

    class Metadata:
        async def get(self, client, path):
            if path.endswith("11111111-1111-4111-8111-111111111111"):
                return {"primary-type": "Single", "title": "Single"}
            if path.endswith("22222222-2222-4222-8222-222222222222"):
                return {
                    "primary-type": "Album",
                    "secondary-types": ["Live"],
                    "title": "Live",
                }
            return {
                "primary-type": "Album",
                "secondary-types": [],
                "title": "Studio",
                "first-release-date": "2020",
            }

    # Explicit IDs keep the provider fixture easy to inspect.
    ids = [
        "11111111-1111-4111-8111-111111111111",
        "22222222-2222-4222-8222-222222222222",
        "33333333-3333-4333-8333-333333333333",
    ]

    def transport(request):
        return httpx.Response(
            200,
            json=(
                []
                if "top-recordings" in request.url.path
                else [
                    {
                        "release_group_mbid": identity,
                        "release_group": {"type": "Album", "name": "Provider album"},
                        "total_listen_count": 30 - index,
                    }
                    for index, identity in enumerate(ids)
                ]
            ),
        )

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            result = await Popularity(store, Metadata()).get(client, ARTIST)
            assert [a["title"] for a in result["albums"]] == ["Studio"]

    asyncio.run(run())


def test_artist_warming_fetches_songs_without_album_details_or_poisoning_full_cache(
    tmp_path,
):
    from server.routes.catalogue import catalogue_router

    store = Store(tmp_path)
    save_settings(store, False, True, listenbrainz_token=TOKEN)
    catalogue = Catalogue(store)
    popularity = Popularity(store, catalogue)
    requests = []

    def transport(request):
        requests.append((request.url.host, request.url.path))
        if request.url.host == "musicbrainz.org":
            return httpx.Response(
                200, json={"primary-type": "Album", "title": "Studio"}
            )
        if "top-recordings" in request.url.path:
            return httpx.Response(
                200,
                json=[
                    {
                        "artist_mbids": [ARTIST],
                        "recording_mbid": RELEASE,
                        "release_mbid": RELEASE,
                        "recording_name": "Song",
                        "release_name": "Studio",
                    }
                ],
            )
        return httpx.Response(
            200,
            json=[
                {
                    "release_group_mbid": RELEASE,
                    "release_group": {"type": "Album", "name": "Studio"},
                }
            ],
        )

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            router, *_ = catalogue_router(
                store,
                catalogue,
                None,
                popularity,
                None,
                None,
                None,
                None,
                None,
                lambda: "fixture",
                lambda: client,
            )
            assert await router.warm_artist(ARTIST) == [("release", RELEASE)]
            assert len(requests) == 1 and "top-recordings" in requests[0][1]
            # A songs-only warm cache cannot hide the full page's album results.
            full = await popularity.get(client, ARTIST)
            assert [album["title"] for album in full["albums"]] == ["Studio"]
            assert full["ranked_album_ids"] == [RELEASE]
            before = list(requests)
            assert await router.warm_artist(ARTIST) == [("release", RELEASE)]
            assert await popularity.get(client, ARTIST) == full
            assert requests == before

    asyncio.run(run())


def test_background_popularity_album_details_yield_to_cover_warming_and_foreground(
    tmp_path, monkeypatch
):
    from server.catalogue import background_metadata

    store = Store(tmp_path)
    save_settings(store, False, True, listenbrainz_token=TOKEN)
    catalogue = Catalogue(store)
    popularity = Popularity(store, catalogue)
    order = []

    def transport(request):
        if request.url.host == "musicbrainz.org":
            order.append(request.url.path.rsplit("/", 1)[-1])
            return httpx.Response(
                200, json={"primary-type": "Album", "title": "Studio"}
            )
        return httpx.Response(
            200,
            json=(
                []
                if "top-recordings" in request.url.path
                else [
                    {
                        "release_group_mbid": RELEASE,
                        "release_group": {"type": "Album", "name": "Studio"},
                    }
                ]
            ),
        )

    async def run():
        started = {
            path: asyncio.Event()
            for path in [
                "release-group/" + RELEASE,
                "release/cover",
                "artist/foreground",
            ]
        }
        get = catalogue.get

        async def observed(client, path, **params):
            started[path].set()
            return await get(client, path, **params)

        monkeypatch.setattr(catalogue, "get", observed)

        async def queued(path, priority):
            # The cache is read off the loop first, so a started call joins
            # its priority's waiters a moment later.
            await asyncio.wait_for(started[path].wait(), 1)
            for _ in range(100):
                if catalogue.waiters[priority]:
                    return
                await asyncio.sleep(0.01)
            raise AssertionError(path + " never waited for its turn")

        await catalogue.lock.acquire()
        released = False
        tasks = []
        try:
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(transport)
            ) as client:

                async def cover():
                    token = background_metadata.set(True)
                    try:
                        return await catalogue.get(client, "release/cover")
                    finally:
                        background_metadata.reset(token)

                async def background_popularity():
                    token = background_metadata.set(True)
                    try:
                        return await popularity.get(client, ARTIST)
                    finally:
                        background_metadata.reset(token)

                tasks.append(asyncio.create_task(background_popularity()))
                await queued("release-group/" + RELEASE, 2)
                tasks.append(asyncio.create_task(cover()))
                await queued("release/cover", 1)
                tasks.append(
                    asyncio.create_task(catalogue.get(client, "artist/foreground"))
                )
                await queued("artist/foreground", 0)
                catalogue.lock.release()
                released = True
                await asyncio.wait_for(asyncio.gather(*tasks), 2)
        finally:
            if not released:
                catalogue.lock.release()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(run())
    assert order == ["foreground", "cover", RELEASE]


@pytest.mark.parametrize("interval", [0, 1.05])
def test_foreground_popularity_finishes_during_continuous_warming(tmp_path, interval):
    from server.catalogue import background_metadata

    store = Store(tmp_path)
    save_settings(store, False, True, listenbrainz_token=TOKEN)
    catalogue = Catalogue(store)
    catalogue.min_interval = interval
    popularity = Popularity(store, catalogue)
    warming_requests = []

    async def run():
        entered = asyncio.Event()
        stop = asyncio.Event()

        async def transport(request):
            if request.url.host == "musicbrainz.org":
                if "/release/warm-" in request.url.path:
                    warming_requests.append(request.url.path)
                    entered.set()
                await asyncio.sleep(0.05)
                return httpx.Response(
                    200, json={"primary-type": "Album", "title": "Studio"}
                )
            return httpx.Response(
                200,
                json=(
                    []
                    if "top-recordings" in request.url.path
                    else [
                        {
                            "release_group_mbid": RELEASE,
                            "release_group": {"type": "Album", "name": "Studio"},
                        }
                    ]
                ),
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:

            async def warm():
                token = background_metadata.set(True)
                try:
                    index = 0
                    while not stop.is_set():
                        # Unique uncached requests, as in a cold warming pass.
                        await catalogue.get(client, f"release/warm-{index}")
                        index += 1
                finally:
                    background_metadata.reset(token)

            warming_task = asyncio.create_task(warm())
            page = None
            try:
                await asyncio.wait_for(entered.wait(), 1)
                page = asyncio.create_task(popularity.get(client, ARTIST))
                done, _ = await asyncio.wait({page}, timeout=2.8)
                assert (
                    page in done
                ), "foreground popularity starved behind continuous warming"
                assert [album["title"] for album in page.result()["albums"]] == [
                    "Studio"
                ]
                assert not stop.is_set() and not warming_task.done()
                assert len(warming_requests) <= 2
            finally:
                stop.set()
                for task in (warming_task, page):
                    if task is not None:
                        task.cancel()
                await asyncio.gather(
                    *[task for task in (warming_task, page) if task is not None],
                    return_exceptions=True,
                )

    asyncio.run(run())


GROUPS = [f"2222222{n}-2222-4222-8222-222222222222" for n in range(5)]


def listenbrainz(requests):
    def transport(request):
        requests.append(request.url.path)
        if "top-recordings" in request.url.path:
            return httpx.Response(
                200,
                json=[
                    {
                        "artist_mbids": [ARTIST],
                        "recording_mbid": RELEASE,
                        "release_mbid": RELEASE,
                        "recording_name": "Song",
                        "release_name": "Album",
                        "total_listen_count": 50,
                    }
                ],
            )
        return httpx.Response(
            200,
            json=[
                {
                    "release_group_mbid": group,
                    "release_group": {"type": "Album", "name": group, "date": ""},
                    "total_listen_count": 100 - n,
                }
                for n, group in enumerate(GROUPS)
            ],
        )

    return transport


class SlowCatalogue:
    """MusicBrainz release-group details, one paced request each."""

    def __init__(self):
        self.fetched = []

    async def get(self, client, path):
        self.fetched.append(path)
        await asyncio.sleep(0)
        return {"primary-type": "Album", "title": path[-4:], "secondary-types": []}

    async def cached(self, path):
        return None


def test_an_artist_page_answers_before_every_album_is_looked_up(tmp_path, monkeypatch):
    """#21: a cold full popularity answer made up to 40 paced album-detail
    requests first (32-287 s live), past the phone's 20 s timeout. Past its
    budget the page gets what is known; the rest finish in the background."""
    import server.popularity as module

    monkeypatch.setattr(module, "ALBUM_SECONDS", -1, raising=False)
    store = Store(tmp_path)
    save_settings(store, True, True, listenbrainz_token=TOKEN)
    catalogue = SlowCatalogue()
    provider = Popularity(store, catalogue)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(listenbrainz([]))
        ) as client:
            first = await provider.get(client, ARTIST)
            assert [s["title"] for s in first["songs"]] == ["Song"]
            assert first["albums"] == [] and catalogue.fetched == []
            await asyncio.gather(*list(provider.completing.values()))
            assert len(catalogue.fetched) == len(GROUPS)
            later = await provider.get(client, ARTIST)
            assert len(later["albums"]) == len(GROUPS)

    asyncio.run(run())


def test_songs_only_and_full_requests_share_one_recordings_fetch(tmp_path):
    """#44: songs-only and full answers used separate locks, so concurrent
    requests both fetched the artist's recordings."""
    store = Store(tmp_path)
    save_settings(store, True, True, listenbrainz_token=TOKEN)
    requests = []
    provider = Popularity(store)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(listenbrainz(requests))
        ) as client:
            songs, full = await asyncio.gather(
                provider.get(client, ARTIST, include_albums=False),
                provider.get(client, ARTIST),
            )
            assert songs["songs"] == full["songs"] and full["albums"]
            assert sum("top-recordings" in r for r in requests) == 1

    asyncio.run(run())


def test_a_failed_songs_lookup_never_becomes_a_day_long_full_answer(tmp_path):
    """#44 (Codex round 1): a full answer reused a songs-only answer whose
    recordings lookup had failed, saving no songs for a day."""
    store = Store(tmp_path)
    save_settings(store, True, True, listenbrainz_token=TOKEN)
    requests = []
    working = listenbrainz(requests)
    outage = [True]

    def transport(request):
        if outage[0] and "top-recordings" in request.url.path:
            requests.append(request.url.path)
            return httpx.Response(503)
        return working(request)

    provider = Popularity(store)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            assert (await provider.get(client, ARTIST, include_albums=False))[
                "songs"
            ] == []
            outage[0] = False
            full = await provider.get(client, ARTIST)
            assert [s["title"] for s in full["songs"]] == ["Song"]

    asyncio.run(run())
