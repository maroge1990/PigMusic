"""Display-only, bounded Fanart artist backdrops."""

import asyncio, hashlib
from .keyed_locks import KeyedLocks
from .images import save_image
from .library import identifier
from .catalogue import mbid
from .media import settings
from .fanart import Fanart, best, download


class Backgrounds:
    def __init__(self, store, artwork, fanart=None):
        self.store, self.artwork = store, artwork
        self.locks = KeyedLocks()
        self.slots = asyncio.Semaphore(3)
        self.fanart = fanart or Fanart(store)

    async def get(self, client, artist_id):
        config = settings(self.store)
        token = config.get("fanart_key")
        if not token or not config["portraits"]:
            return None
        artist_id = mbid(artist_id)
        target = background_target(self.store, artist_id)
        key = target.stem
        if target.exists():
            return target
        async with self.locks.hold(key), self.slots:
            if target.exists():
                return target
            if await self.artwork.waiting(key):
                return None
            try:
                data = await self.fanart.get(client, artist_id, token)
                photo = best(data.get("artistbackground"))
                if not photo:
                    await self.artwork.missing(key, 86400)
                    return None
                content = await download(client, photo["url"])
                await asyncio.to_thread(self.save, content, target)
                return target
            except Exception:
                await self.artwork.missing(key, 300)
                return None

    @staticmethod
    def save(content, target):
        save_image(content, target, (1600, 900), quality=86)


def background_target(store, artist_id):
    token = settings(store).get("fanart_key", "")
    key = identifier(
        "artist-background",
        mbid(artist_id),
        hashlib.sha256(token.encode()).hexdigest()[:12],
    )
    return store.cover_dir / (key + ".jpg")
