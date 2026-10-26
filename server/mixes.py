"""Private, reproducible daily listening editions prepared before publication."""

import asyncio
from collections import Counter
from datetime import date, datetime, timedelta
import hashlib
import json
import logging
import threading
import time
import uuid
import httpx
from zoneinfo import ZoneInfo

from .paths import versioned
from .artist_identity import cached_identity
from .catalogue import background_metadata, credit
from .home import release_choice
from .matching import compatible
from .media import chart_region, settings, track_file
from .mix_cache import MixCache, MixDownloader, song_key
from .playlists import Playlists
from .api_models import TrackDTO

log = logging.getLogger("pig_music.mixes")
PASS_SECONDS = 240
SHUTDOWN_SECONDS = 10
KINDS = {
    "daily": "Daily blend",
    "fresh": "Fresh finds",
    "releases": "New from your artists",
    "back": "Back in the mix",
    "artist": "Around an artist",
    "genre": "Genre rotation",
    "decade": "Decade rotation",
    "popular": "In rotation",
    "deep": "Deep cuts",
    "recent": "Recently added",
}
# Home order: the lead blend and discovery first, library-only mixes last.
KIND_ORDER = (
    "CASE kind WHEN 'daily' THEN 0 WHEN 'fresh' THEN 2 WHEN 'releases' THEN 3"
    " WHEN 'artist' THEN 4 WHEN 'genre' THEN 5 WHEN 'decade' THEN 6"
    " WHEN 'popular' THEN 7 WHEN 'recent' THEN 8 WHEN 'deep' THEN 9"
    " WHEN 'back' THEN 10 ELSE 6 END,kind"
)
# Discovery sources a Daily blend and Fresh finds draw on.
DISCOVERY = {"related", "genre", "decade"}
SEEDS, PER_SEED, GENRE_SONGS, DECADE_SONGS = 5, 12, 20, 12
CANDIDATE_LIMIT = 100
RELEASE_ARTISTS, RELEASE_DAYS = 10, 90
# A rotating mix shorter than this is topped up from the library where that
# fits its theme, or not shown at all.
MIN_MIX = 20
# Radio keeps this many ready songs ahead of the listener, added in batches.
RADIO_BUFFER, RADIO_BATCH, RADIO_PAGE = 20, 20, 10


def decade_theme(tracks, day):
    """A decade with enough owned songs, rotating daily; None without one."""
    decades = Counter(
        t["year"] // 10 * 10
        for t in tracks
        if isinstance(t.get("year"), int) and t["year"] >= 1900
    )
    eligible = sorted(d for d, n in decades.items() if n >= 15)
    return (
        eligible[date.fromisoformat(str(day)).toordinal() % len(eligible)]
        if eligible
        else None
    )


class TrackKeys:
    """`song_key` once per track for one planning pass. The pass works on a
    stable snapshot, so a track object keeps its key; the object itself is
    kept with its key, so a reused `id()` can never return a stale one."""

    def __init__(self):
        self.known = {}

    def __call__(self, track):
        entry = self.known.get(id(track))
        if entry is None or entry[0] is not track:
            entry = self.known[id(track)] = (track, song_key(track))
        return entry[1]


def choose(
    tracks,
    count,
    salt,
    history=None,
    weights=None,
    previous=None,
    per_artist=2,
    key=song_key,
):
    """Daily weighted shuffle with diversity; identity is never guessed from title."""
    history = history or {}
    weights = weights or {}
    previous = previous or set()
    unique = {}
    for t in reversed(tracks):
        k = key(t)
        unique[t.get("musicbrainz_recording_id") or k] = (t, k)
    now = time.time()

    def rank(entry):
        k = entry[1]
        random = (int(hashlib.sha256((salt + k).encode()).hexdigest(), 16) + 1) / (
            2**256 + 1
        )
        return (
            history.get(k, 0) > now - 7 * 86400,
            k in previous,
            -(random ** (1 / weights.get(k, 1))),
        )

    ordered = [t for t, _ in sorted(unique.values(), key=rank)]
    result, artists = [], Counter()
    while ordered and len(result) < count:
        index = next(
            (
                i
                for i, t in enumerate(ordered)
                if artists[t["artist"]] < per_artist
                and (not result or result[-1]["artist"] != t["artist"])
            ),
            None,
        )
        if index is None:
            break
        candidate = ordered.pop(index)
        result.append(candidate)
        artists[candidate["artist"]] += 1
    return result


