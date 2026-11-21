"""Bounded portrait enrichment with album artwork as the existing fallback."""

import asyncio
from contextlib import nullcontext
from urllib.parse import urlsplit

import httpx
from PIL import Image

from .keyed_locks import KeyedLocks
from .artwork import quoted
from .deezer_art import deezer_image
from .library import identifier, normalise
from .media import settings
from .fanart import Fanart, key_tag

# The generation of saved portraits. v3 (#168): Deezer's "no picture"
# silhouette had been saved as some artists' photo, and a saved image did not
# record its source, so every portrait is fetched again when next asked for.
# Files of an earlier generation are left where they are and no longer read.
PORTRAIT_KEY = "portrait-v3"


class Portraits:
    def __init__(self, store, catalogue, artwork, fanart=None):
        self.store, self.catalogue, self.artwork = store, catalogue, artwork
        self.fanart = fanart or Fanart(store)
        self.locks = KeyedLocks()
        self.slots = asyncio.Semaphore(3)

    async def get(self, client, name, artist_id=None):
        config = settings(self.store)
        if not config["portraits"]:
            return None
        target = portrait_target(self.store, name, artist_id)
        key = target.stem
        if target.exists():
            return target
        # ":deezer" (#194): misses remembered before Deezer was asked retry.
        missing = (
            "portrait-missing:"
            + key
            + ":deezer"
            + (":fanart" if config.get("fanart_key") else "")
        )
        if await self.artwork.waiting(missing):
            return None
        async with self.locks.hold(key), self.slots:
            if target.exists():
                return target
            if await self.artwork.waiting(missing):
                return None
            failures = (
                httpx.HTTPError,
                ValueError,
                OSError,
                KeyError,
                TypeError,
                Image.DecompressionBombError,
            )
            unsure = False
            try:
                url = await self.artwork.deezer.photo(client, name, artist_id)
                if url:
                    return await self.keep(client, deezer_image(url), target, "deezer")
            except failures:
                # Deezer may have one: ask again soon, after the other sources.
                unsure = True
            url = None
            if config.get("fanart_key"):
                try:
                    if not artist_id:
                        data = await self.catalogue.get(
                            client, "artist/", query="artist:" + quoted(name), limit=100
                        )
                        matches = [
                            a
                            for a in data.get("artists", [])
                            if normalise(a.get("name", "")) == normalise(name)
                        ]
                        if data.get("count", len(matches)) <= 100 and len(matches) == 1:
                            artist_id = matches[0]["id"]
                    if artist_id:
                        data = await self.fanart.get(
                            client, artist_id, config["fanart_key"]
                        )
                        photos = data.get("artistthumb", [])
                        photos.sort(key=lambda p: int(p.get("likes", 0)), reverse=True)
                        url = photos[0]["url"] if photos else None
                except (httpx.HTTPError, ValueError, KeyError):
                    pass
            if url:
                try:
                    return await self.keep(client, url, target, "fanart")
                except failures:
                    await self.artwork.missing(missing, 300)
                    return None
            await self.artwork.missing(missing, 300 if unsure else 86400)
            return None

    async def keep(self, client, url, target, source):
        """Download a trusted portrait address and save it; raises on failure."""
        parts = urlsplit(url)
        if source != "deezer" and (
            parts.scheme != "https"
            or parts.hostname != "assets.fanart.tv"
            or parts.username
            or parts.password
            or parts.port not in {None, 443}
        ):
            raise ValueError("Untrusted portrait address")
        async with self.fanart.slots if source == "fanart" else nullcontext():
            async with client.stream(
                "GET", url, timeout=12, follow_redirects=False
            ) as response:
                response.raise_for_status()
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > 5_000_000:
                        raise ValueError("Portrait too large")
        await asyncio.to_thread(self.artwork.save, bytes(content), target)
        self.artwork.counted("photo", source)
        await self.artwork.sourced(target, source, url)
        return target


def portrait_target(store, name, artist_id=None):
    config = settings(store)
    provider = key_tag(config.get("fanart_key", ""))
    key = identifier(PORTRAIT_KEY, (artist_id or normalise(name)) + provider)
    return store.cover_dir / (key + ".jpg")
