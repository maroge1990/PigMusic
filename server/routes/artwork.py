"""Local artwork bytes and presentation metadata; provider aliases are delegated."""

import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response, FileResponse

from ..image_cache import SIZES, variant


def artwork_router(store, image_slots, enrichment, image_response):
    router = APIRouter()

    @router.get("/api/v1/covers/{filename}/metadata")
    async def artwork_metadata(request: Request, filename: str):
        sha = filename.removesuffix(".jpg")
        if filename != sha + ".jpg" or not store.covers.path(sha):
            raise HTTPException(404, "Artwork not found.")
        headers = {
            "Cache-Control": "private, max-age=31536000, immutable",
            "ETag": f'"{sha}-metadata-v1"',
        }
        if any(
            value.strip().removeprefix("W/") in {headers["ETag"], "*"}
            for value in request.headers.get("if-none-match", "").split(",")
        ):
            return Response(status_code=304, headers=headers)
        async with image_slots:
            value = await asyncio.to_thread(store.covers.metadata, sha)
        return JSONResponse(value, headers=headers)

    @router.get("/api/v1/covers/{filename}")
    async def cached_artwork(
        request: Request, filename: str, w: int = 0, soft: bool = False, v: int = 1
    ):
        if not filename.endswith(".jpg") or len(filename) != 68:
            # Old provider URLs are aliases, never immutable filenames.
            if Path(filename).name != filename or not filename.endswith(".jpg"):
                raise HTTPException(404, "Artwork not found.")
            return await image_response(store.cover_dir / filename, w, soft)
        target = store.covers.path(filename[:-4])
        if not target:
            raise HTTPException(404, "Artwork not found.")

        async def backfill_metadata():
            # Even a cached-metadata check reads appdata files off the loop.
            if await asyncio.to_thread(store.covers.presentation, filename[:-4]):
                return None
            return await asyncio.to_thread(store.covers.metadata, filename[:-4])

        if v != 1:
            raise HTTPException(400, "Unsupported image variant version.")
        if w and w not in SIZES:
            raise HTTPException(400, "Unsupported image size.")
        if soft and not w:
            raise HTTPException(400, "Blurred variants require a size.")
        enrichment.submit("metadata:" + filename[:-4], backfill_metadata)
        if w:
            async with image_slots:
                target = await asyncio.to_thread(variant, target, w, soft)
        etag = '"' + filename[:-4] + (f"-v1-{w}-{int(soft)}" if w else "") + '"'
        headers = {
            "Cache-Control": "private, max-age=31536000, immutable",
            "ETag": etag,
        }
        if any(
            value.strip().removeprefix("W/") in {etag, "*"}
            for value in request.headers.get("if-none-match", "").split(",")
        ):
            return Response(status_code=304, headers=headers)
        return FileResponse(
            target, media_type="image/webp" if w else "image/jpeg", headers=headers
        )

    @router.get("/api/v1/artwork/{sha}", tags=["personal-library"])
    async def native_artwork(request: Request, sha: str, w: int = 256):
        if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            raise HTTPException(404, "Artwork not found.")
        return await cached_artwork(request, sha + ".jpg", w=w)

    return router