class Mixes:
    def __init__(self, store, home, runtime):
        self.store, self.home, self.runtime = store, home, runtime
        self.cache = MixCache(store)
        store.mix_cache = self.cache
        store.mixes = self
        self.stopping = asyncio.Event()
        # Set when a listener reaches the radio buffer, so the worker refills now.
        self.wake = asyncio.Event()
        self.radio_lock = threading.Lock()
        self.task = None

    def day(self):
        return datetime.now(ZoneInfo(self.cache.settings()["timezone"])).date()

    def start(self, client):
        self.task = asyncio.create_task(self.run(client))

    async def close(self):
        self.stopping.set()
        self.wake.set()
        if not self.task:
            return
        # Workers poll the stop flag, but a provider call can outlast the
        # supervisor's 25 s grace; durable cursors make cancellation safe.
        try:
            await asyncio.wait_for(asyncio.shield(self.task), timeout=SHUTDOWN_SECONDS)
        except TimeoutError:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    async def run(self, client):
        background_metadata.set(True)
        while not self.stopping.is_set():
            try:
                await self.prepare_pass(client)
            except Exception:
                log.exception("Mix preparation failed; retaining ready editions")
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=60)
            except TimeoutError:
                pass
            self.wake.clear()

    def people(self):
        with self.store.connect() as conn:
            return [r[0] for r in conn.execute("SELECT id FROM users WHERE disabled=0")]

    def history(self, user_id, tracks, key=song_key):
        with self.store.connect() as conn:
            recent = dict(
                conn.execute(
                    "SELECT track_id,MAX(played_at) FROM play_events WHERE user_id=? GROUP BY track_id",
                    (user_id,),
                )
            )
            history = dict(
                conn.execute(
                    "SELECT key,MAX(played_at) FROM mix_listens WHERE user_id=? GROUP BY key",
                    (user_id,),
                )
            )
        for track in tracks:
            k = key(track)
            history[k] = max(history.get(k, 0), recent.get(track.get("id"), 0))
        return history

    def hidden(self, user_id):
        with self.store.connect() as conn:
            return {
                r[0]
                for r in conn.execute(
                    "SELECT key FROM mix_feedback WHERE user_id=?", (user_id,)
                )
            }

    def plays(self, user_id):
        """Play counts by library track id and by mix song key."""
        with self.store.connect() as conn:
            by_track = dict(
                conn.execute(
                    "SELECT track_id,COUNT(*) FROM play_events WHERE user_id=? GROUP BY track_id",
                    (user_id,),
                )
            )
            by_key = dict(
                conn.execute(
                    "SELECT key,COUNT(*) FROM mix_listens WHERE user_id=? GROUP BY key",
                    (user_id,),
                )
            )
        return by_track, by_key

    def recently_added(self, user_id, days=30):
        with self.store.connect() as conn:
            return {
                r[0]
                for r in conn.execute(
                    "SELECT track_id FROM library_members WHERE user_id=? AND added_at>=? AND source!='migration'",
                    (user_id, int(time.time()) - days * 86400),
                )
            }

    def exposure(self, user, day):
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT payload FROM mix_editions WHERE user_id=? AND kind='daily' AND day<? ORDER BY day DESC LIMIT 1",
                (user, str(day)),
            ).fetchone()
        return {song_key(t) for t in json.loads(row[0])["tracks"]} if row else set()

    def favourite_weights(self, user):
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT payload FROM playlists WHERE id=? AND owner_id=?",
                ("pig-favourites:" + user, user),
            ).fetchone()
        return (
            {song_key(t): 3 for t in json.loads(row[0]).get("tracks", [])}
            if row
            else {}
        )

    async def candidates(self, client, projection):
        """Use public artist/tag seeds only; personal listening never leaves PigMusic."""
        seeds = await self.home.seeds(client, projection, {"unavailable": False})
        records = []
        for identity, name in seeds[:SEEDS]:
            if self.stopping.is_set():
                break
            data, _ = await self.home.feed(
                client,
                "lb-radio/artist/" + identity,
                mode="easy",
                max_similar_artists=8,
                max_recordings_per_artist=4,
                pop_begin=20,
                pop_end=100,
            )
            seed_records = []
            pools = [items for items in data.values() if isinstance(items, list)]
            for position in range(4):
                for items in pools:
                    if position < len(items) and isinstance(items[position], dict):
                        seed_records.append(
                            (
                                items[position].get("recording_mbid"),
                                "Similar to " + name,
                                "related",
                            )
                        )
            records.extend(seed_records[:PER_SEED])
        labels = {
            t["genre"].casefold(): t["genre"]
            for t in projection.get("tracks", [])
            if t.get("genre")
        }
        genres = Counter(
            genre
            for artist, genre in {
                (t["artist"].casefold(), t["genre"].casefold())
                for t in projection.get("tracks", [])
                if t.get("genre")
            }
        )
        themes = [
            key
            for key, count in sorted(
                genres.items(), key=lambda item: (-item[1], item[0])
            )[:3]
        ]
        ordinal = date.fromisoformat(
            projection.get("mix_day") or str(self.day())
        ).toordinal()
        genre = labels[themes[ordinal % len(themes)]] if themes else None
        if genre:
            data, _ = await self.home.feed(
                client,
                "lb-radio/tags",
                tag=genre,
                operator="or",
                count=GENRE_SONGS,
                pop_begin=20,
                pop_end=100,
            )
            records.extend(
                (r.get("recording_mbid"), genre, "genre")
                for r in data.get("recordings", [])[:GENRE_SONGS]
                if isinstance(r, dict)
            )
        decade = decade_theme(
            projection.get("tracks", []), projection.get("mix_day") or self.day()
        )
        if decade:
            data, _ = await self.home.feed(
                client,
                "lb-radio/tags",
                tag=f"{decade}s",
                operator="or",
                count=DECADE_SONGS,
                pop_begin=20,
                pop_end=100,
            )
            records.extend(
                (r.get("recording_mbid"), f"From the {decade}s", "decade")
                for r in data.get("recordings", [])[:DECADE_SONGS]
                if isinstance(r, dict)
            )
        # Current regional charts are a separate signal from lifetime popularity.
        popular = await self.home.popular(client)
        candidates, seen = [], set()
        for recording, reason, bucket in records[:200]:
            if self.stopping.is_set() or len(candidates) >= CANDIDATE_LIMIT:
                break
            if not self.home.identity(recording):
                continue
            if recording in seen:
                if bucket in {"genre", "decade"}:
                    for item in candidates:
                        if item["musicbrainz_recording_id"] == recording:
                            item[bucket] = genre if bucket == "genre" else decade
                continue
            seen.add(recording)
            try:
                data = await self.home.catalogue.get(
                    client,
                    "recording/" + recording,
                    inc="releases+artist-credits+isrcs",
                )
                release = release_choice(data.get("releases", []))
                if not release or not credit(data.get("artist-credit", [])):
                    continue
                candidates.append(
                    {
                        "title": data["title"],
                        "artist": credit(data["artist-credit"]),
                        "album": release["title"],
                        "duration": (data.get("length") or 0) / 1000,
                        "musicbrainz_recording_id": recording,
                        "musicbrainz_release_id": release["id"],
                        "isrcs": data.get("isrcs", []),
                        "reason": reason,
                        "bucket": bucket,
                        "genre": genre if bucket == "genre" else None,
                        "decade": decade if bucket == "decade" else None,
                    }
                )
            except (ValueError, KeyError, httpx.HTTPError):
                continue
        candidates += await self.releases(client, projection)
        # Chart songs only enter a personalised popular mix with an artist/genre affinity.
        artist_names = {
            t["artist"].casefold() for t in projection.get("tracks", []) + candidates
        }
        for item in popular.get("songs", []):
            if (
                item.get("release_id")
                and item.get("artist", "").casefold() in artist_names
            ):
                candidates.append(
                    {
                        "title": item["name"],
                        "artist": item["artist"],
                        "album": item.get("album", ""),
                        "musicbrainz_recording_id": item["id"],
                        "musicbrainz_release_id": item["release_id"],
                        "bucket": "popular",
                        "reason": "Popular in "
                        + chart_region(settings(self.store)).upper(),
                    }
                )
        # A recording gets one allocation bucket, even when it is both a radio
        # recommendation and in today's chart. Retain the richer recording data.
        unique = {}
        for track in candidates:
            key = track.get("musicbrainz_recording_id") or song_key(track)
            if key not in unique:
                unique[key] = track
            elif track["bucket"] == "popular":
                unique[key].update(bucket="popular", reason=track["reason"])
            if track.get("genre"):
                unique[key]["genre"] = track["genre"]
            if track.get("decade"):
                unique[key]["decade"] = track["decade"]
        return list(unique.values()), genre

    async def releases(self, client, projection):
        """Recent recordings by library artists already resolved to MusicBrainz.

        One search per artist; ten artists rotate daily so the whole library
        is covered over time without new identity lookups.
        """
        day = date.fromisoformat(str(projection.get("mix_day") or self.day()))
        artists = [
            (a, known)
            for a in projection.get("artists", [])
            if not a.get("compilation_only")
            and (known := cached_identity(self.store, a))
        ]
        if not artists:
            return []
        offset = day.toordinal() % len(artists)
        # A month-aligned window keeps the search cacheable for the whole day.
        since = (day - timedelta(days=RELEASE_DAYS)).replace(day=1)
        found = []
        for artist, known in (artists[offset:] + artists[:offset])[:RELEASE_ARTISTS]:
            if self.stopping.is_set():
                break
            try:
                data = await self.home.catalogue.get(
                    client,
                    "recording/",
                    query=f"arid:{known['id']} AND firstreleasedate:[{since} TO *]"
                    " AND status:official AND video:false",
                    limit=25,
                )
            except (ValueError, KeyError, httpx.HTTPError):
                continue
            picks = 0
            for recording in data.get("recordings", []):
                release = release_choice(recording.get("releases", []))
                date_text = (release or {}).get("date") or ""
                if (
                    picks >= 3
                    or not release
                    or not recording.get("id")
                    or not credit(recording.get("artist-credit", []))
                    or date_text < str(since)
                ):
                    continue
                picks += 1
                found.append(
                    {
                        "title": recording["title"],
                        "artist": credit(recording["artist-credit"]),
                        "album": release["title"],
                        "duration": (recording.get("length") or 0) / 1000,
                        "musicbrainz_recording_id": recording["id"],
                        "musicbrainz_release_id": release["id"],
                        "isrcs": recording.get("isrcs", []),
                        "reason": "New from " + artist["name"],
                        "bucket": "release",
                        "released": date_text,
                    }
                )
        return found

    def edition(self, user_id, kind, day):
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT id,payload,active FROM mix_editions WHERE user_id=? AND kind=? AND day=?",
                (user_id, kind, str(day)),
            ).fetchone()
        return row

    def stage(self, user_id, kind, day, title, reason, tracks, backfill=None):
        identity = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"pig-mix:v1:{user_id}:{kind}:{day}")
        )
        payload = {
            "title": title,
            "reason": reason,
            "tracks": tracks,
            # The intended songs stay visible to Admin after failures shorten tracks.
            "planned": tracks,
            "state": "preparing",
            "attempts": 0,
            "retry_at": 0,
        }
        if backfill is not None:
            payload.update(backfill=backfill, minimum=MIN_MIX)
        with self.store.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO mix_editions(id,user_id,kind,day,payload) VALUES (?,?,?,?,?)",
                (identity, user_id, kind, str(day), json.dumps(payload)),
            )
        return identity

    def plan(self, user_id, kind, day, title, reason, tracks, backfill=()):
        """Stage a rotating mix that can reach MIN_MIX; None when it cannot.

        Backfill is owned music that fits the mix, used in order when planned
        songs fail to prepare.
        """
        seen = {song_key(t) for t in tracks}
        planned, extra = len(seen), []
        for track in backfill:
            if song_key(track) not in seen:
                seen.add(song_key(track))
                extra.append(track)
        if planned + len(extra) < MIN_MIX:
            log.info(
                "Mixes: skipped %s for %s on %s: %d songs, %d needed",
                title,
                user_id,
                day,
                planned + len(extra),
                MIN_MIX,
            )
            return None
        return self.stage(
            user_id, kind, day, title, reason, tracks, backfill=extra[: MIN_MIX * 2]
        )

    @staticmethod
    def neighbourhood_fill(owned, artist, neighbourhood, salt, history, key=song_key):
        """The artist's other songs, then owned songs by their neighbours, then
        owned songs in the artist's main genre."""
        own = sorted(
            (t for t in owned if t["artist"] == artist),
            key=lambda t: hashlib.sha256((salt + key(t)).encode()).hexdigest(),
        )
        near = {t["artist"].casefold() for t in neighbourhood}
        genres = Counter((t.get("genre") or "").casefold() for t in own)
        genres.pop("", None)
        genre = genres.most_common(1)[0][0] if genres else None
        others = [t for t in owned if t["artist"] != artist]
        return (
            own[:MIN_MIX]
            + choose(
                [t for t in others if t["artist"].casefold() in near],
                MIN_MIX,
                salt + ":near",
                history,
                per_artist=4,
                key=key,
            )
            + choose(
                [
                    t
                    for t in others
                    if genre and (t.get("genre") or "").casefold() == genre
                ],
                MIN_MIX,
                salt + ":genre",
                history,
                per_artist=3,
                key=key,
            )
        )

    def publish(self, identity, tracks, today):
        with self.store.lifecycle_lock, self.store.connect() as conn:
            row = conn.execute(
                "SELECT user_id,kind,day,payload FROM mix_editions WHERE id=?",
                (identity,),
            ).fetchone()
            payload = {**json.loads(row[3]), "tracks": tracks, "state": "ready"}
            available_keys = {song_key(t) for t in tracks}
            payload["failures"] = {
                key: error
                for key, error in payload.get("failures", {}).items()
                if key not in available_keys
            }
            payload.pop("blocked", None)
            active = row[2] <= str(today) and not row[1].startswith("saved:")
            if active:
                conn.execute(
                    "UPDATE mix_editions SET active=0,retired_at=? WHERE user_id=? AND kind=? AND active=1",
                    (time.time(), row[0], row[1]),
                )
            conn.execute(
                "UPDATE mix_editions SET payload=?,active=? WHERE id=?",
                (json.dumps(payload), int(active), identity),
            )
        log.info(
            "Mixes: published %s for %s on %s with %d of %d songs",
            payload["title"],
            row[0],
            row[2],
            len(tracks),
            len(payload.get("planned") or tracks),
        )

    def activate(self, today):
        with self.store.lifecycle_lock, self.store.connect() as conn:
            conn.execute(
                "UPDATE mix_editions SET active=0,retired_at=? WHERE user_id IN (SELECT id FROM users WHERE disabled=1) AND (active=1 OR retired_at IS NULL)",
                (time.time(),),
            )
            conn.execute(
                "UPDATE mix_editions SET retired_at=? WHERE active=0 AND retired_at IS NULL AND day<?",
                (time.time(), str(today)),
            )
            for identity, user, kind, payload in conn.execute(
                "SELECT id,user_id,kind,payload FROM mix_editions WHERE day=? AND active=0 AND retired_at IS NULL AND kind!='radio' AND user_id IN (SELECT id FROM users WHERE disabled=0)",
                (str(today),),
            ).fetchall():
                if json.loads(payload).get("state") != "ready":
                    continue
                conn.execute(
                    "UPDATE mix_editions SET active=0,retired_at=? WHERE user_id=? AND kind=? AND active=1",
                    (time.time(), user, kind),
                )
                conn.execute("UPDATE mix_editions SET active=1 WHERE id=?", (identity,))

    async def prepare_pass(self, client):
        today = self.day()
        await asyncio.to_thread(self.activate, today)
        # Reclaim retired audio before checking capacity for new editions.
        await asyncio.to_thread(self.cache.cleanup)
        for user in await asyncio.to_thread(self.people):
            if self.stopping.is_set():
                return
            try:
                await asyncio.wait_for(
                    self.prepare_person(client, user, today), timeout=PASS_SECONDS
                )
            except TimeoutError:
                log.info(
                    "Mix pass time budget reached; resuming persisted work next pass"
                )
            except Exception:
                log.exception(
                    "Preparing one person's mixes failed; other members continue"
                )
            try:
                await asyncio.wait_for(
                    self.prepare_radio(user, today), timeout=PASS_SECONDS
                )
            except TimeoutError:
                pass
            except Exception:
                log.exception("Preparing one person's radio failed")
        await asyncio.to_thread(self.cache.cleanup)

    def library_snapshot(self, user):
        """This person's playable, unhidden songs and their keys: the stable
        input every planning step of one pass works from (worker thread)."""
        projection = self.store.user_libraries.projection(user)
        hidden = self.hidden(user)
        keys = TrackKeys()
        owned = [
            t
            for t in projection["tracks"]
            if t.get("available", True) and keys(t) not in hidden
        ]
        return projection, owned, hidden, keys

    def first_back(self, user, today, owned, history, keys):
        tracks = choose(owned, 30, f"{user}:{today}:back", history, key=keys)
        identity = self.plan(
            user,
            "back",
            today,
            KINDS["back"],
            "Saved music worth another listen",
            tracks,
        )
        if identity:
            self.publish(identity, tracks, today)

    async def prepare_person(self, client, user, today):
        deadline = time.monotonic() + PASS_SECONDS
        current = await asyncio.to_thread(self.edition, user, "daily", today)
        upcoming = await asyncio.to_thread(
            self.edition, user, "daily", today + timedelta(days=1)
        )
        if (
            current
            and upcoming
            and all(
                json.loads(row[1])["state"] == "ready" for row in (current, upcoming)
            )
        ):
            # Readiness belongs to each family, not just the lead Daily blend.
            for day in (today, today + timedelta(days=1)):
                await self.prepare_staged(user, day, today, deadline)
            return
        projection, owned, hidden, keys = await asyncio.to_thread(
            self.library_snapshot, user
        )
        if not owned:
            # An imported inventory is still a taste signal even before its
            # files arrive. With no taste at all, label regional charts honestly.
            public = None
            for day in (today, today + timedelta(days=1)):
                kind = "daily" if projection["tracks"] else "popular"
                if not await asyncio.to_thread(self.edition, user, kind, day):
                    if projection["tracks"]:
                        pool, _ = await self.candidates(
                            client, {**projection, "mix_day": str(day)}
                        )
                        title, reason = (
                            KINDS["daily"],
                            "Discoveries inspired by your library",
                        )
                    else:
                        if public is None:
                            public = await self.home.popular(client)
                        pool = [
                            {
                                "title": t["name"],
                                "artist": t["artist"],
                                "album": t.get("album", ""),
                                "musicbrainz_recording_id": t["id"],
                                "musicbrainz_release_id": t["release_id"],
                            }
                            for t in public.get("songs", [])
                            if t.get("release_id")
                        ]
                        title = public.get("title") or (
                            "Popular in " + chart_region(settings(self.store)).upper()
                        )
                        reason = "Regional chart music. Add music to your library for personalised mixes."
                    tracks = choose(
                        [t for t in pool if song_key(t) not in hidden],
                        30,
                        f"{user}:{day}",
                        previous=await asyncio.to_thread(self.exposure, user, day),
                    )
                    if tracks:
                        await asyncio.to_thread(
                            self.plan, user, kind, day, title, reason, tracks
                        )
                await self.prepare_staged(user, day, today, deadline)
            return
        history = await asyncio.to_thread(self.history, user, owned, keys)
        weights = await asyncio.to_thread(self.favourite_weights, user)
        # Give a new person a playable library mix before any external lookup.
        if not await asyncio.to_thread(self.edition, user, "back", today):
            await asyncio.to_thread(self.first_back, user, today, owned, history, keys)
        for day in (today, today + timedelta(days=1)):
            if self.stopping.is_set():
                return
            if not await asyncio.to_thread(self.edition, user, "daily", day):
                candidates, genre = await self.candidates(
                    client, {**projection, "mix_day": str(day)}
                )
                await asyncio.to_thread(
                    self.plan_library_day,
                    user,
                    day,
                    owned,
                    hidden,
                    candidates,
                    genre,
                    history,
                    weights,
                    keys,
                )
            await self.prepare_staged(user, day, today, deadline)

    def plan_library_day(
        self, user, day, owned, hidden, candidates, genre, history, weights, keys
    ):
        """Every library-backed mix for one day. Pure CPU and SQLite work on a
        stable snapshot, so it runs on a worker thread, never the event loop."""
        pick = lambda *args, **kwargs: choose(*args, key=keys, **kwargs)
        candidates = [t for t in candidates if keys(t) not in hidden]
        owned_keys = {t.get("musicbrainz_recording_id") or keys(t) for t in owned}
        discoveries = [
            t
            for t in candidates
            if (t.get("musicbrainz_recording_id") or keys(t)) not in owned_keys
        ]
        salt = f"{user}:{day}"
        previous = self.exposure(user, day)
        pool = [t for t in discoveries if t["bucket"] in DISCOVERY]
        related = pick(pool, 15, salt, history, previous=previous)
        popular_tracks = pick(
            [t for t in discoveries if t["bucket"] == "popular"],
            30,
            salt + ":popular",
            history,
        )
        chart = pick(popular_tracks, 3, salt, history, previous=previous)
        familiar = pick(
            owned,
            max(12, 30 - len(related) - len(chart)),
            salt,
            history,
            weights,
            previous,
        )
        daily = pick(familiar + related + chart, 30, salt, history, previous=previous)
        self.plan(
            user,
            "daily",
            day,
            KINDS["daily"],
            (
                "Your favourites, fresh discoveries and a little of what's current"
                if related and chart
                else (
                    "Your library and fresh discoveries"
                    if related
                    else (
                        "Your library and current chart discoveries"
                        if chart
                        else "A fresh selection from your library"
                    )
                )
            ),
            daily,
            backfill=pick(
                owned,
                2 * MIN_MIX,
                salt + ":fill",
                history,
                weights,
                per_artist=3,
            ),
        )
        self.plan(
            user,
            "back",
            day,
            KINDS["back"],
            "Saved music worth another listen",
            pick(owned, 30, salt + ":back", history),
        )
        # Discoveries only, never repeating today's Daily blend.
        in_daily = {keys(t) for t in daily}
        fresh = pick(
            [t for t in pool if keys(t) not in in_daily],
            30,
            salt + ":fresh",
            history,
        )
        if len(fresh) >= 10:
            self.plan(
                user,
                "fresh",
                day,
                KINDS["fresh"],
                "New to you, picked from artists and genres you love",
                fresh,
            )
        releases = pick(
            [t for t in discoveries if t["bucket"] == "release"],
            30,
            salt + ":releases",
            history,
            per_artist=3,
        )
        if len(releases) >= 3:
            self.plan(
                user,
                "releases",
                day,
                KINDS["releases"],
                "Recent releases by artists in your library",
                releases,
            )
        seeded = [
            t
            for t in owned
            if any(c.get("reason") == "Similar to " + t["artist"] for c in candidates)
        ]
        artist = pick(seeded or owned, 1, salt)[0]["artist"]
        neighbourhood = [
            t
            for t in candidates
            if ("Similar to " + artist).casefold() == t.get("reason", "").casefold()
        ]
        artist_tracks = pick(
            [t for t in owned if t["artist"] == artist] + neighbourhood,
            25,
            salt,
            history,
            per_artist=8,
        )
        if len({t["artist"].casefold() for t in artist_tracks}) > 1:
            self.plan(
                user,
                "artist",
                day,
                "Around " + artist,
                "An artist you saved and their musical neighbourhood",
                artist_tracks,
                backfill=self.neighbourhood_fill(
                    owned, artist, neighbourhood, salt, history, keys
                ),
            )
        if genre:
            same = lambda t: (t.get("genre") or "").casefold() == genre.casefold()
            genre_new = pick([t for t in pool if same(t)], 15, salt + ":genre", history)
            genre_owned = pick(
                [t for t in owned if same(t)],
                30 - len(genre_new),
                salt + ":genre",
                history,
            )
            self.plan(
                user,
                "genre",
                day,
                genre + " rotation",
                "A genre from your library",
                pick(genre_owned + genre_new, 30, salt, history),
                backfill=pick(
                    [t for t in owned if same(t)],
                    2 * MIN_MIX,
                    salt + ":fill",
                    history,
                    per_artist=3,
                ),
            )
        decade = decade_theme(owned, day)
        if decade:
            decade_new = pick(
                [t for t in pool if t.get("decade") == decade],
                12,
                salt + ":decade",
                history,
            )
            decade_owned = pick(
                [
                    t
                    for t in owned
                    if isinstance(t.get("year"), int) and t["year"] // 10 * 10 == decade
                ],
                30 - len(decade_new),
                salt + ":decade",
                history,
            )
            self.plan(
                user,
                "decade",
                day,
                f"Your {decade}s",
                f"Music from the {decade}s, from your library and beyond",
                pick(decade_owned + decade_new, 30, salt, history),
                backfill=pick(
                    [
                        t
                        for t in owned
                        if isinstance(t.get("year"), int)
                        and t["year"] // 10 * 10 == decade
                    ],
                    2 * MIN_MIX,
                    salt + ":fill",
                    history,
                    per_artist=3,
                ),
            )
        plays = self.plays(user)
        deep = pick(
            [
                t
                for t in owned
                if plays[0].get(t.get("id"), 0) + plays[1].get(keys(t), 0) <= 1
            ],
            30,
            salt + ":deep",
            history,
        )
        if len(deep) >= 10:
            self.plan(
                user,
                "deep",
                day,
                KINDS["deep"],
                "Songs you own but have barely played",
                deep,
            )
        added = self.recently_added(user)
        recent = pick(
            [t for t in owned if t.get("id") in added],
            30,
            salt + ":recent",
            history,
        )
        if len(recent) >= 10:
            self.plan(
                user,
                "recent",
                day,
                KINDS["recent"],
                "Music added to your library in the last 30 days",
                recent,
            )
        if popular_tracks:
            self.plan(
                user,
                "popular",
                day,
                KINDS["popular"],
                "Current chart music close to your taste",
                popular_tracks,
            )

    async def prepare_staged(self, user, day, today, deadline=None):
        deadline = deadline if deadline is not None else time.monotonic() + PASS_SECONDS
        rows = await asyncio.to_thread(self.pending, user, day)
        for identity, raw, kind in rows:
            payload = json.loads(raw)
            if payload["state"] == "ready" or payload.get("retry_at", 0) > time.time():
                continue
            if self.stopping.is_set() or time.monotonic() >= deadline:
                return
            payload["worked_at"] = time.time()
            payload.pop("blocked", None)
            hidden = (
                await asyncio.to_thread(self.hidden, user)
                if not kind.startswith("saved:")
                else set()
            )
            payload["tracks"] = [
                t for t in payload["tracks"] if song_key(t) not in hidden
            ]
            await asyncio.to_thread(self.update, identity, payload)
            deferred = False
            # Resume across passes/restarts. A repeatedly failing early song
            # cannot prevent later songs from ever getting a download attempt.
            cursor = payload.get("cursor", 0)
            for position in range(cursor, len(payload["tracks"])):
                track = payload["tracks"][position]
                if self.stopping.is_set() or time.monotonic() >= deadline:
                    return
                try:
                    await asyncio.to_thread(self.cache.available, track)
                except FileNotFoundError:
                    blocked = await asyncio.to_thread(self.blocked)
                    if blocked:
                        deferred = True
                        payload["blocked"] = blocked
                        continue
                    downloader = await asyncio.to_thread(
                        MixDownloader,
                        self.cache,
                        self.runtime,
                        lambda: self.stopping.is_set() or time.monotonic() >= deadline,
                    )
                    if not await asyncio.to_thread(downloader.fetch, track):
                        deferred |= downloader.result != "failed"
                        if downloader.result == "failed":
                            payload.setdefault("failures", {})[song_key(track)] = (
                                downloader.error or "No matching recording available"
                            )
                            log.info(
                                "Mixes: %s by %s unavailable for %s: %s",
                                track.get("title"),
                                track.get("artist"),
                                payload["title"],
                                payload["failures"][song_key(track)],
                            )
                        else:
                            payload["blocked"] = (
                                downloader.error
                                or "Preparation deferred: downloads paused or cache space reserved"
                            )
                        if time.monotonic() >= deadline:
                            await asyncio.to_thread(self.update, identity, payload)
                            return
                        payload["cursor"] = position + 1
                        await asyncio.to_thread(self.update, identity, payload)
                        continue
                payload.get("failures", {}).pop(song_key(track), None)
                payload["cursor"] = position + 1
                await asyncio.to_thread(self.update, identity, payload)
            # Successful audio is durable; rebuild readiness after a resumed pass.
            prepared = []
            for track in payload["tracks"]:
                try:
                    await asyncio.to_thread(self.cache.available, track)
                    prepared.append(track)
                except FileNotFoundError:
                    pass
            payload["cursor"] = 0
            if deferred:
                payload["retry_at"] = time.time() + 60
                await asyncio.to_thread(self.update, identity, payload)
                continue
            if (
                len(prepared) == len(payload["tracks"])
                or payload.get("attempts", 0) >= 2
            ):
                need = payload.get("minimum", 0)
                if len(prepared) < need:
                    prepared += await asyncio.to_thread(
                        self.fill, payload, prepared, hidden, need
                    )
                if len(prepared) < need:
                    await asyncio.to_thread(self.retire_short, identity, payload)
                elif prepared:
                    await asyncio.to_thread(self.publish, identity, prepared, today)
                else:
                    payload["retry_at"] = time.time() + 3600
                    await asyncio.to_thread(self.update, identity, payload)
            else:
                payload.update(
                    attempts=payload.get("attempts", 0) + 1, retry_at=time.time() + 300
                )
                await asyncio.to_thread(self.update, identity, payload)

    def fill(self, payload, prepared, hidden, need):
        """Owned backfill songs with audio, in order, up to `need` in all."""
        have = {song_key(t) for t in prepared}
        added = []
        for track in payload.get("backfill", []):
            if len(prepared) + len(added) >= need:
                break
            key = song_key(track)
            if key in have or key in hidden:
                continue
            try:
                self.cache.available(track)
            except FileNotFoundError:
                continue
            have.add(key)
            added.append(track)
        return added

    def retire_short(self, identity, payload):
        """A mix that cannot reach MIN_MIX is never shown."""
        payload.update(state="short", cursor=0)
        with self.store.connect() as conn:
            conn.execute(
                "UPDATE mix_editions SET payload=?,retired_at=? WHERE id=?",
                (json.dumps(payload), time.time(), identity),
            )
        log.info(
            "Mixes: retired %s: fewer than %d songs ready", payload["title"], MIN_MIX
        )

    def pending(self, user, day):
        with self.store.connect() as conn:
            return conn.execute(
                "SELECT id,payload,kind FROM mix_editions WHERE user_id=? AND day=? AND (retired_at IS NULL OR lease_until>?) ORDER BY kind LIKE 'saved:%' DESC,COALESCE(json_extract(payload,'$.worked_at'),0),id",
                (user, str(day), time.time()),
            ).fetchall()

    def blocked(self):
        """Why automatic preparation must wait, or None when it may download."""
        # Explicit library requests always get first use of sources.
        with self.store.connect() as conn:
            busy = conn.execute(
                "SELECT 1 FROM queue WHERE status IN ('queued','searching','downloading','validating') LIMIT 1"
            ).fetchone()
        if busy:
            return "Explicit library downloads take priority"
        if not self.store.download_settings()["enabled"]:
            return "Downloads are paused"
        if not self.cache.room():
            return "Cache budget or disk reserve reached"
        return None

    def update(self, identity, payload):
        with self.store.connect() as conn:
            conn.execute(
                "UPDATE mix_editions SET payload=? WHERE id=?",
                (json.dumps(payload), identity),
            )

    def entry(self, user, track_id, playback=False):
        try:
            prefix, identity, position = track_id.split(":")
            if prefix != "mix":
                raise ValueError()
            with self.store.connect() as conn:
                row = conn.execute(
                    "SELECT payload,active,retired_at,lease_until,kind FROM mix_editions WHERE id=? AND user_id=?",
                    (identity, user),
                ).fetchone()
            if not row:
                raise ValueError()
            payload = json.loads(row[0])
            # Old queues get a bounded lease across a rotation; old metadata alone
            # never grants access to another person's files or an arbitrary path.
            # Today's radio is never "active" on Home but is always playable.
            live_radio = row[4] == "radio" and row[2] is None
            if playback and (
                payload["state"] != "ready"
                or (
                    not row[1]
                    and not live_radio
                    and (row[2] is None or row[2] < time.time() - 6 * 3600)
                    and row[3] < time.time()
                )
            ):
                raise ValueError()
            index = int(position)
            if index < 0:
                raise ValueError()
            return payload["tracks"][index]
        except (ValueError, IndexError, TypeError):
            raise LookupError("Mix song not found") from None

    def playback(self, user, track_id):
        track = self.entry(user, track_id, playback=True)
        identity = track_id.split(":")[1]
        with self.store.mix_lock, self.store.connect() as conn:
            until = time.time() + 6 * 3600
            conn.execute(
                "UPDATE mix_editions SET lease_until=? WHERE id=?", (until, identity)
            )
            payload = json.loads(
                conn.execute(
                    "SELECT payload FROM mix_editions WHERE id=?", (identity,)
                ).fetchone()[0]
            )
            for item in payload["tracks"]:
                conn.execute(
                    "UPDATE mix_audio SET lease_until=MAX(lease_until,?) WHERE key=?",
                    (until, song_key(item)),
                )
            return self.cache.available(track)

    def stand_in(self, user, track_id):
        """Where a song from this person's edition plays once the edition no
        longer does (rotated overnight, its play lease over: a queue paused
        last night): the household's own file, else the same song, on any
        edition of any album, in one of this person's editions that still
        plays (today's mixes, a leased one, today's radio). None when neither
        has it."""
        track = self.entry(user, track_id)
        match = self.store.index.match(track).track
        if match:
            try:
                track_file(self.store, match["id"])
                return match["id"]
            except FileNotFoundError:
                pass
        with self.store.connect() as conn:
            rows = conn.execute(
                "SELECT id,payload FROM mix_editions WHERE user_id=? AND id!=? AND (active=1 OR lease_until>? OR (kind='radio' AND retired_at IS NULL)) ORDER BY active DESC,id",
                (user, track_id.split(":")[1], time.time()),
            ).fetchall()
        for identity, raw in rows:
            for index, item in enumerate(json.loads(raw).get("tracks") or []):
                if not compatible(item, track):
                    continue
                candidate = f"mix:{identity}:{index}"
                try:
                    self.playback(user, candidate)
                    return candidate
                except (FileNotFoundError, LookupError):
                    pass
        return None

    def reference(self, user, track):
        """A saved playlist can reuse audio from this person's rotating edition."""
        key = song_key(track)
        with self.store.connect() as conn:
            rows = conn.execute(
                "SELECT id,payload FROM mix_editions WHERE user_id=? AND (active=1 OR lease_until>?)",
                (user, time.time()),
            ).fetchall()
        for identity, raw in rows:
            for index, item in enumerate(json.loads(raw)["tracks"]):
                if song_key(item) == key:
                    return f"mix:{identity}:{index}"
        return None

    def list(self, user):
        with self.store.connect() as conn:
            members = {
                r[0]
                for r in conn.execute(
                    "SELECT track_id FROM library_members WHERE user_id=?", (user,)
                )
            }
            rows = conn.execute(
                "SELECT id,kind,day,payload FROM mix_editions WHERE user_id=? AND active=1 ORDER BY "
                + KIND_ORDER,
                (user,),
            ).fetchall()
            preparing = bool(
                conn.execute(
                    "SELECT 1 FROM mix_editions WHERE user_id=? AND day=? AND active=0 AND retired_at IS NULL AND json_extract(payload,'$.state')='preparing' LIMIT 1",
                    (user, str(self.day())),
                ).fetchone()
            )
        hidden = self.hidden(user)
        result = []
        for identity, kind, day, raw in rows:
            if "edition:" + identity in hidden:
                continue
            payload = json.loads(raw)
            if not kind.startswith("saved:") and len(payload["tracks"]) < MIN_MIX:
                continue  # Short editions from before the minimum stay out of Home.
            tracks = [
                dto
                for index, track in enumerate(payload["tracks"])
                if song_key(track) not in hidden
                and (dto := self.track_dto(identity, index, track, members))
            ]
            if tracks:
                result.append(
                    {
                        "id": identity,
                        "kind": kind,
                        "day": day,
                        "title": payload["title"],
                        "reason": payload["reason"],
                        "tracks": tracks,
                    }
                )
        radio = self.radio_row(user, self.day())
        card = None
        if radio and json.loads(radio[1])["tracks"]:
            payload = json.loads(radio[1])
            card = {
                "title": payload["title"],
                "ready": max(0, len(payload["tracks"]) - 1 - payload.get("served", -1)),
            }
        return {"mixes": result, "preparing": preparing, "radio": card}

    def track_dto(self, identity, index, track, members):
        """A playable mix entry, or None while its audio is unavailable."""
        try:
            self.cache.available(track)
        except FileNotFoundError:
            return None
        library_id = None
        match = self.store.index.match(track).track
        if match:
            try:
                track_file(self.store, match["id"])
                library_id = match["id"]
            except FileNotFoundError:
                pass
        return TrackDTO(
            **{
                **track,
                "id": f"mix:{identity}:{index}",
                "server_state": (
                    "in_library"
                    if library_id in members
                    else ("household_available" if library_id else "not_in_library")
                ),
                "library_track_id": library_id,
                "available": True,
                "cover_url": (
                    f"/api/v1/catalogue/artwork/release/{track['musicbrainz_release_id']}"
                    if track.get("musicbrainz_release_id")
                    else versioned(track.get("cover_url"))
                ),
            }
        )

    # Radio: one append-only edition per person per day. Entries keep the
    # ordinary mix:{edition}:{index} identity, so playback, signed URLs,
    # listens and cache retention need no radio-specific path.
    def radio_row(self, user, day):
        with self.store.connect() as conn:
            return conn.execute(
                "SELECT id,payload FROM mix_editions WHERE user_id=? AND kind='radio' AND day=?",
                (user, str(day)),
            ).fetchone()

    def radio_edition(self, user, day):
        row = self.radio_row(user, day)
        if row:
            return row[0]
        with self.store.connect() as conn:
            name = conn.execute(
                "SELECT display_name FROM users WHERE id=?", (user,)
            ).fetchone()[0]
        identity = self.stage(
            user,
            "radio",
            day,
            f"{name}'s radio",
            "Your library and new discoveries, without end",
            [],
        )
        # Ready from the start: entries become playable as they are appended.
        self.radio_update(
            identity, lambda p: p.update(state="ready", served=-1, queue=[])
        )
        return identity

    def radio_update(self, identity, change):
        """Read-modify-write so the worker and listeners never lose updates."""
        with self.radio_lock, self.store.connect() as conn:
            payload = json.loads(
                conn.execute(
                    "SELECT payload FROM mix_editions WHERE id=?", (identity,)
                ).fetchone()[0]
            )
            change(payload)
            conn.execute(
                "UPDATE mix_editions SET payload=? WHERE id=?",
                (json.dumps(payload), identity),
            )
            return payload

    def radio_weights(self, user, tracks):
        """Favourites lift a song; a recent skip lowers and a like lifts its artist."""
        now = time.time()
        with self.store.connect() as conn:
            rows = conn.execute(
                "SELECT artist,action,MAX(at) FROM radio_feedback WHERE user_id=? AND at>? GROUP BY artist,action",
                (user, now - 30 * 86400),
            ).fetchall()
        factor = {}
        for artist, action, at in rows:
            key = artist.casefold()
            if action == "skip" and at > now - 7 * 86400:
                factor[key] = factor.get(key, 1) * 0.3
            elif action == "like":
                factor[key] = factor.get(key, 1) * 2
        favourites = self.favourite_weights(user)
        return {
            song_key(t): favourites.get(song_key(t), 1)
            * factor.get(t["artist"].casefold(), 1)
            for t in tracks
        }

    def radio_pool(self, user, day):
        """Discoveries already chosen for today's mixes; radio adds no lookups."""
        with self.store.connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM mix_editions WHERE user_id=? AND day=? AND kind!='radio' AND kind NOT LIKE 'saved:%'",
                (user, str(day)),
            ).fetchall()
        pool = {}
        for (raw,) in rows:
            payload = json.loads(raw)
            for track in payload.get("planned") or payload["tracks"]:
                if track.get("bucket") in DISCOVERY | {"release"}:
                    pool.setdefault(song_key(track), track)
        return list(pool.values())

    def radio_batch(self, user, day, payload):
        projection = self.store.user_libraries.projection(user)
        hidden = self.hidden(user)
        used = {song_key(t) for t in payload["tracks"] + payload.get("queue", [])}
        owned = [
            t
            for t in projection["tracks"]
            if t.get("available", True)
            and song_key(t) not in hidden
            and song_key(t) not in used
        ]
        owned_keys = {
            t.get("musicbrainz_recording_id") or song_key(t)
            for t in projection["tracks"]
        }
        discoveries = [
            t
            for t in self.radio_pool(user, day)
            if song_key(t) not in hidden
            and song_key(t) not in used
            and (t.get("musicbrainz_recording_id") or song_key(t)) not in owned_keys
        ]
        history = self.history(user, owned)
        weights = self.radio_weights(user, owned + discoveries)
        salt = f"{user}:{day}:radio:{len(payload['tracks'])}"
        half = RADIO_BATCH // 2
        new = choose(discoveries, half, salt, history, weights)
        mine = choose(owned, RADIO_BATCH - len(new), salt, history, weights)
        return choose(mine + new, RADIO_BATCH, salt, history, weights)

    async def prepare_radio(self, user, today):
        """Keep RADIO_BUFFER ready songs ahead of the listener's position."""
        deadline = time.monotonic() + PASS_SECONDS
        identity = await asyncio.to_thread(self.radio_edition, user, today)
        payload = await asyncio.to_thread(self.radio_update, identity, lambda p: None)
        ahead = len(payload["tracks"]) - 1 - payload.get("served", -1)
        if ahead >= RADIO_BUFFER:
            return
        if not payload.get("queue"):
            batch = await asyncio.to_thread(self.radio_batch, user, today, payload)
            if not batch:
                return
            payload = await asyncio.to_thread(
                self.radio_update,
                identity,
                lambda p: p.update(queue=batch, planned=p.get("planned", []) + batch),
            )
        added = 0
        for track in list(payload["queue"]):
            if self.stopping.is_set() or time.monotonic() >= deadline:
                break
            error = None
            try:
                await asyncio.to_thread(self.cache.available, track)
                ready = True
            except FileNotFoundError:
                reason = await asyncio.to_thread(self.blocked)
                if reason:
                    await asyncio.to_thread(
                        self.radio_update, identity, lambda p: p.update(blocked=reason)
                    )
                    break
                downloader = await asyncio.to_thread(
                    MixDownloader,
                    self.cache,
                    self.runtime,
                    lambda: self.stopping.is_set() or time.monotonic() >= deadline,
                )
                ready = await asyncio.to_thread(downloader.fetch, track)
                if not ready and downloader.result != "failed":
                    break  # Deferred: keep the song queued for the next pass.
                error = downloader.error or "No matching recording available"

            def advance(p, track=track, ready=ready):
                p["queue"] = [t for t in p["queue"] if song_key(t) != song_key(track)]
                p.pop("blocked", None)
                if ready:
                    p["tracks"].append(track)
                else:
                    p.setdefault("failures", {})[song_key(track)] = error

            await asyncio.to_thread(self.radio_update, identity, advance)
            added += ready
        if added:
            log.info("Mixes: radio for %s gained %d songs", user, added)

    def radio_next(self, user, after=None):
        """The next playable radio songs after a listener's last entry."""
        identity = self.radio_edition(user, self.day())
        start = 0
        if after:
            try:
                prefix, edition, position = after.split(":")
                if prefix == "mix" and edition == identity:
                    start = int(position) + 1
            except ValueError:
                pass
        with self.store.connect() as conn:
            members = {
                r[0]
                for r in conn.execute(
                    "SELECT track_id FROM library_members WHERE user_id=?", (user,)
                )
            }
        hidden = self.hidden(user)
        payload = self.radio_update(identity, lambda p: None)
        tracks, last = [], start - 1
        for index in range(max(0, start), len(payload["tracks"])):
            if len(tracks) >= RADIO_PAGE:
                break
            track = payload["tracks"][index]
            last = index
            if song_key(track) in hidden:
                continue
            dto = self.track_dto(identity, index, track, members)
            if dto:
                tracks.append(dto)
        payload = self.radio_update(
            identity, lambda p: p.update(served=max(p.get("served", -1), last))
        )
        return {
            "id": identity,
            "title": payload["title"],
            "tracks": tracks,
            "preparing": len(payload["tracks"]) - 1 - payload["served"] < RADIO_BUFFER,
        }

    def radio_feedback(self, user, track_id, action):
        track = self.entry(user, track_id)
        with self.store.connect() as conn:
            conn.execute(
                "INSERT INTO radio_feedback VALUES (?,?,?,?,?)",
                (user, song_key(track), track["artist"], action, int(time.time())),
            )

    def inspect(self, user):
        """Owner diagnostics: every live edition with each intended song's state."""
        with self.store.connect() as conn:
            person = conn.execute(
                "SELECT display_name FROM users WHERE id=?", (user,)
            ).fetchone()
            if not person:
                raise LookupError("Person not found")
            rows = conn.execute(
                "SELECT id,kind,day,active,payload FROM mix_editions WHERE user_id=? AND (retired_at IS NULL OR active=1) ORDER BY day,"
                + KIND_ORDER,
                (user,),
            ).fetchall()
        hidden = self.hidden(user)
        now = time.time()
        editions = []
        for identity, kind, day, active, raw in rows:
            payload = json.loads(raw)
            published = {song_key(t) for t in payload["tracks"]}
            failures = payload.get("failures", {})
            preparing = payload["state"] != "ready"
            cursor = payload.get("cursor", 0)
            songs = []
            for position, track in enumerate(
                payload.get("planned") or payload["tracks"]
            ):
                key = song_key(track)
                status, detail = "waiting", None
                match = self.store.index.match(track).track
                try:
                    if not match:
                        raise FileNotFoundError()
                    track_file(self.store, match["id"])
                    status = "library"
                except (FileNotFoundError, ValueError, OSError):
                    try:
                        self.cache.file(key)
                        status = "cached"
                    except FileNotFoundError:
                        pass
                if key in hidden:
                    status = "hidden"
                elif status == "waiting" and key in failures:
                    status, detail = "failed", failures[key]
                elif status == "waiting" and not preparing and key not in published:
                    status = "dropped"
                elif status == "waiting" and preparing and position == cursor:
                    status = "next"
                songs.append(
                    {
                        "title": track.get("title"),
                        "artist": track.get("artist"),
                        "album": track.get("album"),
                        "reason": track.get("reason"),
                        "status": status,
                        "detail": detail,
                    }
                )
            retry = payload.get("retry_at", 0)
            editions.append(
                {
                    "id": identity,
                    "kind": kind,
                    "day": day,
                    "title": payload["title"],
                    "reason": payload.get("reason"),
                    "state": payload["state"],
                    "active": bool(active),
                    "attempts": payload.get("attempts", 0),
                    "retry_at": retry if preparing and retry > now else None,
                    "worked_at": payload.get("worked_at"),
                    "blocked": payload.get("blocked") if preparing else None,
                    "published": len(payload["tracks"]) if not preparing else None,
                    "songs": songs,
                }
            )
        return {"person": person[0], "editions": editions}

    def hide_edition(self, user, identity):
        with self.store.lifecycle_lock, self.store.connect() as conn:
            if not conn.execute(
                "SELECT 1 FROM mix_editions WHERE id=? AND user_id=?", (identity, user)
            ).fetchone():
                raise LookupError("Mix not found")
            conn.execute(
                "INSERT OR IGNORE INTO mix_feedback VALUES (?,?)",
                (user, "edition:" + identity),
            )
            conn.execute(
                "UPDATE mix_editions SET active=0,retired_at=COALESCE(retired_at,?) WHERE id=?",
                (time.time(), identity),
            )

    def queue_saved(self, user, requested):
        """Explicit playback of saved mix metadata grants temporary preparation.

        The canonical song must still belong to this person's saved edition;
        arbitrary catalogue requests never gain a cache-download entitlement.
        """
        wanted = song_key(requested)
        with self.store.connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM playlists WHERE owner_id=? AND json_extract(payload,'$.source')='mix'",
                (user,),
            ).fetchall()
        song = next(
            (
                t
                for (raw,) in rows
                for t in json.loads(raw)["tracks"]
                if song_key(t) == wanted
            ),
            None,
        )
        if song is None:
            return None
        identity = self.stage(
            user,
            "saved:" + wanted,
            self.day(),
            "Saved mix playback",
            "Prepared from your saved edition",
            [song],
        )
        with self.store.lifecycle_lock, self.store.connect() as conn:
            payload = json.loads(
                conn.execute(
                    "SELECT payload FROM mix_editions WHERE id=?", (identity,)
                ).fetchone()[0]
            )
            try:
                self.cache.available(song)
                payload["state"] = "ready"
            except FileNotFoundError:
                payload["state"] = "preparing"
            conn.execute(
                "UPDATE mix_editions SET active=0,retired_at=COALESCE(retired_at,?),lease_until=?,payload=? WHERE id=?",
                (time.time(), time.time() + 6 * 3600, json.dumps(payload), identity),
            )
        return f"mix:{identity}:0"

    async def restore(self, user, track):
        identity = await asyncio.to_thread(self.queue_saved, user, track)
        if not identity:
            return None
        song = self.entry(user, identity)
        try:
            await asyncio.to_thread(self.cache.available, song)
        except FileNotFoundError:
            deadline = time.monotonic() + 180
            worker = await asyncio.to_thread(
                MixDownloader,
                self.cache,
                self.runtime,
                lambda: self.stopping.is_set() or time.monotonic() >= deadline,
            )
            if not await asyncio.to_thread(worker.fetch, song):
                await asyncio.to_thread(
                    self.saved_failure, identity, song, worker.result, worker.error
                )
                raise ValueError(
                    "This saved mix song is waiting for a download. Check that downloads are enabled and cache space is available, then try again."
                )
        await asyncio.to_thread(
            self.publish, identity.split(":")[1], [song], self.day()
        )
        return identity

    def saved_failure(self, identity, song, result, error):
        edition = identity.split(":")[1]
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT payload FROM mix_editions WHERE id=?", (edition,)
            ).fetchone()
        if not row:
            return
        payload = json.loads(row[0])
        if result == "failed":
            payload.setdefault("failures", {})[song_key(song)] = (
                error or "No matching full recording available"
            )
            payload["attempts"] = payload.get("attempts", 0) + 1
        else:
            payload["blocked"] = (
                error
                or "Preparation deferred: downloads paused or cache space reserved"
            )
        payload["retry_at"] = time.time() + (300 if result == "failed" else 60)
        self.update(edition, payload)

    def hide(self, user, track_id):
        track = self.entry(user, track_id)
        with self.store.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO mix_feedback VALUES (?,?)",
                (user, song_key(track)),
            )

    def save(self, user, identity):
        with self.store.lifecycle_lock, self.store.connect() as conn:
            row = conn.execute(
                "SELECT payload,day FROM mix_editions WHERE id=? AND user_id=?",
                (identity, user),
            ).fetchone()
            if not row or json.loads(row[0])["state"] != "ready":
                raise LookupError("Mix not found")
            payload = json.loads(row[0])
            hidden = {
                r[0]
                for r in conn.execute(
                    "SELECT key FROM mix_feedback WHERE user_id=?", (user,)
                )
            }
            playlist_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "saved-mix:" + identity))
            tracks = [
                {
                    k: v
                    for k, v in t.items()
                    if k not in {"id", "reason", "bucket", "relative_path"}
                }
                for t in payload["tracks"]
                if song_key(t) not in hidden
            ]
            conn.execute(
                "INSERT OR IGNORE INTO playlists(id,payload,owner_id) VALUES (?,?,?)",
                (
                    playlist_id,
                    json.dumps(
                        {
                            "id": playlist_id,
                            "name": payload["title"] + " · " + row[1],
                            "tracks": tracks,
                            "revision": 0,
                            "source": "mix",
                            "imported_at": datetime.now().isoformat(),
                        }
                    ),
                    user,
                ),
            )
            from .user_library import bump

            bump(conn, user)
        return Playlists(self.store, user).detail(playlist_id)
