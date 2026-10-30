"""Shared provider metadata with personal availability and acquisition controls."""

import asyncio
import json
import re
from uuid import UUID
import httpx
from fastapi import APIRouter, Header, HTTPException, Request, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from ..artist_identity import Ambiguous, cached_identities, resolve_artist
from ..catalogue import RELEASE_INC, release_tracks, credit, edition_rank
from ..library import normalise
from ..releases import release_query
from ..api_models import (
    CatalogueGroupsDTO,
    CatalogueEditionsDTO,
    CatalogueReleaseDTO,
    CatalogueTrackDTO,
    CatalogueArtistsDTO,
    CataloguePopularityDTO,
    CatalogueProfileDTO,
    ArtistIdentityDTO,
)
from ..conditional import CONDITIONAL_RESPONSES, conditional_json
from ..user_library import track_dto


# A trailing qualifier that names an edition of the same album. Anything else
# in brackets ("Live", "Demos", "Remixes") names different recordings.
EDITION = re.compile(
    r"\b(deluxe|expanded|edition|remaster(ed)?|anniversary|bonus|special|"
    r"collector'?s|reissue|explicit|clean|mono|stereo|super)\b",
    re.I,
)
DISTINCT = re.compile(
    r"\b(live|acoustic|demos?|remix(es)?|instrumentals?|karaoke|sessions?|unplugged)\b",
    re.I,
)


def title_key(title):
    """Album titles compare without edition qualifiers: "Brat (Deluxe)" is
    "brat", but "Brat (Live)" stays a different album."""
    text = title or ""
    while found := re.search(r"\s*[\(\[]([^\)\]]*)[\)\]]\s*$", text):
        qualifier = found.group(1)
        if not EDITION.search(qualifier) or DISTINCT.search(qualifier):
            break
        text = text[: found.start()]
    return normalise(text) or normalise(title)


# Covers fetched as soon as an artist's discography is opened: a phone's
# first screen of release tiles.
ARTIST_PAGE_COVERS = 24
ACTIVE = {"waiting_for_sources", "searching", "downloading", "validating", "importing"}


