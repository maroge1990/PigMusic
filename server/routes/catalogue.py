"""Artist artwork aliases, owner acquisition, quality and media settings routes."""

import asyncio
import time
from pathlib import Path
import httpx
from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from ..artist_identity import Ambiguous, resolve_artist
from ..releases import release_query
from ..catalogue import RELEASE_INC, mbid
from ..artwork import quoted
from ..media import public_settings, save_settings
from ..portraits import portrait_target
from ..artist_backgrounds import background_target
from ..artist_logos import logo_target


def catalogue_router(
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
    http_client,
    logos=None,
):
    router = APIRouter()

    async def metadata(path, **params):
        try:
            return await music_catalogue.get(http_client(), path, **params)
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise HTTPException(
                502, "The catalogue is unavailable. Try again shortly."
            ) from exc

    def valid_id(value):
        try:
            return mbid(value)
        except ValueError as exc:
            raise HTTPException(400, "Invalid catalogue identifier.") from exc

    async def library_artist_catalogue(artist_id: str):
        artist = store.artists_by_id.get(artist_id)
        if artist is None:
            raise HTTPException(404, "Artist not found.")
        albums = [
            store.albums_by_id[i]
            for i in artist.get("album_ids", [])
            if i in store.albums_by_id
        ]
        try:
            identity = await resolve_artist(
                store, music_catalogue, http_client(), artist, albums
            )
        except Ambiguous as exc:
            raise HTTPException(
                409, "Choose this artist in Search to distinguish matching names."
            ) from exc
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise HTTPException(
                502, "The catalogue is unavailable. Try again shortly."
            ) from exc
        # Search supplies the identity portrait requests read. SQLite can wait
        # on another writer, so the cache is checked and filled off the loop.
        await asyncio.to_thread(remember_identity, identity)
        return identity

    def remember_identity(identity):
        key = "catalogue:artist/" + identity["id"] + "[]"
        if not store.read_document(key):
            store.write_document(key, {"time": time.time(), "data": identity})

    async def library_identity_document(artist_id):
        # SQLite can wait on another writer, so the identity is read off the loop.
        return await asyncio.to_thread(
            store.read_document, "library-artist:" + artist_id
        )

    async def cached_artist_image(cache_key):
        # SQLite can wait on another writer, so the cache is read off the loop.
        cached = await asyncio.to_thread(store.read_document, cache_key)
        return cached.get("file", "")

    class ArtistRequest(BaseModel):
        kinds: list[str] = Field(default=["album"], max_length=3)
        monitor: bool = False
        request: bool = False

    @router.get("/api/v1/admin/catalogue/artists/{artist_id}/acquisition")
    def artist_acquisition(artist_id: str):
        return artist_jobs.state(valid_id(artist_id))

    @router.put("/api/v1/admin/catalogue/artists/{artist_id}/acquisition")
    def artist_acquisition_update(artist_id: str, body: ArtistRequest):
        try:
            return artist_jobs.configure(
                valid_id(artist_id), body.kinds, body.monitor, body.request
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get(
        "/api/v1/catalogue/artists/{artist_id}/background", tags=["personal-library"]
    )
    async def catalogue_artist_background(
        artist_id: str, w: int = 0, soft: bool = False
    ):
        artist_id = valid_id(artist_id)
        config = public_settings(store)
        if not config["portraits"] or not config["fanart_configured"]:
            raise HTTPException(404, "Artwork unavailable.")
        target = background_target(store, artist_id)
        if not target.is_file():
            enrichment.submit(
                "background:" + target.stem,
                lambda: backgrounds.get(http_client(), artist_id),
            )
        return await image_response(target, w, soft)

    async def enrich_background(artist_id):
        identity = await library_artist_catalogue(artist_id)
        return await backgrounds.get(http_client(), identity["id"])

    @router.get("/api/v1/artists/{artist_id}/background")
    async def artist_background(artist_id: str, w: int = 0, soft: bool = False):
        if artist_id not in store.artists_by_id:
            raise HTTPException(404, "Artist not found.")
        config = public_settings(store)
        if not config["fanart_configured"] or not config["portraits"]:
            raise HTTPException(404, "Artwork unavailable.")
        identity = await library_identity_document(artist_id)
        target = background_target(store, identity["id"]) if identity else None
        if not target or not target.is_file():
            enrichment.submit(
                "library-background:" + artist_id, lambda: enrich_background(artist_id)
            )
        return await image_response(target, w, soft)

    def logo_response(target):
        if not target or not target.is_file():
            return Response(status_code=404, headers={"Cache-Control": "no-store"})
        # The file name changes with the Fanart key, so a day's reuse is safe.
        return FileResponse(
            target,
            media_type="image/png",
            headers={"Cache-Control": "private, max-age=86400"},
        )

    def logos_enabled():
        config = public_settings(store)
        return logos is not None and config["portraits"] and config["fanart_configured"]

    @router.get("/api/v1/catalogue/artists/{artist_id}/logo", tags=["personal-library"])
    async def catalogue_artist_logo(artist_id: str):
        artist_id = valid_id(artist_id)
        if not logos_enabled():
            raise HTTPException(404, "Artwork unavailable.")
        target = logo_target(store, artist_id)
        if not target.is_file():
            enrichment.submit(
                "logo:" + target.stem, lambda: logos.get(http_client(), artist_id)
            )
        return logo_response(target)

    async def enrich_logo(artist_id):
        identity = await library_artist_catalogue(artist_id)
        return await logos.get(http_client(), identity["id"])

    @router.get("/api/v1/artists/{artist_id}/logo")
    async def artist_logo(artist_id: str):
        if artist_id not in store.artists_by_id:
            raise HTTPException(404, "Artist not found.")
        if not logos_enabled():
            raise HTTPException(404, "Artwork unavailable.")
        identity = await library_identity_document(artist_id)
        target = logo_target(store, identity["id"]) if identity else None
        if not target or not target.is_file():
            enrichment.submit(
                "library-logo:" + artist_id, lambda: enrich_logo(artist_id)
            )
        return logo_response(target)

    async def enrich_catalogue_artist(artist_id, cache_key):
        if public_settings(store)["portraits"]:
            identity = await metadata("artist/" + artist_id)
            target = await portraits.get(
                http_client(), identity.get("name", ""), artist_id
            )
            if target:
                await asyncio.to_thread(
                    store.write_document, cache_key, {"file": target.name}
                )
                return target
        data = await metadata(
            "release", artist=artist_id, inc="release-groups", limit=50
        )
        releases = [
            r
            for r in data.get("releases", [])
            if r.get("cover-art-archive", {}).get("front")
        ]
        releases.sort(
            key=lambda r: (
                r.get("release-group", {}).get("primary-type") != "Album",
                bool(r.get("release-group", {}).get("secondary-types")),
            )
        )
        if releases:
            target = await artwork.catalogue_cover(
                http_client(), "release", valid_id(releases[0]["id"])
            )
            if target:
                await asyncio.to_thread(
                    store.write_document, cache_key, {"file": target.name}
                )
                return target
        return None

    @router.get(
        "/api/v1/catalogue/artists/{artist_id}/artwork", tags=["personal-library"]
    )
    async def artist_artwork(artist_id: str, w: int = 0):
        artist_id = valid_id(artist_id)
        cache_key = "catalogue-artist-image:" + artist_id + ":" + image_config()
        cached = await cached_artist_image(cache_key)
        target = (
            store.cover_dir / cached if cached and Path(cached).name == cached else None
        )
        if not target or not target.is_file():
            enrichment.submit(
                cache_key, lambda: enrich_catalogue_artist(artist_id, cache_key)
            )
        return await image_response(target, w)

    async def release_data(release_id):
        return await metadata("release/" + valid_id(release_id), inc=RELEASE_INC)

    class CatalogueRequest(BaseModel):
        track_ids: list[str] | None = Field(default=None, max_length=500)
        quality_profile: str | None = None

    class AlbumQuality(BaseModel):
        profile: str | None = None

    @router.post("/api/v1/admin/albums/{album_id}/quality")
    def album_quality(album_id: str, body: AlbumQuality):
        try:
            return store.quality_override("album", album_id, body.profile)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get("/api/v1/admin/albums/{album_id}/quality")
    def album_quality_status(album_id: str):
        try:
            return store.quality_status("album", album_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.get("/api/v1/admin/artists/{artist_id}/quality")
    def artist_quality_status(artist_id: str):
        try:
            return store.quality_status("artist", artist_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.post("/api/v1/admin/artists/{artist_id}/quality")
    def artist_quality(artist_id: str, body: AlbumQuality):
        try:
            return store.quality_override("artist", artist_id, body.profile)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    class MediaSettings(BaseModel):
        portraits: bool
        remote_playback: bool
        fanart_key: str | None = Field(default=None, max_length=128)
        listenbrainz_token: str | None = Field(default=None, max_length=128)
        chart_region: str | None = Field(default=None, max_length=2)

    @router.get("/api/v1/admin/settings/media")
    def media_settings():
        return public_settings(store)

    @router.put("/api/v1/admin/settings/media")
    def update_media_settings(body: MediaSettings):
        try:
            result = save_settings(store, **body.model_dump())
            store.refresh_library()
            return result
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    async def warm_artist(artist_id, full=False):
        """Prepare what an artist page loads, in the same caches the page reads.

        Returns covers worth preparing: (kind, catalogue id) pairs.
        """
        artist_id = mbid(artist_id)
        client = http_client()
        covers = []
        try:
            popular = await popularity.get(client, artist_id, include_albums=False)
            covers += [
                ("release", s["release_id"]) for s in popular.get("songs", [])[:5]
            ]
        except Exception:
            pass
        if public_settings(store)["portraits"]:
            cache_key = "catalogue-artist-image:" + artist_id + ":" + image_config()
            cached = await cached_artist_image(cache_key)
            if not cached or not (store.cover_dir / Path(cached).name).is_file():
                try:
                    await enrich_catalogue_artist(artist_id, cache_key)
                except Exception:
                    pass
            if public_settings(store)["fanart_configured"]:
                await backgrounds.get(client, artist_id)
                if logos is not None:
                    await logos.get(client, artist_id)
        if full:
            try:
                data = await music_catalogue.get(
                    client,
                    "release-group/",
                    query=release_query(artist_id, "album"),
                    limit=100,
                    offset=0,
                )
                covers += [
                    ("release-group", r["id"])
                    for r in data.get("release-groups", [])[:24]
                ]
            except (httpx.HTTPError, ValueError, KeyError):
                pass
        return covers

    async def library_identity(artist_id):
        """Catalogue identity for a library artist, or None when ambiguous."""
        try:
            return (await library_artist_catalogue(artist_id))["id"]
        except (HTTPException, KeyError):
            return None

    router.warm_artist = warm_artist
    router.library_identity = library_identity
    return router, library_artist_catalogue, valid_id, release_data
