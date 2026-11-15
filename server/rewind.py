"""Rewind: a person's listening for the week, month or year so far (#203).

Counted from their own play events in the household's time zone (the mixes'
setting). A play under 30 s is a skip, not a listen, as on the clients."""

from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime, timedelta
import json
import time
from zoneinfo import ZoneInfo

from .artist_identity import cached_identities
from .library import normalise
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
    return midnight(day, zone)


def midnight(day, zone):
    # Built from the date, so a daylight-saving change in between holds.
    return int(datetime(day.year, day.month, day.day, tzinfo=zone).timestamp())


def day_starts(start, now, zone):
    """Each local midnight from `start`'s day up to and including today."""
    day = datetime.fromtimestamp(start, zone).date()
    today = datetime.fromtimestamp(now, zone).date()
    starts = []
    while day <= today:
        starts.append(midnight(day, zone))
        day += timedelta(days=1)
    return starts


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
            f"""SELECT p.track_id,p.seconds,p.played_at,{DETAIL} FROM play_events p
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
    projection = store.user_libraries.projection(user)
    library = {t["id"]: t for t in projection["tracks"] if t.get("id")}
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
    starts = day_starts(start, now, zone)
    day_plays, day_seconds = [0] * len(starts), [0] * len(starts)
    artist_covers, album_ids = defaultdict(Counter), defaultdict(Counter)
    for track_id, seconds, played_at, *kept in rows:
        track = resolve(track_id, kept)
        # The apps report a listen once, at 30 s: a listen counts as the
        # song's length when it's known (Codex round 1).
        heard = max(seconds, (track or {}).get("duration") or 0)
        total += heard
        # A listen reported a moment ahead of the clock still counts today.
        day = min(max(bisect_right(starts, played_at) - 1, 0), len(starts) - 1)
        day_plays[day] += 1
        day_seconds[day] += heard
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
            if address := cover(track):
                artist_covers[name.casefold()][address] += 1
        if track.get("album"):
            album = (
                track["album"].casefold(),
                (track.get("album_artist") or name).casefold(),
            )
            albums[album] += 1
            album_info.setdefault(album, track)
            if track_id in library and track.get("album_id"):
                album_ids[album][track["album_id"]] += 1
    earlier = set()
    for track_id, *kept in before:
        track = resolve(track_id, kept)
        if track and track.get("artist"):
            earlier.add(track["artist"].casefold())
    kinds = {}
    if editions:
        with store.connect() as conn:
            for identity in editions:
                row = conn.execute(
                    "SELECT kind,json_extract(payload,'$.title') FROM mix_editions WHERE id=? AND user_id=?",
                    (identity, user),
                ).fetchone()
                if row:
                    kinds[identity] = row[0]
                    if row[1]:
                        mix_titles.setdefault(identity, row[1])
    top_mixes, mix_editions = Counter(), defaultdict(Counter)
    for identity, count in editions.items():
        if identity in mix_titles:
            top_mixes[mix_titles[identity]] += count
            mix_editions[mix_titles[identity]][identity] += count

    def mix(title, count):
        # The most played edition still kept names the mix and its kind.
        kept = [i for i, _ in mix_editions[title].most_common() if i in kinds]
        identity = kept[0] if kept else None
        return {
            "title": title,
            "plays": count,
            "id": identity,
            "kind": kinds.get(identity),
        }

    # The library's own pages: an artist by name, an album by the songs
    # played from it, else by its release or its title and artist.
    library_artists = {}
    for entry in projection.get("artists", []):
        library_artists.setdefault(normalise(entry["name"]), entry)
    by_id, by_release, by_title = {}, {}, {}
    for entry in projection.get("albums", []):
        by_id[entry["id"]] = entry
        if entry.get("musicbrainz_release_id"):
            by_release.setdefault(entry["musicbrainz_release_id"], entry)
        by_title.setdefault(
            (entry["title"].casefold(), (entry.get("artist") or "").casefold()), entry
        )
    ranked_artists = artists.most_common(TOP)
    found = {
        key: library_artists.get(normalise(artist_names[key]))
        for key, _ in ranked_artists
    }
    identities = cached_identities(store, [a for a in found.values() if a])

    def artist(key, count):
        entry = found[key]
        identity = identities.get(entry["id"]) if entry else None
        covers = artist_covers.get(key)
        return {
            "name": artist_names[key],
            "plays": count,
            "minutes": round(artist_seconds[key] / 60),
            "id": entry["id"] if entry else None,
            "musicbrainz_artist_id": identity["id"] if identity else None,
            "new": key not in earlier,
            "cover_url": covers.most_common(1)[0][0] if covers else None,
        }

    def album(key, count):
        info = album_info[key]
        release = info.get("musicbrainz_release_id")
        if album_ids.get(key):
            played = album_ids[key].most_common(1)[0][0]
            entry = by_id.get(played, {"id": played})
        else:
            entry = by_release.get(release) or by_title.get(key)
        return {
            "title": info["album"],
            "artist": info.get("album_artist") or info.get("artist", ""),
            "plays": count,
            "cover_url": cover(info),
            "id": entry["id"] if entry else None,
            "musicbrainz_release_id": release
            or (entry or {}).get("musicbrainz_release_id"),
        }

    def song(key):
        track_id, track = song_ids[key]
        if track_id in library:
            from .user_library import track_dto

            return {**track_dto(track), "server_state": "in_library"}
        if track_id.startswith("mix:"):
            _, identity, index = track_id.split(":")
            # Playable only as playback allows it: a retired edition past its
            # lease is refused even while its audio is cached (Codex round 2).
            try:
                entry = mixes.entry(user, track_id, playback=True)
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
        "top_artists": [artist(key, count) for key, count in ranked_artists],
        "top_albums": [album(key, count) for key, count in albums.most_common(TOP)],
        "new_artists": [
            artist_names[key] for key, _ in artists.most_common() if key not in earlier
        ][:TOP],
        "top_mixes": [mix(title, count) for title, count in top_mixes.most_common(TOP)],
        "days": [
            {"start": day, "minutes": round(day_seconds[i] / 60), "plays": day_plays[i]}
            for i, day in enumerate(starts)
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
