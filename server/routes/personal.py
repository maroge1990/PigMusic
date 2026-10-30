"""Versioned personal-library API, with no household/private path disclosure."""

import asyncio
import os
from typing import Annotated, Literal

from fastapi import APIRouter, Header, HTTPException, Request, Response, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..library import identifier, normalise
from ..api_models import (
    AlbumDetail,
    ArtistDetail,
    PlaylistDTO,
    PlaylistList,
    SearchResults,
    RequestList,
    RequestGroupList,
    FavouriteResultDTO,
    MembershipResultDTO,
    AcquisitionResultDTO,
    ClearedRequestsDTO,
    DeletedPlaylistDTO,
    ImportResultDTO,
    ImportPreviewDTO,
    QualityDTO,
    QualitySavedDTO,
    RemovalPreviewDTO,
    RemovalResultDTO,
    RestoredDTO,
    RecordedDTO,
    ArtistAcquisitionDTO,
    RetriedDTO,
    CancelledRequestDTO,
    LookupResultDTO,
    ServerInfoDTO,
)
from ..conditional import CONDITIONAL_RESPONSES, conditional_json
from ..playlists import Playlists, PlaylistConflict, PlaylistFull

# Apps compare these to explain which features need a server update.
SERVER_API_LEVEL = 2
SERVER_CAPABILITIES = [
    "household_playlist_tracks",
    "queue_events",
    "playlist_limit",
    "server_info",
]
from ..user_library import track_dto, SearchChanged
from ..personal_removal import PersonalRemoval, RemovalChanged
from ..household_policy import HouseholdPolicy


RecordingKind = Annotated[
    str,
    Field(
        pattern="^(live|remix|demo|unplugged|acoustic|karaoke|session|instrumental)$"
    ),
]


