"""Apple's music search, for songs and albums not on the server (D-61).

Apple orders results by what people play, so the first ones are the ones a
search usually means. Its free search allows about 20 requests a minute, so
answers are kept for a while and a search past the budget answers "busy"
instead of waiting. Devices never reach Apple: covers come through the
server. Opening or requesting a result matches it to MusicBrainz first.
"""

import asyncio
import time
from collections import OrderedDict, deque
from urllib.parse import urlsplit

import httpx

from .library import normalise

SEARCH_URL = "https://itunes.apple.com/search"
LOOKUP_URL = "https://itunes.apple.com/lookup"
HEADERS = {"User-Agent": "PigMusic/1.0 (https://github.com/maroge1990/Pig-Music)"}
# Apple allows about 20 a minute; each search asks twice (songs, albums).
CALLS_PER_MINUTE = 18
CACHE_SECONDS = 10 * 60
CACHE_SIZE = 500
RESULTS = 10
EMPTY = {"songs": [], "albums": []}


def apple_artwork(value):
    """Apple's cover address at 600 px, only from Apple's image hosts."""
    url = urlsplit(value or "")
    host = url.hostname or ""
    if (
        url.scheme != "https"
        or url.username
        or url.password
        or url.port not in {None, 443}
        or not host.endswith(".mzstatic.com")
    ):
        raise ValueError("Untrusted artwork address")
    return value.replace("100x100bb", "600x600bb")


def text(value, limit=300):
    return value.strip()[:limit] if isinstance(value, str) and value.strip() else None


def number(value):
    return value if isinstance(value, int) and 0 < value < 10**15 else None


def song(entry):
    if entry.get("wrapperType") != "track" or entry.get("kind") != "song":
        return None
    apple_id, title = number(entry.get("trackId")), text(entry.get("trackName"))
    artist = text(entry.get("artistName"))
    if not (apple_id and title and artist):
        return None
    millis = entry.get("trackTimeMillis")
    return {
        "apple_id": apple_id,
        "title": title,
        "artist": artist,
        "album": text(entry.get("collectionName")) or "",
        "album_apple_id": number(entry.get("collectionId")),
        "duration": millis / 1000 if isinstance(millis, int) and millis > 0 else None,
        "explicit": entry.get("trackExplicitness") == "explicit",
        "year": (text(entry.get("releaseDate")) or "")[:4],
    }


def album(entry):
    if entry.get("wrapperType") != "collection":
        return None
    apple_id, title = number(entry.get("collectionId")), text(
        entry.get("collectionName")
    )
    artist = text(entry.get("artistName"))
    if not (apple_id and title and artist):
        return None
    count = entry.get("trackCount")
    return {
        "apple_id": apple_id,
        "title": title,
        "artist": artist,
        "track_count": count if isinstance(count, int) and count > 0 else None,
        "explicit": entry.get("collectionExplicitness") == "explicit",
        "year": (text(entry.get("releaseDate")) or "")[:4],
    }


