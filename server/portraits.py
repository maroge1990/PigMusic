"""Bounded portrait enrichment with album artwork as the existing fallback."""

import asyncio
import hashlib
import csv
import io
import re
import time
from urllib.parse import urlsplit

import httpx
from PIL import Image

from .artwork import quoted
from .catalogue import settled
from .deezer_art import deezer_image
from .library import identifier, normalise
from .media import settings
from .fanart import Fanart


class Portraits:
    def __init__(self, store, catalogue, artwork, fanart=None):
        self.store, self.catalogue, self.artwork = store, catalogue, artwork
        self.fanart = fanart or Fanart(store)
        self.locks = {}
        self.index_lock = asyncio.Lock()
        self.slots = asyncio.Semaphore(3)

    async def artistgrid(self, client, name):
        async with self.index_lock:
            index = await asyncio.to_thread(
                self.store.read_document, "artistgrid-index"
            )
            if index.get("retry_after", 0) > time.time():
                return None
            if time.time() - index.get("time", 0) > 604800:
                async with client.stream(
                    "GET", "https://artists.artistgrid.cx/artists.csv", timeout=12
                ) as response:
                    response.raise_for_status()
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > 2_000_000:
                            raise ValueError("Portrait index too large")
                names = [
                    r["name"]
                    for r in csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
                ]
                index = {"time": time.time(), "names": names}
                await settled(self.store.write_document, "artistgrid-index", index)
        matches = [n for n in index["names"] if normalise(n) == normalise(name)]
        slug = re.sub("[^a-z0-9]", "", name.lower())
        # Reject punctuation collisions in the upstream filename scheme.
        if (
            len(matches) != 1
            or sum(re.sub("[^a-z0-9]", "", n.lower()) == slug for n in index["names"])
            != 1
        ):
            return None
        return "https://assets.artistgrid.cx/jpg/" + slug + ".jpg"

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
            + (":fanart" if config.get("fanart_key") else ":grid")
        )
        if await self.artwork.waiting(missing):
            return None
        async with self.locks.setdefault(key, asyncio.Lock()), self.slots:
            if target.exists():
                return target
            if await self.artwork.waiting(missing):
                return None
            url, source, unsure = None, None, False
            try:
                url = await self.artwork.deezer.photo(client, name, artist_id)
                source = "deezer" if url else None
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                unsure = True  # Deezer may have one: ask again soon.
            if not url and config.get("fanart_key"):
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
                        source = "fanart" if url else None
                except (httpx.HTTPError, ValueError, KeyError):
                    pass
            try:
                if not url:
                    url = await self.artistgrid(client, name)
                    source = "artistgrid"
                if url and source == "deezer":
                    url = deezer_image(url)
                elif url:
                    parts = urlsplit(url)
                    if (
                        parts.scheme != "https"
                        or parts.hostname
                        not in {"assets.artistgrid.cx", "assets.fanart.tv"}
                        or parts.username
                        or parts.password
                        or parts.port not in {None, 443}
                    ):
                        raise ValueError("Untrusted portrait address")
                if url:
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
                    return target
            except (
                httpx.HTTPError,
                ValueError,
                OSError,
                KeyError,
                Image.DecompressionBombError,
            ):
                if not url:
                    await self.artwork.missing("artistgrid-index", 300)
                await self.artwork.missing(missing, 300)
                return None
            await self.artwork.missing(missing, 300 if unsure else 86400)
            return None


def portrait_target(store, name, artist_id=None):
    config = settings(store)
    provider = hashlib.sha256(config.get("fanart_key", "").encode()).hexdigest()[:12]
    key = identifier("portrait-v2", (artist_id or normalise(name)) + provider)
    return store.cover_dir / (key + ".jpg")
