"""Rewind: a person's listening for the week, month or year so far (#203).

Counted from their own play events in the household's time zone (the mixes'
setting). A play under 30 s is a skip, not a listen, as on the clients."""

from collections import Counter
from datetime import datetime, timedelta
import json
import time
from zoneinfo import ZoneInfo

from .mix_cache import mix_key
from .paths import versioned

LISTEN_SECONDS = 30
TOP = 10


def period_start(period, now, zone):
    """Midnight on the first day of `period` in `zone`, as epoch seconds."""
    day = datetime.fromtimestamp(now, zone).date()
    if period == "week":
        day -= timedelta(days=day.weekday())
    elif period == "month":
        day = day.replace(day=1)
    else:
        day = day.replace(month=1, day=1)
    # Built from the date, so a daylight-saving change in between holds.
    return int(datetime(day.year, day.month, day.day, tzinfo=zone).timestamp())


def cover(track):
    if track.get("musicbrainz_release_id"):
        return f"/api/v1/catalogue/artwork/release/{track['musicbrainz_release_id']}"
    return versioned(track.get("cover_url"))


def rewind(store, mixes, user, period):
    now = time.time()
    zone = ZoneInfo(mixes.cache.settings()["timezone"])
    start = period_start(period, now, zone)
    with store.connect() as conn:
        rows = conn.execute(
            f"""SELECT p.track_id,p.seconds,{DETAIL} FROM play_events p
            LEFT JOIN listen_details d ON d.user_id=p.user_id AND d.event_id=p.event_id
            WHERE p.user_id=? AND p.played_at>=? AND p.played_at<=? AND p.seconds>=?""",
            (user, start, int(now) + 300, LISTEN_SECONDS),
        ).fetchall()
        before = conn.execute(
            f"""SELECT DISTINCT p.track_id,{DETAIL} FROM play_events p
            LEFT JOIN listen_details d ON d.user_id=p.user_id AND d.event_id=p.event_id
            WHERE p.user_id=? AND p.played_at<? AND p.seconds>=?""",
            (user, start, LISTEN_SECONDS),
        ).fetchall()
        members = {
            r[0]
            for r in conn.execute(
                "SELECT track_id FROM library_members WHERE user_id=?", (user,)
            )
        }
    library = {
        t["id"]: t
        for t in store.user_libraries.projection(user)["tracks"]
        if t.get("id")
    }
    known = {}

    def resolve(track_id, kept):
        """The song: the library's, the kept details, or the mix entry."""
        if track_id in library:
            return library[track_id]
        if kept[0] is not None:
            return {
                "title": kept[0],
                "artist": kept[1],
                "album": kept[2],
                "album_artist": kept[3],
                "musicbrainz_release_id": kept[4],
                "duration": kept[5],
            }
        if track_id not in known:
            track = None
            if track_id.startswith("mix:"):
                try:
                    track = mixes.entry(user, track_id)
                except LookupError:
                    pass
            known[track_id] = track
        return known[track_id]

    songs, song_ids, artists, artist_names = Counter(), {}, Counter(), {}
    artist_seconds, albums, album_info, editions = Counter(), Counter(), {}, Counter()
    mix_titles, total = {}, 0
    for track_id, seconds, *kept in rows:
        track = resolve(track_id, kept)
        # The apps report a listen once, at 30 s: a listen counts as the
        # song's length when it's known (Codex round 1).
        heard = max(seconds, (track or {}).get("duration") or 0)
        total += heard
        if track_id.startswith("mix:"):
            identity = track_id.split(":")[1]
            editions[identity] += 1
            if kept[6]:
                mix_titles.setdefault(identity, kept[6])
        if not track:
            continue
        key = mix_key(track)
        songs[key] += 1
        # The library copy plays from Rewind; a mix entry only while it lasts.
        if key not in song_ids or track_id in library:
            song_ids[key] = (track_id, track)
        name = track.get("artist") or ""
        if name:
            artists[name.casefold()] += 1
            artist_seconds[name.casefold()] += heard
            artist_names.setdefault(name.casefold(), name)
        if track.get("album"):
            album = (
                track["album"].casefold(),
                (track.get("album_artist") or name).casefold(),
            )
            albums[album] += 1
            album_info.setdefault(album, track)
    earlier = set()
    for track_id, *kept in before:
        track = resolve(track_id, kept)
        if track and track.get("artist"):
            earlier.add(track["artist"].casefold())
    missing = [identity for identity in editions if identity not in mix_titles]
    if missing:
        with store.connect() as conn:
            for identity in missing:
                row = conn.execute(
                    "SELECT json_extract(payload,'$.title') FROM mix_editions WHERE id=? AND user_id=?",
                    (identity, user),
                ).fetchone()
                if row and row[0]:
                    mix_titles[identity] = row[0]
    top_mixes = Counter()
    for identity, count in editions.items():
        if identity in mix_titles:
            top_mixes[mix_titles[identity]] += count

    def song(key):
        track_id, track = song_ids[key]
        if track_id in library:
            from .user_library import track_dto

            return {**track_dto(track), "server_state": "in_library"}
        if track_id.startswith("mix:"):
            _, identity, index = track_id.split(":")
            entry = known.get(track_id)
            if entry is None and track_id not in known:
                try:
                    entry = mixes.entry(user, track_id)
                except LookupError:
                    entry = None
            if entry:
                dto = mixes.track_dto(identity, int(index), entry, members)
                if dto:
                    return dto.model_dump()
        # No longer playable here: shown, not played.
        return {
            **{k: v for k, v in track.items() if k in SHOWN and v is not None},
            "id": track_id,
            "available": False,
            "server_state": "not_in_library",
            "cover_url": cover(track),
        }

    return {
        "period": period,
        "start": start,
        "end": int(now),
        "timezone": str(zone),
        "plays": len(rows),
        "minutes": round(total / 60),
        "top_songs": [
            {"track": song(key), "plays": count}
            for key, count in songs.most_common(TOP)
        ],
        "top_artists": [
            {
                "name": artist_names[key],
                "plays": count,
                "minutes": round(artist_seconds[key] / 60),
            }
            for key, count in artists.most_common(TOP)
        ],
        "top_albums": [
            {
                "title": album_info[key]["album"],
                "artist": album_info[key].get("album_artist")
                or album_info[key].get("artist", ""),
                "plays": count,
                "cover_url": cover(album_info[key]),
            }
            for key, count in albums.most_common(TOP)
        ],
        "new_artists": [
            artist_names[key] for key, _ in artists.most_common() if key not in earlier
        ][:TOP],
        "top_mixes": [
            {"title": title, "plays": count}
            for title, count in top_mixes.most_common(TOP)
        ],
    }


DETAIL = "d.title,d.artist,d.album,d.album_artist,d.release_id,d.duration,d.mix_title"


SHOWN = {
    "title",
    "artist",
    "album",
    "album_artist",
    "year",
    "track_number",
    "disc_number",
    "duration",
    "musicbrainz_release_id",
    "musicbrainz_recording_id",
    "genre",
}