class AppleSearch:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.cache = OrderedDict()  # (region, query) -> (expiry, value)
        self.calls = deque()
        self.inflight = {}
        # Cover addresses seen in answers, so a cover needs no extra lookup.
        self.covers = OrderedDict()
        # (kind, Apple id) -> the song or album as Apple described it. Resolve
        # trusts only these, never a device's title and artist (Codex, high):
        # a match is cached for everyone, Home's chart included.
        self.items = OrderedDict()

    def allow(self, count):
        now = self.clock()
        while self.calls and self.calls[0] <= now - 60:
            self.calls.popleft()
        if len(self.calls) + count > CALLS_PER_MINUTE:
            return False
        self.calls.extend([now] * count)
        return True

    async def search(self, client, query, region, known=frozenset()):
        """Songs and albums in Apple's order, minus music already on the
        server (`known`: normalised (title, artist) pairs)."""
        words = " ".join(query.split())[:100]
        key = (region, normalise(words))
        if len(key[1]) < 2:
            return {**EMPTY, "state": "ready"}
        cached = self.cache.get(key)
        if cached and cached[0] > self.clock():
            self.cache.move_to_end(key)
            return without(cached[1], known)
        task = self.inflight.get(key)
        if task is None:
            if not self.allow(2):
                return {**EMPTY, "state": "busy"}
            task = asyncio.ensure_future(self.fetch(client, words, region))
            self.inflight[key] = task
            task.add_done_callback(lambda _: self.inflight.pop(key, None))
        try:
            value = await asyncio.shield(task)
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return {**EMPTY, "state": "unavailable"}
        self.cache[key] = (self.clock() + CACHE_SECONDS, value)
        while len(self.cache) > CACHE_SIZE:
            self.cache.popitem(last=False)
        return without(value, known)

    async def fetch(self, client, words, region):
        songs, albums = await asyncio.gather(
            self.ask(client, words, region, "song"),
            self.ask(client, words, region, "album"),
        )
        for entry in songs + albums:
            self.remember_cover(entry)
        value = {
            "songs": [s for s in map(song, songs) if s],
            "albums": [a for a in map(album, albums) if a],
            "state": "ready",
        }
        for kind in ("song", "album"):
            for item in value[kind + "s"]:
                self.remember(kind, item)
        return value

    def remember(self, kind, item):
        key = (kind, item["apple_id"])
        self.items[key] = item
        self.items.move_to_end(key)
        while len(self.items) > 5000:
            self.items.popitem(last=False)

    async def item(self, client, kind, apple_id):
        """Apple's own description of a song or album: from a recent answer,
        else asked of Apple by its id. None when Apple doesn't know it."""
        if (kind, apple_id) in self.items:
            return self.items[(kind, apple_id)]
        entry = next(
            (
                e
                for e in await self.lookup(client, apple_id)
                if (e.get("trackId") if kind == "song" else e.get("collectionId"))
                == apple_id
            ),
            None,
        )
        found = (song if kind == "song" else album)(entry) if entry else None
        if found:
            self.remember(kind, found)
        return found

    async def lookup(self, client, apple_id):
        if not self.allow(1):
            raise ValueError("Apple search budget used")
        response = await client.get(
            LOOKUP_URL,
            params={"id": apple_id},
            headers=HEADERS,
            timeout=6,
            follow_redirects=False,
        )
        response.raise_for_status()
        if len(response.content) > 1_000_000:
            raise ValueError("Answer too large")
        results = response.json()["results"]
        if not isinstance(results, list):
            raise ValueError("Invalid answer")
        found = [r for r in results if isinstance(r, dict)]
        for entry in found:
            self.remember_cover(entry)
        return found

    async def ask(self, client, words, region, entity):
        response = await client.get(
            SEARCH_URL,
            params={
                "term": words,
                "country": region,
                "media": "music",
                "entity": entity,
                "limit": RESULTS,
            },
            headers=HEADERS,
            timeout=6,
            follow_redirects=False,
        )
        response.raise_for_status()
        if len(response.content) > 1_000_000:
            raise ValueError("Answer too large")
        results = response.json()["results"]
        if not isinstance(results, list):
            raise ValueError("Invalid answer")
        return [r for r in results if isinstance(r, dict)]

    def remember_cover(self, entry):
        collection = number(entry.get("collectionId"))
        try:
            url = apple_artwork(entry.get("artworkUrl100"))
        except ValueError:
            return
        if collection:
            self.covers[collection] = url
            self.covers.move_to_end(collection)
            while len(self.covers) > 5000:
                self.covers.popitem(last=False)

    async def cover_url(self, client, collection_id):
        """The 600 px cover address for an Apple album."""
        if collection_id in self.covers:
            return self.covers[collection_id]
        await self.lookup(client, collection_id)
        if collection_id not in self.covers:
            raise ValueError("No such album")
        return self.covers[collection_id]


def without(value, known):
    """Drop what the server already has: search lists it with its mark."""

    def new(item):
        return (normalise(item["title"]), normalise(item["artist"])) not in known

    return {
        "songs": [s for s in value["songs"] if new(s)],
        "albums": [a for a in value["albums"] if new(a)],
        "state": value["state"],
    }
