"""Authenticated personal mix editions; cache administration remains web-only."""

import asyncio
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..api_models import MixList, PlaylistDTO, RadioBatch
from .personal import playlist_dto


class MixFeedback(BaseModel):
    track_id: str = Field(max_length=100)


class RadioFeedback(BaseModel):
    track_id: str = Field(max_length=100)
    action: str = Field(pattern="^(skip|like)$")


class RadioContinuation(BaseModel):
    """The last songs played and the queue, for similar songs (#202)."""

    seeds: list[str] = Field(default_factory=list, max_length=10)
    exclude: list[str] = Field(default_factory=list, max_length=500)


class MixCacheSettings(BaseModel):
    budget_bytes: int = Field(ge=1_000_000_000, le=2_000_000_000_000)
    reserve_bytes: int = Field(ge=1_000_000_000, le=100_000_000_000)
    profile: str = Field(pattern="^(mp3_320|native_standard|lossless)$")
    timezone: str = Field(max_length=100)


def mixes_router(mixes):
    router = APIRouter()

    @router.get("/api/v1/home/mixes", response_model=MixList, tags=["personal-library"])
    async def home_mixes(request: Request):
        return await asyncio.to_thread(mixes.list, request.state.user["id"])

    @router.get("/api/v1/radio", response_model=RadioBatch, tags=["personal-library"])
    async def radio(request: Request, after: str | None = Query(None, max_length=100)):
        result = await asyncio.to_thread(
            mixes.radio_next, request.state.user["id"], after
        )
        if result["preparing"]:
            mixes.wake.set()
        return result

    @router.post(
        "/api/v1/radio/continuation",
        response_model=RadioBatch,
        tags=["personal-library"],
    )
    async def radio_continuation(body: RadioContinuation, request: Request):
        seeds = [s[:128] for s in body.seeds]
        exclude = [s[:128] for s in body.exclude]
        result = await asyncio.to_thread(
            mixes.continuation, request.state.user["id"], seeds, exclude
        )
        if result["preparing"]:
            mixes.wake.set()
        return result

    @router.post("/api/v1/radio/feedback", tags=["personal-library"])
    async def radio_feedback(body: RadioFeedback, request: Request):
        try:
            await asyncio.to_thread(
                mixes.radio_feedback,
                request.state.user["id"],
                body.track_id,
                body.action,
            )
            return {"recorded": True}
        except LookupError:
            raise HTTPException(404, "Mix song not found")

    @router.post(
        "/api/v1/mixes/{edition_id}/save",
        response_model=PlaylistDTO,
        tags=["personal-library"],
    )
    async def save_mix(edition_id: str, request: Request):
        try:
            return playlist_dto(
                await asyncio.to_thread(
                    mixes.save, request.state.user["id"], edition_id
                )
            )
        except LookupError:
            raise HTTPException(404, "Mix not found")

    @router.post("/api/v1/mixes/hide", tags=["personal-library"])
    async def hide_mix_song(body: MixFeedback, request: Request):
        try:
            await asyncio.to_thread(mixes.hide, request.state.user["id"], body.track_id)
            return {"hidden": True}
        except LookupError:
            raise HTTPException(404, "Mix song not found")

    @router.post("/api/v1/mixes/{edition_id}/hide", tags=["personal-library"])
    async def hide_mix(edition_id: str, request: Request):
        try:
            await asyncio.to_thread(
                mixes.hide_edition, request.state.user["id"], edition_id
            )
            return {"hidden": True}
        except LookupError:
            raise HTTPException(404, "Mix not found")

    # These paths intentionally lack the personal-library tag.
    admin = APIRouter()

    @admin.get("/api/v1/admin/mixes")
    async def mix_status():
        return await asyncio.to_thread(mixes.cache.status)

    @admin.get("/api/v1/admin/mixes/people/{user_id}")
    async def mix_person(user_id: str):
        try:
            return await asyncio.to_thread(mixes.inspect, user_id)
        except LookupError:
            raise HTTPException(404, "Person not found")

    @admin.put("/api/v1/admin/mixes")
    async def mix_settings(body: MixCacheSettings):
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(body.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise HTTPException(422, "Choose a valid timezone")
        await asyncio.to_thread(
            mixes.store.write_document, "mix-settings", body.model_dump()
        )
        return await asyncio.to_thread(mixes.cache.status)

    router.include_router(admin)
    return router
