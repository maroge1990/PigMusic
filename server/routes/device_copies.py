"""Personal original/AAC download manifests and revocable Range delivery."""

import asyncio
import subprocess
from typing import Annotated, Literal
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, StringConstraints

# Songs asked for in one request (#219). The phone asks in fifties; a source is
# hashed only the first time, so a request stays inside the phone's timeout.
BATCH_LIMIT = 100


class LeasedFileResponse(FileResponse):
    def __init__(self, *args, release, **kwargs):
        super().__init__(*args, **kwargs)
        self.release = release

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            await asyncio.to_thread(self.release)


class DeviceCopyDTO(BaseModel):
    state: Literal["not_requested", "queued", "preparing", "ready", "failed"]
    quality: Literal["original", "aac"]
    sha256: str | None = None
    source_sha256: str
    bytes: int
    extension: str
    key: str


class DeviceCopiesRequest(BaseModel):
    track_ids: list[Annotated[str, StringConstraints(min_length=1, max_length=256)]] = (
        Field(min_length=1, max_length=BATCH_LIMIT)
    )
    quality: Literal["original", "aac"] = "aac"


class DeviceCopyStatusDTO(BaseModel):
    track_id: str
    # None: not in the person's library, or its file is unavailable.
    device_copy: DeviceCopyDTO | None = None


class DeviceCopiesDTO(BaseModel):
    copies: list[DeviceCopyStatusDTO]


def device_copy_router(copies):
    outer = APIRouter()
    router = APIRouter(prefix="/api/v1/tracks", tags=["personal-library"])
    # Not under /api/v1/tracks/: the media check there needs one track in the
    # path. This route checks each song's membership itself.
    batch = APIRouter(prefix="/api/v1/device-copies", tags=["personal-library"])

    @batch.post("", response_model=DeviceCopiesDTO)
    async def request_copies(body: DeviceCopiesRequest, request: Request):
        """Starts preparing many device copies in one request and reports each
        one's state (#219, D-113): the server converts them on its own, and
        the phone later downloads those that are ready. Only songs in the
        person's own library are answered, as for a single copy."""
        person = getattr(request.state, "user", None)
        if not person:
            raise HTTPException(401, "Sign in again.")
        wanted = list(dict.fromkeys(body.track_ids))

        def prepare():
            libraries = copies.store.user_libraries
            answers = []
            for track_id in wanted:
                value = None
                if libraries.contains(person["id"], track_id):
                    try:
                        value = copies.manifest(track_id, body.quality, True)
                    except (
                        FileNotFoundError,
                        ValueError,
                        OSError,
                        subprocess.SubprocessError,
                    ):
                        value = None
                answers.append({"track_id": track_id, "device_copy": value})
            return answers

        answers = await asyncio.to_thread(prepare)
        if any(
            answer["device_copy"]
            and answer["device_copy"]["state"] in ("queued", "preparing")
            for answer in answers
        ):
            copies.kick()
        return {"copies": answers}

    async def manifest(track_id, quality, create):
        try:
            result = await asyncio.to_thread(copies.manifest, track_id, quality, create)
            if result["state"] in ("queued", "preparing"):
                copies.kick()
            return result
        except FileNotFoundError:
            raise HTTPException(404, "File unavailable")
        except (ValueError, OSError, subprocess.SubprocessError):
            raise HTTPException(
                503, "Couldn't download. Try again or ask the administrator."
            )

    @router.post("/{track_id}/copy", response_model=DeviceCopyDTO)
    async def request_copy(
        track_id: str, quality: Literal["original", "aac"] = "original"
    ):
        return await manifest(track_id, quality, True)

    @router.get("/{track_id}/copy", response_model=DeviceCopyDTO)
    async def copy_status(
        track_id: str, quality: Literal["original", "aac"] = "original"
    ):
        return await manifest(track_id, quality, False)

    @router.get(
        "/{track_id}/copy/file",
        response_class=FileResponse,
        responses={
            200: {
                "content": {
                    "application/octet-stream": {
                        "schema": {"type": "string", "format": "binary"}
                    }
                }
            }
        },
    )
    async def copy_file(
        track_id: str, quality: Literal["original", "aac"] = "original"
    ):
        try:
            path, value = await asyncio.to_thread(copies.acquire, track_id, quality)
        except LookupError:
            raise HTTPException(409, "Device copy not ready")
        except (ValueError, OSError, subprocess.SubprocessError):
            raise HTTPException(404, "Device copy unavailable")
        return LeasedFileResponse(
            path,
            release=lambda: copies.release(value["key"]),
            media_type="application/octet-stream",
            headers={
                "ETag": '"' + value["sha256"] + '"',
                "Cache-Control": "private, no-store",
            },
        )

    outer.include_router(router)
    outer.include_router(batch)
    return outer
