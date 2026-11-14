"""Apple Music library export (XML) import: parsing and the migration plan.

Moved from library.py (#173), which keeps the text helpers and the library
build that some thirty modules import.
"""

import io
import plistlib
from collections import defaultdict
from datetime import datetime, timezone

from .library import identifier, is_compilation, normalise, year_order


def parse_apple(raw: bytes) -> dict:
    if len(raw) > 20_000_000:
        raise ValueError("The export is too large (maximum 20 MB).")
    try:
        data = plistlib.load(io.BytesIO(raw))
    except Exception as exc:
        raise ValueError(
            "Choose a valid Apple Music XML library or playlist export."
        ) from exc
    if not isinstance(data, dict) or not isinstance(data.get("Tracks"), dict):
        raise ValueError("This file does not contain an Apple Music track inventory.")
    result, ignored = [], 0
    for key, track in data["Tracks"].items():
        if (
            not isinstance(track, dict)
            or not track.get("Name")
            or track.get("Video")
            or track.get("Movie")
        ):
            ignored += 1
            continue
        result.append(
            {
                "id": identifier(
                    "apple",
                    data.get("Library Persistent ID", ""),
                    track.get("Persistent ID", key),
                ),
                "source_track_id": str(key),
                "title": str(track["Name"]),
                "artist": str(track.get("Artist") or "Unknown artist"),
                "album_artist": str(
                    track.get("Album Artist") or track.get("Artist") or "Unknown artist"
                ),
                "album": str(track.get("Album") or "Unknown release"),
                "compilation": bool(track.get("Compilation", False)),
                "year": int(track.get("Year") or 0),
                "track_number": int(track.get("Track Number") or 0),
                "disc_number": int(track.get("Disc Number") or 1),
                "duration": int(track.get("Total Time") or 0) / 1000,
                "favourited": bool(track.get("Favorited") or track.get("Loved")),
                "play_count": int(track.get("Play Count") or 0),
            }
        )
    if not result:
        raise ValueError("This export contains no music tracks to review.")
    by_source = {t["source_track_id"]: t["id"] for t in result}
    for key, raw_track in data["Tracks"].items():
        if (
            str(key) in by_source
            and isinstance(raw_track, dict)
            and raw_track.get("Track ID") is not None
        ):
            by_source[str(raw_track["Track ID"])] = by_source[str(key)]
    playlists = []
    for item in data.get("Playlists", []):
        if (
            not isinstance(item, dict)
            or not item.get("Name")
            or item.get("Master")
            or item.get("Folder")
        ):
            continue
        members = [
            by_source[str(entry["Track ID"])]
            for entry in item.get("Playlist Items", [])
            if isinstance(entry, dict) and str(entry.get("Track ID")) in by_source
        ]
        playlists.append(
            {
                "id": identifier(
                    "apple-playlist",
                    data.get("Library Persistent ID", ""),
                    item.get("Playlist Persistent ID") or item["Name"],
                ),
                "name": str(item["Name"]),
                "track_ids": members,
            }
        )
    return {
        "tracks": result,
        "playlists": playlists,
        "source_name": ", ".join(p["name"] for p in playlists[:3])
        or "Apple Music library",
        "imported_at": datetime.now(timezone.utc).isoformat(),
        "ignored_records": ignored,
    }


def build_migration(
    source: dict, library: dict, selected: set[str], index=None
) -> dict:
    from .matching import LibraryIndex

    index = index or LibraryIndex(library)
    groups = defaultdict(list)
    for raw in source.get("tracks", []):
        track = dict(raw)
        match = index.match(track)
        track["status"] = match.status
        track["saved_id"] = match.track["id"] if match.track else None
        track["match_kind"] = match.kind
        track["reason"] = (
            "Artist, track and release match a saved file."
            if match.track
            else (
                "A saved song has this name, but its edition, context or duration needs review."
                if match.kind == "conflict"
                else "No matching track was found in the library snapshot."
            )
        )
        track["selected"] = track["id"] in selected and track["status"] != "downloaded"
        groups[(normalise(track["album_artist"]), normalise(track["album"]))].append(
            track
        )
    albums = []
    for key, tracks in groups.items():
        tracks.sort(key=lambda t: (t["disc_number"], t["track_number"], t["title"]))
        first = tracks[0]
        albums.append(
            {
                "id": identifier("migration-album", *key),
                "title": first["album"],
                "artist": first["album_artist"],
                "year": first["year"],
                "compilation": is_compilation(first["album_artist"], tracks),
                "tracks": tracks,
                "track_count": len(tracks),
                "saved_count": sum(t["status"] == "downloaded" for t in tracks),
                "selected_count": sum(t["selected"] for t in tracks),
                "library_album_id": next(
                    (
                        a["id"]
                        for a in library["albums"]
                        if normalise(a["artist"]) == key[0]
                        and normalise(a["title"]) == key[1]
                    ),
                    None,
                ),
            }
        )
    albums.sort(
        key=lambda a: (
            a["compilation"],
            a["artist"].casefold(),
            year_order(a["year"]),
            a["title"].casefold(),
        )
    )
    all_tracks = [t for a in albums for t in a["tracks"]]
    return {
        "source_name": source.get("source_name", "No export imported"),
        "imported_at": source.get("imported_at"),
        "albums": albums,
        "stats": {
            "tracks": len(all_tracks),
            "saved": sum(t["status"] == "downloaded" for t in all_tracks),
            "missing": sum(t["status"] == "missing" for t in all_tracks),
            "review": sum(t["status"] == "review" for t in all_tracks),
            "selected": sum(t["selected"] for t in all_tracks),
        },
    }
