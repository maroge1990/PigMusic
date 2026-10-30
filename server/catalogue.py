"""Cached MusicBrainz browsing; queue payloads are built from server metadata."""

import asyncio
from contextlib import asynccontextmanager
from urllib.parse import parse_qsl, urljoin, urlsplit
from contextvars import ContextVar
import time
from uuid import UUID

from .library import normalise


def mbid(value):
    return str(UUID(value))


def credit(items):
    return (
        "".join(
            item.get("name", item.get("artist", {}).get("name", ""))
            + item.get("joinphrase", "")
            for item in items
            if isinstance(item, dict)
        )
        or "Unknown artist"
    )


# One include set for every release read, so they share one cached answer.
RELEASE_INC = "recordings+artist-credits+isrcs+release-groups"


# Release-group types that make its songs other recordings (`recording_kind`).
RELEASE_KINDS = {"Live": "live", "Remix": "remix", "DJ-mix": "remix", "Demo": "demo"}


def group_kinds(group):
    """Recording kinds a release group's secondary types name (a Live album)."""
    return frozenset(
        RELEASE_KINDS[t]
        for t in (group or {}).get("secondary-types") or []
        if t in RELEASE_KINDS
    )


def cached_release_kind(store, release_id):
    """The recording kinds of a release this server has already read (its
    catalogue page), or None when it has not: requests from clients that do
    not send `recording_kind` are reconstructed from it."""
    key = "catalogue:release/" + str(release_id) + repr([("inc", RELEASE_INC)])
    cached = store.read_document(key)
    if not cached or "release-group" not in (cached.get("data") or {}):
        return None
    return sorted(group_kinds(cached["data"]["release-group"]))


def release_tracks(release):
    album_artist = credit(release.get("artist-credit", []))
    kinds = sorted(group_kinds(release.get("release-group")))
    year = (
        int(release.get("date", "")[:4]) if release.get("date", "")[:4].isdigit() else 0
    )
    tracks = []
    for medium in release.get("media", []):
        for track in medium.get("tracks", []):
            recording = track.get("recording", {})
            tracks.append(
                {
                    "id": track["id"],
                    "title": track.get("title") or recording.get("title", ""),
                    "artist": credit(
                        track.get("artist-credit")
                        or recording.get("artist-credit")
                        or release.get("artist-credit", [])
                    ),
                    "album": release["title"],
                    "album_artist": album_artist,
                    "year": year,
                    "disc_number": medium.get("position", 1),
                    "track_number": track.get("position", 0),
                    "duration": (track.get("length") or recording.get("length") or 0)
                    / 1000,
                    "musicbrainz_release_id": release["id"],
                    "musicbrainz_recording_id": recording.get("id"),
                    "isrcs": recording.get("isrcs", []),
                    "recording_kind": kinds,
                }
            )
    return tracks


background_metadata = ContextVar("background_metadata", default=False)
# Popularity's optional album details yield to ordinary cover/artist warming,
# even when the popularity page itself was requested in the foreground.
deferred_metadata = ContextVar("deferred_metadata", default=False)
# Set by the server warmer: cached answers this close to expiry are renewed
# now, so nobody ever waits on an expired cache.
warming = ContextVar("warming", default=False)
RENEW = 4 * 3600


def still_fresh(age, lifetime, margin=RENEW):
    return age < lifetime - (margin if warming.get() else 0)


