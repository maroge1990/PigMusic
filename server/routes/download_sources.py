"""Owner-only source diagnostics."""

import asyncio
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from ..providers.contracts import ProviderError


class SourceStatus(BaseModel):
    id: str
    name: str
    profiles: list[str]
    configured: bool
    enabled: bool
    metadata_reachable: bool | None
    last_transfer: str | None
    last_transfer_at: int | None
    reason: str | None
    next_retry_at: int | None


def download_sources_router(runtime):
    router = APIRouter(
        prefix="/api/v1/admin/download-sources", tags=["owner-downloads"]
    )
    # Bound active owner probes; a probe observes metadata only, never audio.
    slots = asyncio.Semaphore(1)

    @router.get("", response_model=list[SourceStatus])
    def statuses():
        return runtime.statuses()

    @router.post("/{source}/check", response_model=SourceStatus)
    async def check(source: str):
        async with slots:
            try:
                return await asyncio.to_thread(runtime.check, source)
            except KeyError:
                raise HTTPException(404, "Source not found")
            except ProviderError as exc:
                return JSONResponse(
                    {"detail": str(exc), "code": "source_" + exc.code}, status_code=503
                )

    return router
