import asyncio
import hashlib
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import (
    JSONResponse,
    FileResponse,
    Response,
    RedirectResponse,
)
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from .auth import Auth
from .auth_logging import install_validation_redaction
from .routes.auth import auth_router, set_cookie
from .routes.personal import personal_router
from .security import Security, audit_routes
from .request_limits import JsonBodyLimit
from .request_timing import (
    RequestTiming,
    StallWatch,
    diagnostics as request_diagnostics,
    mark,
    phase,
    watch_event_loop,
)
from .transport import public_origin
from .enrichment import Enrichment
from .image_cache import public_image
from .routes.artwork import artwork_router
from .device_copies import DeviceCopies
from .routes.device_copies import device_copy_router
from .routes.household import household_router
from .portraits import portrait_target
from .library_artwork import portraits_waiting
from .tidy import Tidy
from .artist_backgrounds import background_target
from pydantic import BaseModel, Field

from .library import identifier
from .fanart import Fanart, best as best_fanart, download as fanart_download
from .storage import download_root
from .store import Store
from .downloader import Downloader
from .providers.runtime import Runtime as SourceRuntime
from .routes.download_sources import download_sources_router
from .catalogue import (
    Catalogue,
    mbid,
    release_tracks,
    background_metadata,
)
from .soulseek import Soulseek
from .artwork import Artwork
from .portraits import Portraits
from .popularity import Popularity
from .artist_backgrounds import Backgrounds
from .artist_logos import Logos
from .home import HomeDiscovery
from .warmer import Warmer
from .image_cache import variant
from .media import (
    DescriptorFileResponse,
    open_playback,
    playable,
    public_settings,
    track_file,
    saved_track,
    saved_track_ids,
    settings as private_media_settings,
)
from .remote import LABELS, RemoteAudio
from .scanner import LibraryScanner
from .playlists import Playlists
from .exporter import Exporter
from .artist_jobs import ArtistJobs
from .recovery import Recovery
from .organisation import Organisation

ROOT = Path(__file__).resolve().parent.parent


