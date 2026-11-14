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
            "SELECT track_id,seconds FROM play_events WHERE user_id=? AND played_at>=? AND played_at<=? AND seconds>=?",
            (user, start, int(now) + 300, LISTEN_SECONDS),
        ).fetchall()
        before = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT track_id FROM play_events WHERE user_id=? AND played_at<? AND seconds>=?",
                (user, start, LISTEN_SECONDS),
            )
        ]
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

    def resolve(track_id):
        if track_id not in known:
            track = library.get(track_id)
            if track is None and track_id.startswith("mix:"):
                try:
                    track = mixes.entry(user, track_id)
                except LookupError:
                    track = None
            known[track_id] = track
        return known[track_id]

    songs, song_ids, artists, artist_names = Counter(), {}, Counter(), {}
    artist_seconds, albums, album_info, editions = Counter(), Counter(), {}, Counter()
    for track_id, seconds in rows:
        if track_id.startswith("mix:"):
            editions[track_id.split(":")[1]] += 1
        track = resolve(track_id)
        if not track:
            continue
        key = mix_key(track)
        songs[key] += 1
        # The library copy plays from Rewind; a mix entry only while it lasts.
        if key not in song_ids or track_id in library:
            song_ids[key] = track_id
        name = track.get("artist") or ""
        if name:
            artists[name.casefold()] += 1
            artist_seconds[name.casefold()] += seconds
            artist_names.setdefault(name.casefold(), name)
        if track.get("album"):
            album = (
                track["album"].casefold(),
                (track.get("album_artist") or name).casefold(),
            )
            albums[album] += 1
            album_info.setdefault(album, track)
    earlier = {(t.get("artist") or "").casefold() for t in map(resolve, before) if t}
    with store.connect() as conn:
        titles = {
            identity: conn.execute(
                "SELECT payload FROM mix_editions WHERE id=? AND user_id=?",
                (identity, user),
            ).fetchone()
            for identity in editions
        }
    top_mixes = Counter()
    for identity, count in editions.items():
        if titles.get(identity):
            top_mixes[json.loads(titles[identity][0]).get("title") or "Mix"] += count

    def song(key):
        track_id = song_ids[key]
        track = resolve(track_id)
        if track_id in library:
            from .user_library import track_dto

            return {**track_dto(track), "server_state": "in_library"}
        _, identity, index = track_id.split(":")
        dto = mixes.track_dto(identity, int(index), track, members)
        if dto:
            return dto.model_dump()
        # No longer playable: shown, not played.
        return {
            **{k: v for k, v in track.items() if k in SHOWN},
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
        "minutes": round(sum(seconds for _, seconds in rows) / 60),
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
