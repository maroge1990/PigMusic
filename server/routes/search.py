"""Search beyond the server (D-61): Apple's order for songs and albums not on
it, and the MusicBrainz identity a result opens as."""

import asyncio
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import search_match
from ..apple_search import AppleSearch
from ..library import normalise
from ..media import chart_region, settings


class AppleSong(BaseModel):
    apple_id: int
    title: str
    artist: str
    album: str = ""
    album_apple_id: int | None = None
    duration: float | None = None
    explicit: bool = False
    year: str = ""


class AppleAlbum(BaseModel):
    apple_id: int
    title: str
    artist: str
    track_count: int | None = None
    explicit: bool = False
    year: str = ""


class OnlineResults(BaseModel):
    songs: list[AppleSong]
    albums: list[AppleAlbum]
    # "busy": past Apple's request budget for now; "unavailable": Apple failed.
    state: Literal["ready", "busy", "unavailable"]


class Resolve(BaseModel):
    """Only the id: the title and artist come from Apple, not the device."""

    kind: Literal["song", "album"]
    # Inclusive bounds: the Swift generator reads only OpenAPI 3.0 limits.
    apple_id: int = Field(ge=1, le=10**15 - 1)


class Resolved(BaseModel):
    """The MusicBrainz recording (song) or release group (album) to open."""

    id: str
    name: str
    artist: str
    artist_id: str
    artist_name: str | None = None
    release_id: str | None = None
    album: str | None = None


# Songs looked up from a search's answer, once typing has paused this long
# (type-ahead sends a search at each pause; a newer one replaces it).
SEARCH_LOOK_AHEAD = 2
SEARCH_SETTLE_SECONDS = 1.5


def search_router(store, home, apple=None):
    router = APIRouter(prefix="/api/v1/search", tags=["personal-library"])
    apple = apple or AppleSearch()
    known_cache = {}

    def known():
        """Every song and album on the server, by normalised title and artist."""
        revision = store.library_revision
        if known_cache.get("revision") != revision:
            with store.library_lock:
                albums = list(store.library["albums"])
            names = set()
            for found in albums:
                names.add((normalise(found["title"]), normalise(found["artist"])))
                for track in found["tracks"]:
                    names.add(
                        (normalise(track["title"]), normalise(track.get("artist", "")))
                    )
            known_cache.update(revision=revision, names=frozenset(names))
        return known_cache["names"]

    @router.get("/online", response_model=OnlineResults)
    async def online(request: Request, q: str = ""):
        region = chart_region(await asyncio.to_thread(settings, store))
        names = await asyncio.to_thread(known)
        found = await apple.search(request.app.state.http, q, region, names)
        look_ahead(request, found)
        return found

    def look_ahead(request, found):
        """The top songs found are matched and looked up once typing has
        settled (#192): a tap on one then opens and plays at once."""
        user = getattr(request.state, "user", None)
        ahead = getattr(request.app.state, "look_ahead", None)
        if not user or ahead is None or not isinstance(found, dict):
            return
        client = request.app.state.http
        song = request.app.state.look_ahead_song

        def job(apple_id):
            async def run():
                item = await apple.item(client, "song", apple_id)
                if not item:
                    return None
                matched = await search_match.match(
                    store, home.catalogue, client, "song", item
                )
                if not matched or not matched.get("release_id"):
                    return None
                return await song(matched["release_id"], recording_id=matched["id"])()

            return run

        ahead.screen(
            user["id"],
            [
                job(item["apple_id"])
                for item in found.get("songs", [])[:SEARCH_LOOK_AHEAD]
                if item.get("apple_id")
            ],
            delay=SEARCH_SETTLE_SECONDS,
        )

    @router.post("/resolve", response_model=Resolved)
    async def resolve(body: Resolve, request: Request):
        client = request.app.state.http
        try:
            found = await apple.item(client, body.kind, body.apple_id)
            if not found:
                raise HTTPException(404, "No such Apple music")
            # The exact version, never Home's studio-first chart match (#103).
            item = await search_match.match(
                store, home.catalogue, client, body.kind, found
            )
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise HTTPException(502, "catalogue_unavailable")
        if not item:
            raise HTTPException(404, "No catalogue match")
        return item

    router.apple = apple
    return router
