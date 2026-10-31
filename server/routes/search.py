"""Search beyond the server (D-61): Apple's order for songs and albums not on
it, and the MusicBrainz identity a result opens as."""

import asyncio
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

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
    kind: Literal["song", "album"]
    apple_id: int = Field(gt=0, lt=10**15)
    title: str = Field(min_length=1, max_length=300)
    artist: str = Field(min_length=1, max_length=300)


class Resolved(BaseModel):
    """The MusicBrainz recording (song) or release group (album) to open."""

    id: str
    name: str
    artist: str
    artist_id: str
    artist_name: str | None = None
    release_id: str | None = None
    album: str | None = None


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
        return await apple.search(request.app.state.http, q, region, names)

    @router.post("/resolve", response_model=Resolved)
    async def resolve(body: Resolve, request: Request):
        entry = {"id": body.apple_id, "name": body.title, "artistName": body.artist}
        try:
            item = await home.match_entry(
                request.app.state.http, body.kind + "s", entry
            )
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise HTTPException(502, "catalogue_unavailable")
        if not item:
            raise HTTPException(404, "No catalogue match")
        return item

    router.apple = apple
    return router
