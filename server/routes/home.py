"""Home uses each signed-in person's projection, with shared public provider caches."""

import asyncio
import logging
from time import monotonic
from fastapi import APIRouter, Request
from ..home import HomeDiscovery, RECOMMENDED_SOURCE
from ..api_models import HomeFeedDTO, RecentAlbums

log = logging.getLogger("pig_music.home")

# Recommended is built in the background (#96): a request gets the last good
# feed at once and starts a rebuild when it is this old. A first build gets
# this long to finish within the request; after that the request answers
# "preparing" and the build carries on.
REFRESH_AFTER = 3600
RETRY_AFTER = 300
FIRST_WAIT = 8
BUILD_TIMEOUT = 120


def empty_feed(state):
    return {
        "artists": [],
        "albums": [],
        "songs": [],
        "unavailable": state == "unavailable",
        "state": state,
        "source": RECOMMENDED_SOURCE,
    }


class RecommendedFeeds:
    """The last good Recommended feed per person, rebuilt in the background."""

    def __init__(self, home):
        self.home = home
        self.feeds = {}  # person: (built at, feed)
        self.building = {}  # person: task

    def rebuild(self, key, client, projection):
        task = self.building.get(key)
        if task is None:
            task = asyncio.create_task(self.build(key, client, projection))
            self.building[key] = task

            def finished(done, key=key):
                if self.building.get(key) is done:
                    del self.building[key]

            task.add_done_callback(finished)
        return task

    async def build(self, key, client, projection):
        try:
            feed = await asyncio.wait_for(
                self.home.recommended(client, await projection()), BUILD_TIMEOUT
            )
        except Exception as error:  # A failed build keeps the last good feed.
            log.info("Recommended build failed (%s)", type(error).__name__)
            feed = empty_feed("unavailable")
        previous = self.feeds.get(key)
        if feed.get("state") == "unavailable":
            # Try again in a few minutes, serving the last good feed meanwhile.
            built = monotonic() - REFRESH_AFTER + RETRY_AFTER
            if previous and previous[1].get("state") != "unavailable":
                feed = previous[1]
            self.feeds[key] = (built, feed)
        else:
            self.feeds[key] = (monotonic(), feed)
        return self.feeds[key][1]

    async def get(self, key, client, projection):
        saved = self.feeds.get(key)
        if saved:
            if monotonic() - saved[0] >= REFRESH_AFTER:
                self.rebuild(key, client, projection)
            return saved[1]
        task = self.rebuild(key, client, projection)
        try:
            # Shielded: a slow first build carries on for the next request.
            return await asyncio.wait_for(asyncio.shield(task), FIRST_WAIT)
        except asyncio.TimeoutError:
            return empty_feed("preparing")


def home_router(store, catalogue, home=None, warmer=None):
    router = APIRouter(tags=["personal-library"])
    home = home or HomeDiscovery(store, catalogue)
    recommended_feeds = RecommendedFeeds(home)

    def prepared(feed, request=None):
        # Albums shown on Home open at once: their pages are cached ahead.
        if warmer is not None and isinstance(feed, dict):
            warmer.prepare_albums(feed)
        # Its songs a tap would stream are looked up now (#192); the two
        # feeds are one screen.
        user = getattr(request.state, "user", None) if request else None
        look_ahead = getattr(request.app.state, "look_ahead", None) if user else None
        if look_ahead is not None and isinstance(feed, dict):
            song = request.app.state.look_ahead_song
            look_ahead.screen(
                user["id"],
                [
                    song(item["release_id"], recording_id=item["id"])
                    for item in feed.get("songs", [])
                    if item.get("release_id") and item.get("id")
                ],
                name="home",
            )
        return feed

    @router.get("/api/v1/home/popular", response_model=HomeFeedDTO)
    async def popular(request: Request):
        return prepared(await home.popular(request.app.state.http), request)

    @router.get("/api/v1/home/recent", response_model=RecentAlbums)
    async def recent(request: Request):
        return await asyncio.to_thread(
            store.user_libraries.recently_added, request.state.user["id"]
        )

    @router.get("/api/v1/home/recommended", response_model=HomeFeedDTO)
    async def recommended(request: Request):
        user = getattr(request.state, "user", None)

        async def projection():
            if not user:
                return store.library
            return await asyncio.to_thread(store.user_libraries.projection, user["id"])

        return prepared(
            await recommended_feeds.get(
                user["id"] if user else None, request.app.state.http, projection
            ),
            request,
        )

    return router
