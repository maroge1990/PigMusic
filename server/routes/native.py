"""Typed native library and authenticated live updates."""

import asyncio
import json
import time
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse, FileResponse
from pathlib import Path
from ..api_models import TrackPage, AlbumPage, ArtistPage, LibraryChanges
from ..native_sync import NativeSync, ResyncRequired
from ..auth import AuthError


# A comment line keeps idle streams open through proxies; tests shorten it.
KEEPALIVE_SECONDS = 25


def native_router(store, auth, health_snapshot=None, admin_snapshot=None):
    """`admin_snapshot` feeds the owner-only `admin` event: the full download
    queue and library scan, so the web Admin section needs no timers."""
    router = APIRouter(prefix="/api/v1", tags=["personal-library"])
    sync = NativeSync(store)
    connections = {}
    shared_admin = Shared(admin_snapshot, 1.5)
    shared_health = Shared(health_snapshot, 15)

    async def page(request, kind, cursor, limit):
        try:
            return await asyncio.to_thread(
                sync.page, request.state.user["id"], kind, cursor, limit
            )
        except ResyncRequired as exc:
            raise HTTPException(409, str(exc))
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    @router.get("/openapi.json", include_in_schema=False)
    def published_contract():
        return FileResponse(
            Path(__file__).resolve().parents[2] / "api/openapi.v1.json",
            media_type="application/json",
            headers={"Cache-Control": "private, no-cache"},
        )

    @router.get("/library/tracks", response_model=TrackPage)
    async def tracks(request: Request, cursor: str | None = None, limit: int = 200):
        return await page(request, "tracks", cursor, limit)

    @router.get("/library/albums", response_model=AlbumPage)
    async def albums(request: Request, cursor: str | None = None, limit: int = 200):
        return await page(request, "albums", cursor, limit)

    @router.get("/library/artists", response_model=ArtistPage)
    async def artists(request: Request, cursor: str | None = None, limit: int = 200):
        return await page(request, "artists", cursor, limit)

    @router.get("/library/changes", response_model=LibraryChanges)
    async def changes(request: Request, since: str):
        try:
            return await asyncio.to_thread(
                sync.changes, request.state.user["id"], since
            )
        except ResyncRequired as exc:
            raise HTTPException(410, str(exc))

    @router.get("/events")
    async def events(request: Request, admin: bool = False):
        """Live updates. Owners add `admin=true` to also receive the household
        queue and scan (`admin` events); other clients never pay for them."""
        session = request.state.session
        key, user_id = session["public_id"], request.state.user["id"]
        if connections.get(key, 0) >= 4:
            raise HTTPException(429, "event_connection_limit")
        # Reserve before returning the response; release even if the generator is cancelled.
        connections[key] = connections.get(key, 0) + 1

        owner = admin and request.state.user.get("role") == "owner" and admin_snapshot

        def session_valid():
            with store.connect() as conn:
                return conn.execute(
                    "SELECT 1 FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.public_id=? AND s.revoked_at IS NULL AND s.expires_at>? AND u.disabled=0",
                    (key, auth.now()),
                ).fetchone()

        async def stream():
            previous = {}
            heartbeat = time.monotonic()
            try:
                while not await request.is_disconnected():
                    # Revoked sessions cannot keep receiving events indefinitely.
                    # Every open stream checks every 2 s, and a busy writer can
                    # hold SQLite for seconds: check off the loop (LOOP1).
                    valid = await asyncio.to_thread(session_valid)
                    if not valid:
                        yield "event: revoked\ndata: {}\n\n"
                        return
                    summary = await asyncio.to_thread(
                        store.user_libraries.projection, user_id
                    )
                    queue = await asyncio.to_thread(store.user_libraries.queue, user_id)
                    # Every connection shares one health check and one Admin
                    # snapshot per interval, however many are open.
                    status = await shared_health.get() or {}
                    state = {
                        "revision": {"revision": summary["revision"]},
                        "queue": queue,
                        "health": (
                            {"storage_available": status["storage_available"]}
                            if status
                            else {}
                        ),
                    }
                    if owner:
                        state["admin"] = {
                            **await shared_admin.get(),
                            "soulseek": status.get("soulseek"),
                        }
                    for kind, value in state.items():
                        if previous.get(kind) != value:
                            yield f'event: {kind}\ndata: {json.dumps(value,separators=(",", ":"))}\n\n'
                            previous[kind] = value
                    if time.monotonic() - heartbeat >= KEEPALIVE_SECONDS:
                        yield ": keepalive\n\n"
                        heartbeat = time.monotonic()
                    await asyncio.sleep(2)
            finally:
                connections[key] -= 1
                if not connections[key]:
                    del connections[key]

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    return router


class Shared:
    """One `produce()` result shared by every caller for `seconds`: the work is
    done at most once per interval, not once per open connection."""

    def __init__(self, produce, seconds):
        self.produce, self.seconds = produce, seconds
        self.value, self.at = None, None
        self.lock = asyncio.Lock()

    async def get(self):
        if self.produce is None:
            return None
        async with self.lock:
            if self.at is None or time.monotonic() - self.at >= self.seconds:
                self.value = await asyncio.to_thread(self.produce)
                self.at = time.monotonic()
            return self.value