def discover_router(store, catalogue, popularity=None, warmer=None, http_client=None):
    router = APIRouter(prefix="/api/v1/catalogue", tags=["personal-library"])

    def valid(value):
        try:
            return str(UUID(value))
        except ValueError:
            raise HTTPException(400, "invalid_catalogue_id")

    async def metadata(request, path, **parameters):
        try:
            return await catalogue.get(request.app.state.http, path, **parameters)
        except (httpx.HTTPError, ValueError, KeyError):
            raise HTTPException(502, "catalogue_unavailable")

    @router.get("/artists", response_model=CatalogueArtistsDTO)
    async def artists(
        request: Request,
        q: str = "",
        offset: int = Query(default=0, ge=0, le=10000),
        limit: int = Query(default=12, ge=1, le=100),
    ):
        if len(q.strip()) < 2:
            return {"artists": [], "offset": offset, "total": 0, "next_offset": None}
        result = await metadata(
            request, "artist/", query=q.strip()[:150], limit=limit, offset=offset
        )
        if warmer is not None:
            # Prepare the likely next tap on the server, for every device.
            warmer.search([a["id"] for a in result.get("artists", []) if "id" in a])
        found = result.get("artists", [])
        total = int(
            result.get("count") or result.get("artist-count") or (offset + len(found))
        )
        end = offset + len(found)
        return {
            "offset": offset,
            "total": total,
            "next_offset": end if found and end < min(total, 10000) else None,
            "artists": [
                {
                    "id": a["id"],
                    "name": a["name"],
                    "description": a.get("disambiguation") or "",
                }
                for a in result.get("artists", [])
            ],
        }

    def saved_albums(user_id, artist_id, name):
        """The person's library albums for a catalogue artist.

        Library artists linked to a catalogue identity match only that
        identity. The name the caller passes is a fallback for library
        artists with no known identity, and only when no library artist is
        linked to this one. No catalogue request is made.
        """
        projection = store.user_libraries.projection(user_id)
        identities = cached_identities(store, projection["artists"])
        artists = [
            a
            for a in projection["artists"]
            if (identities[a["id"]] or {}).get("id") == artist_id
        ]
        wanted = normalise(name)
        if not artists and wanted:
            artists = [
                a
                for a in projection["artists"]
                if identities[a["id"]] is None and normalise(a["name"]) == wanted
            ]
        album_ids = {i for a in artists for i in a.get("album_ids", [])}
        return [
            album
            for album in projection["albums"]
            if album["id"] in album_ids
            and album.get("server_state", "in_library") == "in_library"
        ]

    def owned_album(saved, group):
        """The saved album that is this release group, if any.

        A saved album's known release must be one of the group's releases
        when the provider lists them; otherwise titles decide, ignoring only
        edition qualifiers.
        """
        listed = {r.get("id") for r in group.get("releases") or [] if r.get("id")}
        wanted = title_key(group.get("title"))
        for album in saved:
            releases = {
                t["musicbrainz_release_id"]
                for t in album.get("tracks", [])
                if t.get("musicbrainz_release_id")
            }
            if listed and releases:
                if releases & listed:
                    return album
                continue
            if title_key(album["title"]) == wanted:
                return album
        return None

    @router.get("/artists/{artist_id}/releases", response_model=CatalogueGroupsDTO)
    async def groups(
        artist_id: str,
        request: Request,
        offset: int = 0,
        kind: str | None = None,
        name: str = Query(default="", max_length=200),
    ):
        if not 0 <= offset <= 10000:
            raise HTTPException(422, "invalid_page")
        if kind:
            if kind not in {"album", "single", "ep", "other"}:
                raise HTTPException(422, "invalid_release_type")
            result = await metadata(
                request,
                "release-group/",
                query=release_query(valid(artist_id), kind),
                limit=100,
                offset=offset,
            )
        else:
            result = await metadata(
                request,
                "release-group",
                artist=valid(artist_id),
                limit=50,
                offset=offset,
            )
        saved = await asyncio.to_thread(
            saved_albums, request.state.user["id"], valid(artist_id), name
        )

        def owned(group):
            album = owned_album(saved, group)
            return {
                "saved_album_id": album["id"] if album else None,
                "saved_songs": album.get("track_count", 0) if album else 0,
            }

        groups = result.get("release-groups", [])
        if warmer is not None and groups:
            # The page shows these covers next: fetch them now, ahead of the
            # background pass (ART2: 50-56 of 72 at first ask).
            warmer.covers(
                [("release-group", r["id"]) for r in groups[:ARTIST_PAGE_COVERS]]
            )
        return {
            "total": result.get("count", result.get("release-group-count", 0)),
            "offset": offset,
            "releases": [
                {
                    "id": r["id"],
                    "title": r["title"],
                    "date": r.get("first-release-date") or "",
                    "type": r.get("primary-type") or "Release",
                    **owned(r),
                }
                for r in groups
            ],
        }

    @router.get(
        "/artists/{artist_id}/popularity", response_model=CataloguePopularityDTO
    )
    async def popular_artist(artist_id: str, request: Request):
        return await popularity.get(request.app.state.http, valid(artist_id))

    @router.get("/artists/{artist_id}/profile", response_model=CatalogueProfileDTO)
    async def artist_profile(artist_id: str, request: Request):
        found = await metadata(request, "artist/" + valid(artist_id))
        return {
            "id": valid(artist_id),
            "name": found.get("name") or "",
            "description": found.get("disambiguation") or "",
            "type": found.get("type") or "",
            "country": found.get("country") or "",
            "begin": (found.get("life-span") or {}).get("begin") or "",
            "end": (found.get("life-span") or {}).get("end") or "",
        }

    @router.get("/library-artists/{artist_id}", response_model=ArtistIdentityDTO)
    async def saved_identity(artist_id: str, request: Request):
        projection = await asyncio.to_thread(
            store.user_libraries.projection, request.state.user["id"]
        )
        artist = next((a for a in projection["artists"] if a["id"] == artist_id), None)
        if artist is None:
            raise HTTPException(404, "Artist not found")
        albums = [a for a in projection["albums"] if a["id"] in artist["album_ids"]]
        try:
            return await resolve_artist(
                store, catalogue, request.app.state.http, artist, albums
            )
        except Ambiguous as exc:
            return JSONResponse(
                {
                    "detail": "Choose this artist in catalogue search to distinguish matching names.",
                    "code": "conflict",
                    "candidates": exc.candidates,
                },
                status_code=409,
            )
        except (httpx.HTTPError, ValueError, KeyError):
            raise HTTPException(502, "catalogue_unavailable")

    class ArtistLink(BaseModel):
        id: str

    @router.put("/library-artists/{artist_id}")
    async def link_identity(artist_id: str, body: ArtistLink, request: Request):
        # The household owner chooses once; every member then shares the link.
        if request.state.user["role"] != "owner":
            raise HTTPException(403, "owner_only")
        projection = await asyncio.to_thread(
            store.user_libraries.projection, request.state.user["id"]
        )
        artist = next((a for a in projection["artists"] if a["id"] == artist_id), None)
        if artist is None:
            raise HTTPException(404, "Artist not found")
        chosen = valid(body.id)
        try:
            found = await catalogue.get(request.app.state.http, "artist/" + chosen)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {400, 404}:
                raise HTTPException(400, "invalid_catalogue_id")
            raise HTTPException(502, "catalogue_unavailable")
        except (httpx.HTTPError, ValueError, KeyError):
            raise HTTPException(502, "catalogue_unavailable")
        if found.get("id") != chosen or not found.get("name"):
            raise HTTPException(400, "invalid_catalogue_id")
        identity = {"id": chosen, "name": artist["name"]}
        # SQLite can wait on another writer, so the link is saved off the loop.
        await asyncio.to_thread(
            store.write_document, "library-artist:" + artist_id, identity
        )
        return identity

    def editions_query(group_id, offset):
        return {
            "release-group": group_id,
            "inc": "media",
            "limit": 50,
            "offset": offset,
        }

    async def recommended(request, release):
        """Whether this is the edition the album opens on (ranked first);
        any other was chosen from "Other versions". Unknown counts as yes."""
        group = (release.get("release-group") or {}).get("id")
        if not group:
            return True
        try:
            editions = await catalogue.get(
                request.app.state.http, "release", **editions_query(group, 0)
            )
        except (httpx.HTTPError, ValueError, KeyError):
            return True
        ranked = sorted(editions.get("releases", []), key=edition_rank)
        return not ranked or ranked[0].get("id") == release.get("id")

    def requested_from(user_id, release_id):
        """This person's unfinished requests from one release (not the whole
        household queue)."""
        with store.connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM queue WHERE status IN ("
                + ",".join("?" * len(ACTIVE))
                + ") AND json_extract(payload,'$.musicbrainz_release_id')=? AND "
                "EXISTS (SELECT 1 FROM json_each(queue.payload,'$.requested_by') "
                "WHERE value=?)",
                (*sorted(ACTIVE), release_id, user_id),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    @router.get(
        "/groups/{group_id}/editions",
        response_model=CatalogueEditionsDTO,
        responses=CONDITIONAL_RESPONSES,
    )
    async def editions(
        group_id: str,
        request: Request,
        offset: int = 0,
        if_none_match: str | None = Header(default=None),
    ):
        if not 0 <= offset <= 10000:
            raise HTTPException(422, "invalid_page")
        result = await metadata(
            request, "release", **editions_query(valid(group_id), offset)
        )
        # Ranked like the web, so the first is the recommended edition.
        ranked = sorted(result.get("releases", []), key=edition_rank)
        return conditional_json(
            request,
            CatalogueEditionsDTO,
            {
                "total": result.get("release-count", 0),
                "offset": offset,
                "editions": [
                    {
                        "id": r["id"],
                        "title": r["title"],
                        "date": r.get("date") or "",
                        "country": r.get("country") or "",
                        "description": r.get("disambiguation") or "",
                        "status": r.get("status") or "",
                        "formats": [m.get("format") or "" for m in r.get("media", [])],
                        "track_count": sum(
                            m.get("track-count") or 0 for m in r.get("media", [])
                        ),
                    }
                    for r in ranked
                ],
            },
        )

    @router.get(
        "/releases/{release_id}",
        response_model=CatalogueReleaseDTO,
        responses=CONDITIONAL_RESPONSES,
    )
    async def release(
        release_id: str,
        request: Request,
        if_none_match: str | None = Header(default=None),
    ):
        result = await metadata(
            request, "release/" + valid(release_id), inc=RELEASE_INC
        )
        user = request.state.user["id"]
        projection = await asyncio.to_thread(store.user_libraries.projection, user)
        personal = {t["id"] for t in projection["tracks"]}
        active = await asyncio.to_thread(requested_from, user, result["id"])
        explicit = not await recommended(request, result)
        tracks = []
        for raw in release_tracks(result):
            raw = {**raw, "explicit_edition": explicit}
            matched = store.index.match(raw)
            state = "not_in_library"
            if matched.track and matched.status == "downloaded":
                state = (
                    "in_library"
                    if matched.track["id"] in personal
                    else "household_available"
                )
            elif any(
                j.get("musicbrainz_recording_id") == raw.get("musicbrainz_recording_id")
                and j.get("track_number") == raw["track_number"]
                and j.get("disc_number") == raw["disc_number"]
                for j in active
            ):
                state = "requested"
            tracks.append(
                CatalogueTrackDTO.model_validate(
                    {
                        **raw,
                        "server_state": state,
                        "saved_track": (
                            track_dto(matched.track) if state == "in_library" else None
                        ),
                        "household_track": (
                            {
                                **track_dto(matched.track),
                                "server_state": "household_available",
                            }
                            if state == "household_available"
                            else None
                        ),
                    }
                ).model_dump(mode="json")
            )
        return conditional_json(
            request,
            CatalogueReleaseDTO,
            {
                "id": result["id"],
                "title": result["title"],
                "artist": credit(result.get("artist-credit", [])),
                "artist_id": next(
                    (
                        item["artist"]["id"]
                        for item in result.get("artist-credit", [])
                        if isinstance(item, dict)
                        and isinstance(item.get("artist"), dict)
                        and item["artist"].get("id")
                    ),
                    "",
                ),
                "tracks": tracks,
            },
        )

    async def warm_album(group_id):
        """Cache an album's first editions page and its recommended edition."""
        http = http_client()
        found = await catalogue.get(http, "release", **editions_query(group_id, 0))
        ranked = sorted(found.get("releases", []), key=edition_rank)
        if not ranked:
            return None
        await catalogue.get(http, "release/" + ranked[0]["id"], inc=RELEASE_INC)
        return ranked[0]["id"]

    router.warm_album = warm_album
    return router