def create_app(data_dir: Path | None = None, client_dir: Path | None = None):
    https_origin = public_origin()
    store = Store(
        data_dir or Path(os.environ.get("PIG_MUSIC_DATA", ROOT / ".localdata"))
    )
    device_copies = DeviceCopies(store)
    music_catalogue = Catalogue(store)
    artwork = Artwork(store, music_catalogue)
    fanart = Fanart(store)
    portraits = Portraits(store, music_catalogue, artwork, fanart)
    popularity = Popularity(store, music_catalogue)
    backgrounds = Backgrounds(store, artwork, fanart)
    logos = Logos(store, fanart)
    source_runtime = SourceRuntime(store)
    remote_audio = RemoteAudio(
        directory=store.directory / "remote-cache", runtime=source_runtime
    )
    scanner = LibraryScanner(store)
    playlists = Playlists(store)
    exporter = Exporter(store)
    artist_jobs = ArtistJobs(store, music_catalogue)
    recovery = Recovery(store)
    organisation = Organisation(store, recovery)
    tidy = Tidy(store, recovery, organisation)
    scan_task = None
    preload_stopping = asyncio.Event()
    image_revision = 0

    def images_changed(library=True):
        """Every new image bumps the revision clients refetch artwork by; only
        a library cover or portrait rebuilds the library (1-2 s, #79)."""
        nonlocal image_revision
        image_revision += 1
        if library:
            store.refresh_library()

    enrichment = Enrichment(images_changed)
    image_slots = asyncio.Semaphore(2)

    def feed_queue():
        return {
            **store.queue(),
            "artwork_revision": image_revision,
            "media": public_settings(store),
        }

    def feed_health():
        try:
            download_root(store)
            storage_available = True
        except (OSError, ValueError):
            storage_available = False
        return {
            "storage_available": storage_available,
            "soulseek": Soulseek(store).health(),
        }

    @asynccontextmanager
    async def lifespan(app):
        nonlocal scan_task
        if not security.legacy_test and not await asyncio.to_thread(auth.has_owner):
            # Intentional one-time operator handover; never log submitted passwords.
            print(
                "PigMusic first-run setup: open the private HTTPS WebUI, then enter this installation code:",
                flush=True,
            )
            print(security.installation_code(), flush=True)
            print(
                "This code stops working once the administrator account is created. Keep it private.",
                flush=True,
            )
        async with httpx.AsyncClient(
            timeout=12,
            headers={
                "User-Agent": "PigMusic/0.1 (private household prototype)",
                "Accept": "application/json",
            },
        ) as client:
            app.state.http = client
            enrichment.start()
            await asyncio.to_thread(recovery.recover)
            await asyncio.to_thread(organisation.recover)
            await asyncio.to_thread(tidy.recover)
            from .editions import EditionResolver

            downloader = Downloader(
                store,
                runtime=source_runtime,
                resolver=EditionResolver(store, music_catalogue, artwork, client),
            )
            app.state.downloader = downloader
            task = asyncio.create_task(downloader.run())
            export_task = asyncio.create_task(exporter.run())
            artist_task = asyncio.create_task(artist_jobs.run(client))
            preload_task = asyncio.create_task(preload_library())
            if os.environ.get("PIG_MUSIC_PRELOAD", "true").lower() != "false":
                warmer.start()
                mixes.start(client)
            maintenance_task = asyncio.create_task(maintain_artwork())
            stall_watch = StallWatch()
            stall_watch.start()
            loop_watch = asyncio.create_task(watch_event_loop(stall=stall_watch))
            if (
                scanner.root.is_dir()
                and not (store.directory / "library-snapshot.json").exists()
            ):
                scan_task = asyncio.create_task(asyncio.to_thread(scanner.run))
            try:
                yield
            finally:
                await device_copies.close()
                # Signal every worker before waiting on any of them.
                mixes.stopping.set()
                downloader.stopping.set()
                scanner.stopping.set()
                exporter.stopping.set()
                artist_jobs.stopping.set()
                await mixes.close()
                await artist_task
                preload_stopping.set()
                preload_task.cancel()
                await warmer.close()
                await enrichment.close()
                maintenance_task.cancel()
                loop_watch.cancel()
                stall_watch.stop()
                await task
                await export_task
                try:
                    await preload_task
                except asyncio.CancelledError:
                    pass
                for watcher in (maintenance_task, loop_watch):
                    try:
                        await watcher
                    except asyncio.CancelledError:
                        pass
                if scan_task:
                    await scan_task

    async def maintain_artwork():
        # Wait a week after startup; never prune during startup migration.
        while True:
            await asyncio.sleep(7 * 86400)
            await asyncio.to_thread(store.prune_artwork)

    auth = Auth(store)
    security = Security(
        auth, legacy_test=os.environ.get("PIG_MUSIC_TEST_LEGACY_API") == "1"
    )
    guard = security.current_user
    app = FastAPI(
        title="PigMusic",
        version="0.1.0",
        lifespan=lifespan,
        dependencies=[Depends(guard)],
        # Internet-facing: the schema is served only behind sign-in at /api/v1/openapi.json.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.auth = auth
    app.state.security = security
    app.include_router(auth_router(auth, security, https_origin))
    app.include_router(household_router(store))
    from .routes.native import native_router

    app.include_router(
        native_router(
            store,
            auth,
            feed_health,
            lambda: {"queue": feed_queue(), "scan": scanner.status()},
        )
    )
    app.include_router(device_copy_router(device_copies))
    app.state.device_copies = device_copies
    from .routes.discover import discover_router

    from .routes.home import home_router

    home = HomeDiscovery(store, music_catalogue)
    from .mixes import Mixes
    from .routes.mixes import mixes_router

    mixes = Mixes(store, home, source_runtime)
    app.include_router(mixes_router(mixes))
    warmer = Warmer(store, home, lambda: app.state.http)
    app.include_router(home_router(store, music_catalogue, home, warmer))
    discover_routes = discover_router(
        store, music_catalogue, popularity, warmer, lambda: app.state.http
    )
    app.include_router(discover_routes)
    warmer.warm_album = discover_routes.warm_album
    app.include_router(personal_router(store, artist_jobs, recovery))
    install_validation_redaction(app)
    app.state.store = store
    app.state.source_runtime = source_runtime
    app.state.enrichment = enrichment
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.add_middleware(JsonBodyLimit)

    @app.middleware("http")
    async def headers(request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and origin.rstrip("/") not in {
                https_origin,
                str(request.base_url).rstrip("/"),
            }:
                return JSONResponse(
                    {
                        "detail": "Cross-origin writes are not allowed.",
                        "code": "invalid_origin",
                    },
                    status_code=403,
                )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob: https://*.saavncdn.com; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'"
        )
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000"

        if request.url.path.startswith("/api/") and not request.url.path.startswith(
            ("/api/v1/covers/", "/api/v1/library-artwork/")
        ):
            response.headers.setdefault("Cache-Control", "no-store")
        if request.url.path.startswith("/assets/") and response.status_code in {
            200,
            304,
        }:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        if getattr(request.state, "refresh_cookie", None):
            # A renewed sign-in cookie must never reach a shared cache (audit
            # S1): a response it rides on is private, or made uncacheable.
            directives = {
                d.strip().split("=")[0].lower()
                for d in response.headers.get("Cache-Control", "").split(",")
            }
            if not directives & {"private", "no-store"}:
                response.headers["Cache-Control"] = "private, no-store"
            set_cookie(response, request.state.refresh_cookie)
        return response

    # Added last, so it is outermost: its times include the middleware above.
    app.add_middleware(RequestTiming)

    from fastapi.exceptions import RequestValidationError
    from starlette.exceptions import HTTPException as StarletteHTTPException

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        code = (
            exc.detail
            if isinstance(exc.detail, str) and exc.detail.replace("_", "").isalnum()
            else {
                400: "invalid_request",
                401: "authentication_required",
                403: "forbidden",
                404: "not_found",
                409: "conflict",
                410: "resync_required",
                413: "request_too_large",
                422: "invalid_request",
                503: "setup_required",
            }.get(exc.status_code, "request_failed")
        )
        return JSONResponse(
            {"detail": str(exc.detail), "code": code},
            status_code=exc.status_code,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse(
            {"detail": "Check the submitted fields", "code": "invalid_request"},
            status_code=422,
        )

    @app.get("/api/health")
    def health(request: Request):
        try:
            download_root(store)
            storage_available = True
        except (OSError, ValueError):
            storage_available = False
        if not security.legacy_test:
            return {
                "status": "ok",
                "https": request.url.scheme == "https",
                "storage_available": storage_available,
            }
        return {
            "status": "ok",
            "https": request.url.scheme == "https",
            "public_origin": https_origin,
            "mode": (
                "direct_library"
                if os.environ.get("PIG_MUSIC_DOWNLOAD_ROOT")
                else "isolated_library"
            ),
            "storage_available": storage_available,
            "downloads_enabled": store.download_settings()["enabled"]
            and storage_available,
            "library_snapshot": store.library["snapshot"],
            "library_stats": store.library["stats"],
        }

    @app.post("/api/v1/admin/albums/{album_id}/remove")
    async def remove_album(album_id: str):
        album = store.albums_by_id.get(album_id)
        if not album:
            raise HTTPException(404, "Album not found.")
        count = 0
        for track in list(album["tracks"]):
            try:
                await asyncio.to_thread(recovery.remove, track["id"])
                count += 1
            except (ValueError, OSError):
                raise HTTPException(
                    409,
                    f"Removed {count} songs; remaining files need review. All removed songs are recoverable.",
                )
        return {"removed": count}

    @app.post("/api/v1/admin/artists/{artist_id}/remove")
    async def remove_artist(artist_id: str):
        artist = store.artists_by_id.get(artist_id)
        if not artist:
            raise HTTPException(404, "Artist not found.")
        identity = await library_artist_catalogue(artist_id)
        # The artist lock can wait behind a submission on lifecycle_lock.
        await asyncio.to_thread(artist_jobs.cancel, identity["id"])
        count = 0
        for album in [
            a for a in store.library["albums"] if a["artist_id"] == artist_id
        ]:
            for track in list(album["tracks"]):
                try:
                    await asyncio.to_thread(recovery.remove, track["id"])
                    count += 1
                except (ValueError, OSError):
                    raise HTTPException(
                        409,
                        f"Removed {count} songs; remaining files need review. All removed songs are recoverable.",
                    )
        return {"removed": count, "monitor": False}

    class OrganiseRequest(BaseModel):
        track_ids: list[str] = Field(max_length=5000)
        tags: bool = False

    @app.get("/api/v1/admin/library/organisation/history")
    def organisation_history():
        return [
            {"id": r["id"], "title": r["track"]["title"], "status": r["status"]}
            for r in organisation.history()
            if r["status"] in {"completed", "undoing"}
        ]

    @app.post("/api/v1/admin/library/organisation/{identity}/undo")
    async def undo_organisation(identity: str):
        if scan_task and not scan_task.done():
            raise HTTPException(
                409, "Wait for the library scan before undoing organisation."
            )
        if not scanner.lock.acquire(blocking=False):
            raise HTTPException(
                409, "Wait for the library scan before undoing organisation."
            )
        try:
            return await asyncio.to_thread(organisation.undo, identity)
        except (ValueError, OSError):
            raise HTTPException(
                409, "Undo stopped; original backup retained. Check the destination."
            )
        finally:
            scanner.lock.release()

    @app.get("/api/v1/admin/library/organisation")
    async def organisation_plan():
        return await asyncio.to_thread(organisation.plan)

    @app.post("/api/v1/admin/library/organisation")
    async def organisation_apply(body: OrganiseRequest):
        if scan_task and not scan_task.done():
            raise HTTPException(
                409, "Wait for the library scan before organising files."
            )
        if any(
            i["status"] in {"searching", "downloading", "validating", "importing"}
            for i in store.queue()["items"]
        ):
            raise HTTPException(
                409, "Wait for active downloads before organising files."
            )
        if not scanner.lock.acquire(blocking=False):
            raise HTTPException(
                409, "Wait for the library scan before organising files."
            )
        try:
            return await asyncio.to_thread(
                organisation.apply, body.track_ids, body.tags
            )
        except (ValueError, OSError):
            raise HTTPException(
                409,
                "Organisation stopped; originals and recovery records are retained. Review the selected files.",
            )
        finally:
            scanner.lock.release()

    @app.get("/api/v1/admin/library/trash")
    def removed_music():
        return [
            {
                k: v
                for k, v in record.items()
                if k not in {"root", "original", "trash", "sha256"}
            }
            for record in recovery.list()
            if record["status"] != "restored"
        ]

    @app.post("/api/v1/admin/tracks/{track_id}/remove")
    async def remove_song(track_id: str):
        try:
            record = await asyncio.to_thread(recovery.remove, track_id)
            return {"id": record["id"], "status": record["status"]}
        except (ValueError, OSError, FileNotFoundError):
            raise HTTPException(
                409,
                "Removal stopped; check the library mount and file. Existing recovery data is retained.",
            )

    @app.get("/api/v1/admin/library/duplicates")
    async def duplicate_songs():
        from .duplicates import find

        return {"duplicates": await asyncio.to_thread(find, store)}

    class DuplicateRemoval(BaseModel):
        track_ids: list[str] = Field(min_length=1, max_length=2000)

    @app.post("/api/v1/admin/library/duplicates/remove")
    async def remove_duplicates(body: DuplicateRemoval):
        from .duplicates import remove

        return await asyncio.to_thread(remove, store, recovery, body.track_ids)

    @app.get("/api/v1/admin/library/tidy")
    async def tidy_plan():
        plan = await asyncio.to_thread(tidy.plan)
        history = await asyncio.to_thread(tidy.history)
        return {
            **plan,
            "history": [
                {
                    k: r[k]
                    for k in ("id", "artist", "title", "time", "status", "unresolved")
                }
                | {"error": r.get("error")}
                for r in history
            ],
        }

    class TidyRequest(BaseModel):
        fingerprint: str = Field(max_length=64)

    def files_settled(action):
        if scan_task and not scan_task.done():
            raise HTTPException(409, f"Wait for the library scan before {action}.")
        if any(
            i["status"] in {"searching", "downloading", "validating", "importing"}
            for i in store.queue()["items"]
        ):
            raise HTTPException(409, f"Wait for active downloads before {action}.")
        if not scanner.lock.acquire(blocking=False):
            raise HTTPException(409, f"Wait for the library scan before {action}.")

    @app.post("/api/v1/admin/library/tidy/{album_id}")
    async def tidy_album(album_id: str, body: TidyRequest):
        files_settled("tidying albums")
        try:
            record = await asyncio.to_thread(tidy.apply, album_id, body.fingerprint)
            return {"id": record["id"], "status": record["status"]}
        except (ValueError, OSError) as error:
            raise HTTPException(
                409,
                f"Tidy-up stopped: {error} Every file moved so far is recoverable; scan the library and review again.",
            )
        finally:
            scanner.lock.release()

    @app.post("/api/v1/admin/library/tidy/history/{identity}/undo")
    async def undo_tidy(identity: str):
        files_settled("undoing a tidy-up")
        try:
            record = await asyncio.to_thread(tidy.undo, identity)
        except (ValueError, OSError) as error:
            raise HTTPException(
                409,
                f"Undo stopped: {error} Recoverable files are retained; check the album and try Undo again.",
            )
        finally:
            scanner.lock.release()
        if record["status"] != "undone":
            # Never reported as put back while changes await the owner.
            raise HTTPException(409, record["error"])
        return {"id": record["id"], "status": record["status"]}

    class TidyResolution(BaseModel):
        include: bool

    @app.post("/api/v1/admin/library/tidy/history/{identity}/unresolved/{operation}")
    async def resolve_tidy(identity: str, operation: str, body: TidyResolution):
        try:
            record = await asyncio.to_thread(
                tidy.resolve, identity, operation, body.include
            )
        except ValueError as error:
            raise HTTPException(409, str(error))
        return {"id": record["id"], "status": record["status"]}

    @app.post("/api/v1/admin/library/trash/{identity}/restore")
    async def restore_song(identity: str):
        try:
            record = await asyncio.to_thread(recovery.restore, identity)
            return {"id": record["id"], "status": record["status"]}
        except (ValueError, OSError):
            raise HTTPException(
                409,
                "Restore stopped; check the library mount and destination. Recoverable music is retained.",
            )

    @app.get("/api/v1/admin/library")
    def library(request: Request):
        # Playlist count/settings are part of the response, so they enter the validator.
        with store.library_lock:
            with store.connect() as conn:
                playlist_count = conn.execute(
                    "SELECT COUNT(*) FROM playlists"
                ).fetchone()[0]
            signature = (
                store.library_revision,
                playlist_count,
                json.dumps(public_settings(store), sort_keys=True),
            )
            if not store.library_payload or store.library_payload[0] != signature:
                payload = json.dumps(
                    store.summary(),
                    separators=(",", ":"),
                ).encode()
                etag = 'W/"' + hashlib.sha256(payload).hexdigest() + '"'
                store.library_payload = (signature, payload, etag)
            _, payload, etag = store.library_payload
        headers = {
            "ETag": etag,
            "Cache-Control": "private, no-cache",
            "Vary": "Accept-Encoding",
        }
        candidates = request.headers.get("if-none-match", "").split(",")
        if any(
            value.strip().removeprefix("W/") in {etag.removeprefix("W/"), "*"}
            for value in candidates
        ):
            return Response(status_code=304, headers=headers)
        return Response(payload, media_type="application/json", headers=headers)

    @app.get("/api/v1/admin/library/scan")
    def scan_status():
        return scanner.status()

    @app.get("/api/v1/admin/library/export-plan")
    def export_plan():
        return exporter.plan()

    @app.post("/api/v1/admin/library/scan")
    async def scan_library():
        nonlocal scan_task
        if scan_task and not scan_task.done():
            raise HTTPException(409, "A library scan is already running.")
        if scanner.lock.locked():
            raise HTTPException(409, "Wait for library organisation before scanning.")
        scanner.report(status="scanning", files=0)
        scan_task = asyncio.create_task(asyncio.to_thread(scanner.run))
        return scanner.status()

    class PlaylistName(BaseModel):
        name: str = Field(min_length=1, max_length=100)

    class SongReference(BaseModel):
        playlist_id: str | None = None
        position: int | None = Field(default=None, ge=0)
        saved_id: str | None = None
        release_id: str | None = None
        track_id: str | None = None
        favourite: bool | None = None

    async def resolve_song(body):
        if body.saved_id:
            track, _ = saved_track(store, body.saved_id)
            if not track:
                raise HTTPException(404, "Song not found.")
        elif body.playlist_id and body.position is not None:
            try:
                track = playlists.raw(body.playlist_id)["tracks"][body.position]
            except (ValueError, IndexError):
                raise HTTPException(404, "Playlist song not found.")
        elif body.release_id and body.track_id:
            track = next(
                (
                    t
                    for t in release_tracks(await release_data(body.release_id))
                    if t["id"] == body.track_id
                ),
                None,
            )
            if not track:
                raise HTTPException(404, "Song not found in this release.")
        else:
            raise HTTPException(400, "Choose a saved song or a release track.")
        return {
            k: v
            for k, v in track.items()
            if k
            in {
                "id",
                "artist",
                "title",
                "album",
                "album_artist",
                "year",
                "duration",
                "disc_number",
                "track_number",
                "musicbrainz_release_id",
                "musicbrainz_recording_id",
            }
        }

    async def image_response(target, w=0, soft=False):
        if not target or not target.is_file():
            return Response(status_code=404, headers={"Cache-Control": "no-store"})
        async with image_slots:
            target = await asyncio.to_thread(public_image, store, target)
            if not target:
                return Response(status_code=404, headers={"Cache-Control": "no-store"})
            # Alias requests revalidate; redirect only to immutable local bytes.
            suffix = f"?w={w}&soft={int(soft)}" if w else ""
            return RedirectResponse(
                "/api/v1/covers/" + target.name + suffix,
                headers={"Cache-Control": "private, no-cache"},
            )

    def image_config():
        return hashlib.sha256(
            json.dumps(private_media_settings(store), sort_keys=True).encode()
        ).hexdigest()[:12]

    def library_image_key(kind, item):
        return "library:" + kind + ":" + item["id"] + ":" + image_config()

    async def enrich_library(kind, item):
        if kind == "albums":
            return await artwork.cover(app.state.http, item)
        identity = None
        if public_settings(store)["portraits"]:
            try:
                identity = await library_artist_catalogue(item["id"])
            except HTTPException:
                pass
            target = await portraits.get(
                app.state.http, item["name"], identity.get("id") if identity else None
            )
            if target:
                await asyncio.to_thread(public_image, store, target)
                return target
        return await artwork.artist(app.state.http, item)

    def artist_portrait(item_id, name):
        """The artist's fetched portrait when portraits are on and it exists."""
        identity = store.read_document("library-artist:" + item_id)
        portrait = portrait_target(store, name, identity.get("id"))
        if public_settings(store)["portraits"] and portrait.is_file():
            return portrait
        return None

    @app.get("/api/v1/library-artwork/{kind}/{item_id}")
    async def library_artwork(kind: str, item_id: str, request: Request, w: int = 0):
        if kind not in {"albums", "artists"}:
            raise HTTPException(404, "Artwork not found.")
        item = (store.albums_by_id if kind == "albums" else store.artists_by_id).get(
            item_id
        )
        if item is None:
            raise HTTPException(404, "Library item not found.")
        person = getattr(request.state, "user", None)
        if person and person["role"] != "owner":
            access = await asyncio.to_thread(
                store.user_libraries.artwork_access, person["id"]
            )
            item = access["items"][kind].get(item_id)
            if item is None:
                raise HTTPException(404, "Library item not found.")
        target = artwork.target(item) if kind == "albums" else None
        if kind == "artists":
            # SQLite, settings and a cover-store stat: off the loop (LOOP1).
            target = await asyncio.to_thread(artist_portrait, item_id, item["name"])
            if not target:
                fallback = item.get("fallback_cover_url") or item.get("cover_url") or ""
                if fallback.startswith("/api/v1/covers/"):
                    target = store.cover_dir / fallback.split("/")[-1].split("?")[0]
                elif item.get("cover_album_id") in store.albums_by_id:
                    # Album art fetched lazily under its own alias.
                    target = artwork.target(store.albums_by_id[item["cover_album_id"]])
        enrichment.submit(
            library_image_key(kind, item),
            lambda: enrich_library(kind, item),
            library=True,
        )
        return await image_response(target, w)

    @app.get("/api/v1/catalogue/artwork/{kind}/{item_id}", tags=["personal-library"])
    async def catalogue_artwork(kind: str, item_id: str, w: int = 0):
        if kind not in {"release", "release-group"}:
            raise HTTPException(404, "Artwork not found.")
        item_id = valid_id(item_id)
        key = "catalogue-artwork:" + kind + ":" + item_id
        target = store.cover_dir / (identifier(key) + ".jpg")
        if not target.is_file():
            enrichment.submit(key, lambda: catalogue_cover(kind, item_id, target))
        return await image_response(target, w)

    async def catalogue_cover(kind, item_id, target):
        found = await artwork.catalogue_cover(app.state.http, kind, item_id)
        if found:
            return found
        # Cover Art Archive has nothing: Fanart often has the album cover.
        config = private_media_settings(store)
        token = config.get("fanart_key")
        if not token or not config.get("portraits"):
            return None
        try:
            group_id = item_id
            if kind == "release":
                release = await music_catalogue.get(
                    app.state.http, "release/" + item_id, inc="release-groups"
                )
                group_id = mbid(release["release-group"]["id"])
            images = await fanart.album(app.state.http, group_id, token)
            cover = best_fanart(images.get("albumcover"))
            if not cover:
                return None
            content = await fanart_download(app.state.http, cover["url"])
            await asyncio.to_thread(artwork.save, content, target)
            return target
        except Exception:
            # Optional enrichment; the placeholder cover remains.
            return None

    class Category(BaseModel):
        category: str

    @app.get("/api/v1/admin/albums/{album_id}")
    def household_album(album_id: str):
        """A household album with its tracks, for owner tools."""
        match = store.albums_by_id.get(album_id)
        if match is None:
            raise HTTPException(404, "Album not found.")
        return match

    @app.put("/api/v1/admin/albums/{album_id}/category")
    def category(album_id: str, body: Category):
        try:
            return store.categorise(album_id, body.category)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/v1/admin/migration")
    def migration():
        return store.migration()

    @app.post("/api/v1/admin/migration/import")
    async def import_export(request: Request):
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > 20_000_000:
                raise HTTPException(413, "Choose an export smaller than 20 MB.")
            chunks.append(chunk)
        try:
            return await asyncio.to_thread(store.import_apple, b"".join(chunks))
        except (ValueError, TypeError, OverflowError) as exc:
            raise HTTPException(400, str(exc)) from exc

    class Selection(BaseModel):
        track_ids: list[str] = Field(max_length=5000)
        selected: bool

    @app.post("/api/v1/admin/migration/selection")
    def selection(body: Selection):
        try:
            return store.update_selection(body.track_ids, body.selected)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/v1/admin/migration/plan")
    def plan():
        return Response(
            json.dumps(store.plan(), indent=2, ensure_ascii=False),
            media_type="application/json",
            headers={
                "Content-Disposition": 'attachment; filename="pig-music-migration-plan.json"'
            },
        )

    class Decision(BaseModel):
        track_id: str = Field(max_length=128)
        decision: str

    @app.post("/api/v1/admin/migration/decision")
    def decide(body: Decision):
        try:
            return store.decide(body.track_id, body.decision)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/v1/admin/migration/submit")
    def submit():
        try:
            return store.submit_migration()
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/v1/admin/queue")
    def queue():
        return {
            **store.queue(),
            "artwork_revision": image_revision,
            "media": public_settings(store),
        }

    @app.post("/api/v1/admin/queue/clear")
    def clear_queue(finished: bool = False):
        return store.clear_queue(finished_only=finished)

    app.include_router(download_sources_router(source_runtime))

    @app.get("/api/v1/admin/settings/downloads")
    def download_settings():
        return store.download_settings()

    @app.get("/api/v1/admin/access")
    def admin_access():
        # Owner-only by default: members only reach routes tagged personal-library.
        return {"public_origin": https_origin or None}

    @app.get("/api/v1/admin/settings/soulseek")
    async def soulseek_health():
        return await asyncio.to_thread(Soulseek(store).health)

    class DownloadSettings(BaseModel):
        enabled: bool
        profile: str
        sources: list[str]
        concurrent_downloads: int = Field(default=3, ge=1, le=6)
        fallback_profile: str = "mp3_320"
        fallback_minutes: int = Field(default=10, ge=1, le=60)
        recheck_hours: int = Field(default=24, ge=1, le=168)

    @app.put("/api/v1/admin/settings/downloads")
    def save_download_settings(body: DownloadSettings):
        try:
            return store.save_download_settings(body.model_dump())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/v1/admin/queue/{request_id}/retry")
    def retry(request_id: str):
        try:
            return store.retry_request(request_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/v1/admin/queue/{request_id}/source-review/{decision}")
    def review_source(request_id: str, decision: Literal["approve", "reject"]):
        try:
            return store.review_source(request_id, decision == "approve")
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/v1/admin/queue/{request_id}/cancel")
    def cancel(request_id: str):
        try:
            return store.cancel_request(request_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    from .routes.catalogue import catalogue_router

    catalogue_routes, library_artist_catalogue, valid_id, release_data = (
        catalogue_router(
            store,
            music_catalogue,
            artist_jobs,
            popularity,
            backgrounds,
            portraits,
            artwork,
            enrichment,
            image_response,
            image_config,
            lambda: app.state.http,
            logos,
        )
    )
    app.include_router(catalogue_routes)
    warmer.warm_artist = catalogue_routes.warm_artist
    warmer.library_identity = catalogue_routes.library_identity

    def show_new_portraits():
        if portraits_waiting(store):
            images_changed()

    warmer.after_pass = show_new_portraits

    async def warm_cover(kind, item_id):
        """Fetch a catalogue cover and its common sizes; True when new."""
        key = "catalogue-artwork:" + kind + ":" + item_id
        target = store.cover_dir / (identifier(key) + ".jpg")
        if target.is_file():
            return False
        if not await catalogue_cover(kind, item_id, target):
            return False

        def sizes():
            public = public_image(store, target)
            if public:
                for width in (256, 512):
                    variant(public, width)

        async with image_slots:
            await asyncio.to_thread(sizes)
        return True

    warmer.cover = warm_cover

    @app.get("/api/v1/diagnostics", tags=["personal-library"])
    async def own_diagnostics(request: Request):
        """Your own recent requests with the server's timing, and when the
        server stalled: what the playback probe compares its waits with."""
        return request_diagnostics(request.state.user["id"])

    @app.get("/api/v1/admin/diagnostics")
    async def admin_diagnostics():
        """Every recent request, each route's timing, stalls with stacks."""
        return request_diagnostics()

    @app.get("/api/v1/admin/warmer")
    def warmer_status():
        return warmer.status()

    async def preload_library():
        if os.environ.get("PIG_MUSIC_PRELOAD", "true").lower() == "false":
            return
        # One new/changed local item per pass. No periodic discography walk.
        seen = set()
        while not preload_stopping.is_set():
            for kind in ("albums", "artists"):
                for item in list(store.library[kind]):
                    signature = (
                        kind,
                        item["id"],
                        item.get("cover_url"),
                        json.dumps(public_settings(store), sort_keys=True),
                    )
                    if signature in seen:
                        continue
                    if not (item.get("cover_url") or "").startswith(
                        "/api/v1/library-artwork/"
                    ):
                        seen.add(signature)
                        continue
                    if enrichment.submit(
                        library_image_key(kind, item),
                        lambda kind=kind, item=item: enrich_library(kind, item),
                        library=True,
                    ):
                        seen.add(signature)
                    break
            live_ids = {
                i["id"] for kind in ("albums", "artists") for i in store.library[kind]
            }
            seen = {key for key in seen if key[1] in live_ids}
            await asyncio.sleep(15)

    from .api_models import PlaybackURL, PlaybackURLs, PlaybackURLsRequest

    @app.post("/api/v1/playback/{track_id}/url", response_model=PlaybackURL)
    def playback_url(track_id: str, request: Request):
        mark("handler")
        playing = track_id
        try:
            if track_id.startswith("mix:"):
                try:
                    mixes.playback(request.state.user["id"], track_id)
                except (FileNotFoundError, LookupError):
                    with phase("stand-in"):
                        playing = mixes.stand_in(request.state.user["id"], track_id)
                    if not playing:
                        raise LookupError(track_id)
            else:
                # The snapshot's path, not the NAS (NAS1): opening checks it.
                with phase("file"):
                    playable(store, track_id)
        except (FileNotFoundError, LookupError):
            raise HTTPException(404, "File unavailable")
        return {
            "url": security.sign_url(
                "/api/v1/playback/files/" + playing,
                request.state.user,
                request.state.session,
            ),
            "expires_in": 6 * 3600,
        }

    @app.post(
        "/api/v1/playback-urls", response_model=PlaybackURLs, tags=["personal-library"]
    )
    def playback_urls(body: PlaybackURLsRequest, request: Request):
        """Signed addresses for the songs a page shows, fetched ahead so a tap
        goes straight to the file. Household files stream for every member;
        mix songs keep their own per-play address."""
        wanted = [
            i
            for i in dict.fromkeys(body.track_ids)
            if len(i) <= 64 and not i.startswith("mix:")
        ]
        known = saved_track_ids(store, wanted)
        urls = {
            track_id: security.sign_url(
                "/api/v1/playback/files/" + track_id,
                request.state.user,
                request.state.session,
            )
            for track_id in wanted
            if track_id in known
        }
        return {"urls": urls, "expires_in": 6 * 3600}

    @app.get("/api/v1/tracks/{track_id}/file", tags=["personal-library"])
    @app.head("/api/v1/tracks/{track_id}/file", tags=["personal-library"])
    @app.get("/api/v1/playback/files/{track_id}")
    @app.head("/api/v1/playback/files/{track_id}")
    def playback_file(track_id: str, request: Request):
        mark("handler")
        opened = None
        try:
            if track_id.startswith("mix:"):
                target = mixes.playback(request.state.user["id"], track_id)
            else:
                # One NAS open per request, served from that descriptor (NAS1).
                with phase("file"):
                    opened = open_playback(store, track_id)
        except (FileNotFoundError, LookupError):
            raise HTTPException(404, "This saved file is unavailable on the server.")
        types = {
            ".mp3": "audio/mpeg",
            ".flac": "audio/flac",
            ".m4a": "audio/mp4",
            ".aac": "audio/aac",
            ".ogg": "audio/ogg",
            ".opus": "audio/ogg",
            ".wav": "audio/wav",
        }
        headers = {"Cache-Control": "private, no-cache"}
        if opened:
            fd, info, suffix = opened
            return DescriptorFileResponse(
                fd, info, media_type=types[suffix], headers=headers
            )
        return FileResponse(
            target, media_type=types[target.suffix.lower()], headers=headers
        )

    class PlaybackRequest(BaseModel):
        release_id: str
        track_id: str | None = Field(None, max_length=64)
        # Popularity and Home feeds know the recording, not the release track.
        recording_id: str | None = Field(None, max_length=64)

    def playback_saved(track_id: str):
        """A catalogue song you already have plays from its saved file."""
        try:
            track_file(store, track_id)
        except FileNotFoundError:
            raise HTTPException(404, "This saved file is unavailable on the server.")
        track, _ = saved_track(store, track_id)
        return {
            **track,
            "url": "/api/v1/playback/files/" + track_id,
            "source": "Saved library",
        }

    @app.post("/api/v1/remote-playback/catalogue", tags=["personal-library"])
    async def playback_catalogue(body: PlaybackRequest, request: Request):
        if not body.track_id and not body.recording_id:
            raise HTTPException(422, "Choose a song to play.")
        tracks = music_catalogue.presence(
            release_tracks(await release_data(body.release_id))
        )
        track = next(
            (
                t
                for t in tracks
                if (body.track_id and t["id"] == body.track_id)
                or (
                    not body.track_id
                    and t.get("musicbrainz_recording_id") == body.recording_id
                )
            ),
            None,
        )
        if track is None:
            raise HTTPException(404, "Song not found in this release.")
        if track.get("saved_id"):
            # SQLite plus a NAS resolve and stat: off the loop (LOOP1).
            return await asyncio.to_thread(playback_saved, track["saved_id"])
        return await remote_playback(track, request)

    from .routes.personal import TrackInput

    @app.post("/api/v1/remote-playback", tags=["personal-library"])
    async def playback_remote(body: TrackInput, request: Request):
        return await remote_playback(body.model_dump(exclude_none=True), request)

    class RemotePreparation(BaseModel):
        tracks: list[TrackInput] = Field(max_length=3)

    @app.post("/api/v1/remote-playback/prepare", tags=["personal-library"])
    async def prepare_remote(body: RemotePreparation, request: Request):
        """Players name the next songs in their queue so slow sources are
        fetched before they are reached."""
        tracks = [t.model_dump(exclude_none=True) for t in body.tracks]
        queued = []
        others = []
        for track in tracks:
            identity = await asyncio.to_thread(
                mixes.queue_saved, request.state.user["id"], track
            )
            (queued if identity else others).append(track)
        if not public_settings(store)["remote_playback"]:
            return {"preparing": len(queued)}
        return {"preparing": len(queued) + remote_audio.prepare(app.state.http, others)}

    @app.get("/api/v1/remote-playback/streams/{token}", tags=["personal-library"])
    @app.head("/api/v1/remote-playback/streams/{token}", tags=["personal-library"])
    def playback_relayed(token: str, request: Request):
        found = remote_audio.relay(token, request.state.user["id"])
        if not found:
            raise HTTPException(404, "This stream has expired. Play the song again.")
        return FileResponse(
            found[0],
            media_type=found[1],
            headers={"Cache-Control": "private, no-store"},
        )

    async def remote_playback(track, request):
        user = getattr(request.state, "user", None)
        cached_id = (
            await asyncio.to_thread(mixes.reference, user["id"], track)
            if user
            else None
        )
        if user:
            try:
                if not cached_id:
                    cached_id = await mixes.restore(user["id"], track)
                else:
                    try:
                        await asyncio.to_thread(
                            mixes.entry, user["id"], cached_id, True
                        )
                        await asyncio.to_thread(mixes.cache.available, track)
                    except (FileNotFoundError, LookupError):
                        cached_id = await mixes.restore(user["id"], track)
            except ValueError as exc:
                raise HTTPException(503, str(exc))
        if cached_id:
            try:
                await asyncio.to_thread(
                    mixes.playback, request.state.user["id"], cached_id
                )
                return {
                    **track,
                    "id": cached_id,
                    "history_track_id": cached_id,
                    "url": security.sign_url(
                        "/api/v1/playback/files/" + cached_id,
                        request.state.user,
                        request.state.session,
                    ),
                    "source": "Prepared mix",
                }
            except (FileNotFoundError, LookupError):
                pass
        if not public_settings(store)["remote_playback"]:
            raise HTTPException(
                409, "Experimental remote playback is disabled in Settings."
            )
        try:
            found = await remote_audio.stream(
                app.state.http, track, request.state.user["id"]
            )
        except (httpx.HTTPError, TimeoutError):
            raise HTTPException(
                503,
                "Remote streaming is unavailable right now. Saved music and downloads still work.",
            )
        except (ValueError, KeyError, TypeError):
            raise HTTPException(
                404,
                "No reliable remote stream matched this release. You can still download it.",
            )
        url = found.get("url") or security.sign_url(
            "/api/v1/remote-playback/streams/" + found["relay"],
            request.state.user,
            request.state.session,
        )
        return {
            **track,
            "url": url,
            "source": "Remote · " + LABELS[found["source"]],
            "note": "Not saved. Provider quality and exact mastering are unverified.",
        }

    app.include_router(artwork_router(store, image_slots, enrichment, image_response))

    dist = client_dir or ROOT / "client" / "dist"
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
        if (dist / "brand").is_dir():
            app.mount("/brand", StaticFiles(directory=dist / "brand"), name="brand")
        if (dist / "manifest.webmanifest").is_file():

            @app.get("/manifest.webmanifest", include_in_schema=False)
            def manifest():
                return FileResponse(
                    dist / "manifest.webmanifest",
                    media_type="application/manifest+json",
                    headers={"Cache-Control": "no-cache"},
                )

        if (dist / "sw.js").is_file():

            @app.get("/sw.js", include_in_schema=False)
            def service_worker():
                # Always revalidated so a release replaces the cache rules.
                return FileResponse(
                    dist / "sw.js",
                    media_type="text/javascript",
                    headers={"Cache-Control": "no-cache"},
                )

        @app.get("/theme.js", include_in_schema=False)
        def theme():
            return FileResponse(
                dist / "theme.js",
                media_type="text/javascript",
                headers={"Cache-Control": "no-cache"},
            )

        @app.get("/")
        def index():
            return FileResponse(
                dist / "index.html", headers={"Cache-Control": "no-cache"}
            )

    audit_routes(app, guard)
    return app


app = create_app()
