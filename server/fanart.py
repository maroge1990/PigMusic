"""Shared, key-sensitive Fanart metadata cache and trusted image download."""

import asyncio
import hashlib
import time
from urllib.parse import urlsplit

from .keyed_locks import KeyedLocks
from .catalogue import settled


class Fanart:
    def __init__(self, store):
        self.store = store
        self.locks = KeyedLocks()

    async def get(self, client, artist_id, token):
        return await self.read(client, "music/" + artist_id, token)

    async def album(self, client, group_id, token):
        """Images for one release group: album covers and disc art."""
        data = await self.read(client, "music/albums/" + group_id, token)
        albums = data.get("albums")
        return albums.get(group_id, {}) if isinstance(albums, dict) else {}

    async def read(self, client, path, token):
        key = (
            "fanart-metadata:"
            + path.removeprefix("music/")
            + ":"
            + hashlib.sha256(token.encode()).hexdigest()[:12]
        )
        async with self.locks.hold(key):
            cached = await asyncio.to_thread(self.store.read_document, key)
            if cached.get("expires", 0) > time.time():
                return cached["data"]
            response = await client.get(
                "https://webservice.fanart.tv/v3.2/" + path,
                headers={"api-key": token},
                timeout=12,
                follow_redirects=False,
            )
            # Fanart answers 404 when it has no images; that is a cacheable answer.
            if response.status_code == 404:
                data = {}
            else:
                response.raise_for_status()
                if len(response.content) > 2_000_000:
                    raise ValueError("Artwork metadata too large")
                data = response.json()
                if not isinstance(data, dict):
                    raise ValueError("Invalid artwork metadata")
            await settled(
                self.store.write_document,
                key,
                {"expires": time.time() + 86400, "data": data},
            )
            return data


def best(images, english=False):
    """The most-liked image, preferring English (or language-neutral) logos."""
    images = [i for i in images or [] if isinstance(i, dict) and i.get("url")]
    if english:
        local = [i for i in images if i.get("lang") in ("en", "00", "", None)]
        images = local or images
    return max(images, key=lambda i: int(i.get("likes") or 0), default=None)


async def download(client, url, limit=5_000_000):
    """Image bytes from Fanart's asset host only, bounded in size."""
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.hostname != "assets.fanart.tv"
        or parts.port not in (None, 443)
        or parts.username
        or parts.password
    ):
        raise ValueError("Untrusted artwork")
    async with client.stream("GET", url, timeout=12, follow_redirects=False) as r:
        r.raise_for_status()
        content = bytearray()
        async for chunk in r.aiter_bytes():
            content.extend(chunk)
            if len(content) > limit:
                raise ValueError("Artwork too large")
    return bytes(content)