class TrackInput(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    artist: str = Field(min_length=1, max_length=500)
    album: str = Field(default="", max_length=500)
    album_artist: str = Field(default="", max_length=500)
    year: str = Field(default="", max_length=10)
    disc_number: int = Field(default=1, ge=1, le=999)
    track_number: int = Field(default=1, ge=0, le=9999)
    duration: float = Field(default=0, ge=0, le=86400)
    musicbrainz_release_id: str | None = Field(default=None, max_length=36)
    musicbrainz_recording_id: str | None = Field(default=None, max_length=36)
    isrcs: list[str] = Field(default_factory=list, max_length=50)
    # Chosen from "Other versions": only this edition will do.
    explicit_edition: bool = False
    # From the release type: a live/remix/demo recording, not the studio one.
    recording_kind: list[RecordingKind] = Field(default_factory=list, max_length=8)


class Tracks(BaseModel):
    tracks: list[TrackInput] = Field(min_length=1, max_length=500)


class LookupSong(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    artist: str = Field(min_length=1, max_length=500)
    musicbrainz_recording_id: str | None = Field(default=None, max_length=36)
    duration: float = Field(default=0, ge=0, le=86400)
    recording_kind: list[RecordingKind] = Field(default_factory=list, max_length=8)


class Lookup(BaseModel):
    songs: list[LookupSong] = Field(min_length=1, max_length=200)


class Membership(BaseModel):
    track_ids: list[str] = Field(min_length=1, max_length=500)


class Removal(Membership):
    confirmation: str = Field(min_length=64, max_length=64)


class QualityPreference(BaseModel):
    scope: Literal["system", "artist", "album"]
    item_id: str = Field(default="*", max_length=128)
    preferred: (
        Literal["mp3_128", "mp3_320", "aac_256", "lossless", "native_standard"] | None
    ) = None


class PlaylistName(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class PlaylistEdit(BaseModel):
    revision: int = Field(ge=0)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    order: list[int] | None = Field(default=None, max_length=10000)


class Favourite(BaseModel):
    track: TrackInput
    favourite: bool | None = None


class PlayEvent(BaseModel):
    event_id: str = Field(min_length=1, max_length=64)
    track_id: str = Field(min_length=1, max_length=128)
    played_at: int
    seconds: int = Field(ge=0, le=86400)


class PlayEvents(BaseModel):
    events: list[PlayEvent] = Field(min_length=1, max_length=200)


class Preferences(BaseModel):
    appearance: Literal["system", "light", "dark"] = "system"
    default_tab: Literal["artists", "albums", "playlists", "favourites"] = "artists"


def playlist_dto(value):
    return {
        **{k: v for k, v in value.items() if k not in {"tracks", "entries"}},
        "entries": [
            {
                **track_dto(t),
                "server_state": (
                    "in_library"
                    if t.get("saved_track")
                    else (
                        "household_available"
                        if t.get("household_available")
                        else "not_in_library"
                    )
                ),
                **{
                    k: t.get(k)
                    for k in (
                        "entry_id",
                        "position",
                        "status",
                        "match_kind",
                        "saved_id",
                        "household_available",
                    )
                },
                "saved_track": (
                    track_dto(t["saved_track"]) if t.get("saved_track") else None
                ),
                "household_track": (
                    {
                        **track_dto(t["household_track"]),
                        "server_state": "household_available",
                    }
                    if t.get("household_track")
                    else None
                ),
            }
            for t in value["entries"]
        ],
    }


class ArtistRequest(BaseModel):
    kinds: list[Literal["album", "single", "ep"]] = Field(
        default=["album"], min_length=1, max_length=3
    )
    monitor: bool = False
    request: bool = False


class NativeMutationInput(BaseModel):
    operation_id: str = Field(min_length=36, max_length=36)
    action: Literal[
        "create",
        "append",
        "rename",
        "reorder",
        "remove",
        "delete",
        "favourite",
        "preferences",
    ]
    playlist_id: str | None = Field(default=None, max_length=128)
    name: str | None = Field(default=None, max_length=100)
    revision: int | None = Field(default=None, ge=0)
    order: list[int] | None = Field(default=None, max_length=10000)
    position: int | None = Field(default=None, ge=0)
    track: TrackInput | None = None
    favourite: bool | None = None
    preferences: Preferences | None = None


class NativeMutationReceipt(BaseModel):
    operation_id: str
    applied: bool
    playlist_id: str | None = None
    revision: int | None = None
    favourited: bool | None = None


def personal_router(store, artist_jobs, recovery):
    router = APIRouter(prefix="/api/v1", tags=["personal-library"])
    users = store.user_libraries
    removal = PersonalRemoval(store, recovery)

    def identity(request):
        return request.state.user["id"]

    def projection(request):
        return users.projection(identity(request))

    async def run(fn, *args):
        try:
            return await asyncio.to_thread(fn, *args)
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @router.get("/library")
    async def library(request: Request):
        data = await run(users.summary, identity(request))
        etag = '"' + data["revision"] + '"'
        headers = {
            "ETag": etag,
            "Cache-Control": "private, no-cache",
            "Vary": "Accept-Encoding",
        }
        if any(
            value.strip().removeprefix("W/") in {etag, "*"}
            for value in request.headers.get("if-none-match", "").split(",")
        ):
            return Response(status_code=304, headers=headers)
        return JSONResponse(data, headers=headers)

    @router.get("/library-pages/{kind}")
    async def page(
        kind: Literal["artists", "albums", "tracks"],
        request: Request,
        offset: int = 0,
        limit: int = 100,
    ):
        if offset < 0 or not 1 <= limit <= 200:
            raise HTTPException(422, "Invalid page bounds")
        data = await run(users.projection, identity(request))
        selected = data[kind][offset : offset + limit]
        if kind == "tracks":
            selected = [track_dto(t) for t in selected]
        elif kind == "albums":
            selected = [
                {k: v for k, v in a.items() if k not in {"tracks", "cover_key"}}
                for a in selected
            ]
        return {
            "items": selected,
            "total": len(data[kind]),
            "offset": offset,
            "next_offset": offset + limit if offset + limit < len(data[kind]) else None,
            "revision": data["revision"],
        }

    @router.get("/albums/{album_id}", response_model=AlbumDetail)
    async def album(album_id: str, request: Request):
        data = await run(users.projection, identity(request))
        found = next((a for a in data["albums"] if a["id"] == album_id), None)
        if not found:
            raise HTTPException(404, "Album not found")
        return {
            **{k: v for k, v in found.items() if k not in {"tracks", "cover_key"}},
            "tracks": [track_dto(t) for t in found["tracks"]],
        }

    @router.get("/artists/{artist_id}", response_model=ArtistDetail)
    async def artist(artist_id: str, request: Request):
        data = await run(users.projection, identity(request))
        found = next((a for a in data["artists"] if a["id"] == artist_id), None)
        if not found:
            raise HTTPException(404, "Artist not found")
        albums = [
            {k: v for k, v in a.items() if k not in {"tracks", "cover_key"}}
            for a in data["albums"]
            if a["id"] in found["album_ids"]
        ]
        owned_track_ids = {
            t["id"]
            for a in data["albums"]
            if a["id"] in found["album_ids"]
            for t in a["tracks"]
        }
        return {
            **found,
            "albums": albums,
            "tracks": [
                track_dto(t)
                for a in data["albums"]
                if a["id"] in found["album_ids"]
                and a.get("server_state", "in_library") == "in_library"
                for t in a["tracks"]
            ],
            "compilation_tracks": [
                track_dto(t)
                for t in data["tracks"]
                if identifier("artist", normalise(t.get("artist", ""))) == artist_id
                and t["id"] not in owned_track_ids
            ],
        }

    @router.get("/artists/{artist_id}/acquisition", response_model=ArtistAcquisitionDTO)
    async def artist_acquisition(artist_id: str, request: Request):
        return await run(artist_jobs.scoped(identity(request)).state, artist_id)

    @router.put("/artists/{artist_id}/acquisition", response_model=ArtistAcquisitionDTO)
    async def configure_artist(artist_id: str, body: ArtistRequest, request: Request):
        return await run(
            artist_jobs.scoped(identity(request)).configure,
            artist_id,
            body.kinds,
            body.monitor,
            body.request,
        )

    @router.delete(
        "/artists/{artist_id}/acquisition", response_model=ArtistAcquisitionDTO
    )
    async def cancel_artist(artist_id: str, request: Request):
        return await run(artist_jobs.scoped(identity(request)).cancel, artist_id)

    @router.get("/search", response_model=SearchResults)
    async def search(
        request: Request,
        q: str = "",
        offset: int = Query(default=0, ge=0, le=1000000),
        limit: int = Query(default=100, ge=1, le=200),
        revision: str | None = None,
    ):
        try:
            return await asyncio.to_thread(
                users.search, identity(request), q, offset, limit, revision
            )
        except SearchChanged as exc:
            raise HTTPException(409, str(exc))

    @router.post("/library/lookup")
    async def lookup(body: Lookup, request: Request):
        songs = [s.model_dump() for s in body.songs]
        return {"tracks": await run(users.lookup, identity(request), songs)}

    @router.post("/library/matches", response_model=LookupResultDTO)
    async def matches(body: Lookup, request: Request):
        songs = [s.model_dump() for s in body.songs]
        return {
            "matches": [
                {"track": track}
                for track in await run(users.lookup, identity(request), songs)
            ]
        }

    @router.post("/library/membership", response_model=MembershipResultDTO)
    async def add(body: Membership, request: Request):
        return await run(users.add, identity(request), body.track_ids)

    async def remove_call(fn, *args):
        try:
            return await asyncio.to_thread(fn, *args)
        except LookupError as exc:
            raise HTTPException(404, str(exc))
        except RemovalChanged as exc:
            raise HTTPException(409, str(exc))
        except (ValueError, OSError):
            raise HTTPException(
                400,
                "Removal or restore unavailable. Refresh or ask the administrator to check Recovery.",
            )

    @router.post("/library/removal-preview", response_model=RemovalPreviewDTO)
    async def preview_removal(body: Membership, request: Request):
        return await remove_call(removal.preview, identity(request), body.track_ids)

    @router.post("/library/remove", response_model=RemovalResultDTO)
    async def remove_music(body: Removal, request: Request):
        return await remove_call(
            removal.remove, identity(request), body.track_ids, body.confirmation
        )

    @router.post("/library/removals/{operation_id}/undo", response_model=RestoredDTO)
    async def undo_removal(operation_id: str, request: Request):
        return await remove_call(removal.undo, identity(request), operation_id)

    @router.get("/playlists", response_model=PlaylistList)
    async def playlists(request: Request):
        return {"playlists": await run(Playlists(store, identity(request)).list)}

    @router.post("/playlists", response_model=PlaylistDTO)
    async def create(body: PlaylistName, request: Request):
        return playlist_dto(
            await run(Playlists(store, identity(request)).create, body.name)
        )

    @router.get(
        "/playlists/{playlist_id}",
        response_model=PlaylistDTO,
        responses=CONDITIONAL_RESPONSES,
    )
    async def playlist(
        playlist_id: str,
        request: Request,
        if_none_match: str | None = Header(default=None),
    ):
        try:
            value = await asyncio.to_thread(
                Playlists(store, identity(request)).detail, playlist_id
            )
        except ValueError:
            raise HTTPException(404, "Playlist not found")
        return conditional_json(request, PlaylistDTO, playlist_dto(value))

    @router.post("/playlists/{playlist_id}/songs", response_model=PlaylistDTO)
    async def add_song(
        playlist_id: str, body: TrackInput, request: Request, allow_repeat: bool = False
    ):
        try:
            return playlist_dto(
                await asyncio.to_thread(
                    Playlists(store, identity(request)).add,
                    playlist_id,
                    body.model_dump(exclude_none=True),
                    allow_repeat=allow_repeat,
                )
            )
        except PlaylistFull as exc:
            raise HTTPException(409, str(exc))
        except ValueError:
            raise HTTPException(404, "Playlist not found")

    async def edit_playlist(request, playlist_id, revision, **changes):
        try:
            value = await asyncio.to_thread(
                Playlists(store, identity(request)).edit,
                playlist_id,
                revision,
                **changes,
            )
            return value if changes.get("delete") else playlist_dto(value)
        except LookupError:
            raise HTTPException(404, "Playlist not found")
        except PlaylistConflict as exc:
            raise HTTPException(409, str(exc))
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @router.patch("/playlists/{playlist_id}", response_model=PlaylistDTO)
    async def edit(playlist_id: str, body: PlaylistEdit, request: Request):
        return await edit_playlist(
            request, playlist_id, body.revision, name=body.name, order=body.order
        )

    @router.delete(
        "/playlists/{playlist_id}/songs/{position}", response_model=PlaylistDTO
    )
    async def remove_song(
        playlist_id: str, position: int, revision: int, request: Request
    ):
        return await edit_playlist(request, playlist_id, revision, remove=position)

    @router.delete("/playlists/{playlist_id}", response_model=DeletedPlaylistDTO)
    async def delete(playlist_id: str, revision: int, request: Request):
        return await edit_playlist(request, playlist_id, revision, delete=True)

    @router.post("/favourites", response_model=FavouriteResultDTO)
    async def favourite(body: Favourite, request: Request):
        return await run(
            Playlists(store, identity(request)).favourite,
            body.track.model_dump(exclude_none=True),
            body.favourite,
        )

    @router.post(
        "/imports/apple/preview",
        response_model=ImportPreviewDTO,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/xml": {
                        "schema": {"type": "string", "format": "binary"}
                    }
                },
            }
        },
    )
    async def preview_import(request: Request):
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 20 * 1024 * 1024:
                raise HTTPException(413, "Import is larger than 20MB")

        def prepare():
            from ..library import parse_apple

            source = parse_apple(bytes(body))
            personal = {t["id"] for t in users.projection(identity(request))["tracks"]}
            counts = {"saved": 0, "household": 0, "missing": 0}
            for track in source["tracks"]:
                matched = store.index.match(track)
                state = (
                    "saved"
                    if matched.track and matched.track["id"] in personal
                    else "household" if matched.track else "missing"
                )
                counts[state] += 1
            return {
                "playlists": len(source.get("playlists", [])),
                "tracks": len(source["tracks"]),
                **counts,
            }

        return await run(prepare)

    @router.post(
        "/imports/apple",
        response_model=ImportResultDTO,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/xml": {
                        "schema": {"type": "string", "format": "binary"}
                    }
                },
            }
        },
    )
    async def import_apple(request: Request):
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 20 * 1024 * 1024:
                raise HTTPException(413, "Import is larger than 20MB")
        return await run(users.import_playlists, identity(request), bytes(body))

    @router.post("/requests", response_model=AcquisitionResultDTO)
    async def request_tracks(body: Tracks, request: Request):
        return await run(
            users.request,
            identity(request),
            [t.model_dump(exclude_none=True) for t in body.tracks],
        )

    @router.delete("/requests", response_model=ClearedRequestsDTO)
    async def clear_requests(request: Request):
        return await run(users.clear_requests, identity(request))

    @router.post("/requests/{request_id}/cancel", response_model=CancelledRequestDTO)
    async def cancel_request(request_id: str, request: Request):
        try:
            return await run(users.cancel_request, identity(request), request_id)
        except LookupError:
            raise HTTPException(404, "Request not found")

    @router.post("/requests/{request_id}/retry", response_model=RetriedDTO)
    async def retry_request(request_id: str, request: Request):
        try:
            return await run(users.retry_request, identity(request), request_id)
        except LookupError:
            raise HTTPException(404, "Request not found")

    @router.get("/requests/groups", response_model=RequestGroupList)
    async def request_groups(
        request: Request,
        offset: int = Query(default=0, ge=0, le=100000),
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return await run(users.request_groups, identity(request), offset, limit)

    @router.get("/requests", response_model=RequestList)
    async def requests(request: Request, offset: int = 0, limit: int = 200):
        return await run(users.queue, identity(request), offset, limit)

    @router.post("/play-events", response_model=RecordedDTO)
    async def play_events(body: PlayEvents, request: Request):
        return await run(
            users.history,
            identity(request),
            [t.model_dump() for t in body.events],
            request.state.session["public_id"],
        )

    def recent_plays(user_id):
        from ..auth import records

        with store.connect() as conn:
            return {
                "events": records(
                    conn.execute(
                        "SELECT event_id,track_id,played_at,seconds FROM play_events WHERE user_id=? ORDER BY played_at DESC LIMIT 200",
                        (user_id,),
                    )
                )
            }

    @router.get("/play-events")
    async def history(request: Request):
        # SQLite can wait on another writer, so the query runs off the loop.
        return await asyncio.to_thread(recent_plays, identity(request))

    @router.post("/mutations", response_model=NativeMutationReceipt)
    async def native_mutation(body: NativeMutationInput, request: Request):
        from ..native_mutations import NativeMutations

        try:
            return await run(
                NativeMutations(store).apply,
                identity(request),
                body.model_dump(exclude_none=True),
            )
        except LookupError:
            raise HTTPException(404, "Playlist not found")
        except PlaylistConflict as exc:
            raise HTTPException(409, str(exc))

    @router.get("/quality", response_model=QualityDTO)
    async def quality(request: Request):
        return await run(HouseholdPolicy(store).quality, identity(request))

    @router.put("/quality", response_model=QualitySavedDTO)
    async def save_quality(body: QualityPreference, request: Request):
        try:
            return await run(
                HouseholdPolicy(store).set_quality,
                identity(request),
                body.scope,
                body.item_id,
                body.preferred,
            )
        except LookupError:
            raise HTTPException(404, "Library item not found")

    @router.get("/server", response_model=ServerInfoDTO)
    async def server_info():
        """Which server build this is, so apps can explain a needed update."""
        return {
            "version": os.environ.get("PIG_MUSIC_VERSION", "dev"),
            "revision": os.environ.get("PIG_MUSIC_REVISION", "unknown")[:12],
            "api_level": SERVER_API_LEVEL,
            "capabilities": SERVER_CAPABILITIES,
        }

    def read_preferences(user_id):
        import json

        with store.connect() as conn:
            row = conn.execute(
                "SELECT payload FROM user_preferences WHERE user_id=?", (user_id,)
            ).fetchone()
        return json.loads(row[0]) if row else Preferences().model_dump()

    def write_preferences(user_id, value):
        import json

        with store.connect() as conn:
            conn.execute(
                "INSERT INTO user_preferences VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET payload=excluded.payload",
                (user_id, json.dumps(value)),
            )

    # SQLite can wait on another writer, so preferences are used off the loop.
    @router.get("/preferences", response_model=Preferences)
    async def preferences(request: Request):
        return await asyncio.to_thread(read_preferences, identity(request))

    @router.put("/preferences", response_model=Preferences)
    async def save_preferences(body: Preferences, request: Request):
        await asyncio.to_thread(write_preferences, identity(request), body.model_dump())
        return body

    return router