async def settled(function, *args):
    """Run a blocking call (a cache write) in a thread. A caller cancelled
    meanwhile still waits for it to finish, so the lock that caller holds
    covers the write and a newer answer cannot be overwritten by it."""
    task = asyncio.ensure_future(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.wait({task})
            except asyncio.CancelledError:
                pass
        raise


class Catalogue:
    # MusicBrainz allows one request per second per client.
    min_interval = 1.05
    # Its 503/429 answers are retried this many times, pausing this long
    # (seconds, growing) unless it says how long in Retry-After.
    retries = 2
    retry_pause = 2.0

    def __init__(self, store):
        self.store = store
        self.lock = asyncio.Lock()
        self.last_request = 0.0
        # Foreground requests, ordinary warming, optional popularity details.
        self.waiters = [0, 0, 0]

    @asynccontextmanager
    async def turn(self, priority):
        self.waiters[priority] += 1
        try:
            while True:
                while any(self.waiters[:priority]):
                    await asyncio.sleep(0.05)
                async with self.lock:
                    # A higher-priority waiter may have arrived while this
                    # task waited for the current provider request to finish.
                    if any(self.waiters[:priority]):
                        continue
                    yield
                    return
        finally:
            self.waiters[priority] -= 1

    async def get(self, client, path, **params):
        key = "catalogue:" + path + repr(sorted(params.items()))
        # SQLite can wait on another writer, so the cache is used off the loop.
        cached = await asyncio.to_thread(self.store.read_document, key)
        if cached and still_fresh(time.time() - cached["time"], 86400):
            return cached["data"]
        priority = (
            2 if deferred_metadata.get() else (1 if background_metadata.get() else 0)
        )
        async with self.turn(priority):
            cached = await asyncio.to_thread(self.store.read_document, key)
            if cached and still_fresh(time.time() - cached["time"], 86400):
                return cached["data"]
            response = await self.fetch(
                client, "https://musicbrainz.org/ws/2/" + path, params
            )
            data = response.json()
            await settled(
                self.store.write_document, key, {"time": time.time(), "data": data}
            )
            return data

    async def cached(self, path, **params):
        """A fresh cached answer, or None; never a request."""
        key = "catalogue:" + path + repr(sorted(params.items()))
        cached = await asyncio.to_thread(self.store.read_document, key)
        if cached and still_fresh(time.time() - cached["time"], 86400):
            return cached["data"]
        return None

    async def pace(self):
        delay = max(0, self.min_interval - (time.monotonic() - self.last_request))
        if delay:
            await asyncio.sleep(delay)
        self.last_request = time.monotonic()

    async def fetch(self, client, url, params):
        """One answer, paced. MusicBrainz's own redirects (a merged release
        group or artist) are followed on musicbrainz.org only, keeping every
        requested parameter the redirect does not set itself (its own `inc`
        or `fmt` wins); a 503 or 429 (its rate limiting) is retried after a
        pause before it fails."""
        query = {"fmt": "json", **params}
        for attempt in range(self.retries + 1):
            await self.pace()
            response = await client.get(url, params=query)
            effective = query
            for _ in range(3):
                if not response.is_redirect:
                    break
                target = urljoin(str(response.url), response.headers["location"])
                parts = urlsplit(target)
                if (
                    parts.scheme != "https"
                    or parts.hostname != "musicbrainz.org"
                    or parts.username
                    or parts.password
                    or parts.port not in {None, 443}
                ):
                    break
                effective = {**effective, **dict(parse_qsl(parts.query))}
                await self.pace()
                response = await client.get(
                    parts._replace(query="", fragment="").geturl(), params=effective
                )
            if response.status_code in {429, 503} and attempt < self.retries:
                try:
                    wait = float(response.headers.get("retry-after", ""))
                except ValueError:
                    wait = self.retry_pause * (attempt + 1)
                await asyncio.sleep(min(max(wait, 0), 10))
                continue
            response.raise_for_status()
            return response

    def presence(self, tracks):
        for track in tracks:
            match = self.store.index.match(track)
            track["status"] = match.status
            track["saved_id"] = match.track["id"] if match.track else None
            track["match_kind"] = match.kind
            track["presence_note"] = (
                "Matched local tags" if match.kind == "probable" else ""
            )

        return tracks


def edition_rank(release):
    text = normalise(
        (release.get("disambiguation") or "") + " " + (release.get("title") or "")
    )
    special = any(
        word in text.split()
        for word in (
            "deluxe",
            "expanded",
            "anniversary",
            "remastered",
            "remaster",
            "bonus",
            "atmos",
            "surround",
            "spatial",
        )
    )
    formats = {m.get("format") or "" for m in release.get("media") or []}
    return (
        release.get("status") != "Official",
        special,
        0 if "Digital Media" in formats else 1 if "CD" in formats else 2,
        not bool(release.get("date")),
        release.get("date") or "9999",
        release.get("id") or "",
    )
