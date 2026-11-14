"""Display-only Fanart artist wordmarks, kept as transparent PNGs."""

import asyncio, time
from .keyed_locks import KeyedLocks
from .images import save_image
from .library import identifier
from .catalogue import mbid, settled
from .media import settings
from .fanart import Fanart, best, key_tag


class Logos:
    def __init__(self, store, fanart=None):
        self.store = store
        self.locks = KeyedLocks()
        self.slots = asyncio.Semaphore(3)
        self.fanart = fanart or Fanart(store)

    async def get(self, client, artist_id):
        config = settings(self.store)
        token = config.get("fanart_key")
        if not token or not config["portraits"]:
            return None
        artist_id = mbid(artist_id)
        target = logo_target(self.store, artist_id)
        if target.exists():
            return target
        key = "missing-" + target.stem
        async with self.locks.hold(target.stem), self.slots:
            if target.exists():
                return target
            missing = await asyncio.to_thread(self.store.read_document, key)
            if missing.get("retry_after", 0) > time.time():
                return None
            try:
                data = await self.fanart.get(client, artist_id, token)
                logo = best(data.get("hdmusiclogo"), english=True) or best(
                    data.get("musiclogo"), english=True
                )
                if not logo:
                    await settled(
                        self.store.write_document,
                        key,
                        {"retry_after": time.time() + 86400},
                    )
                    return None
                content = await self.fanart.download(client, logo["url"], 3_000_000)
                await asyncio.to_thread(self.save, content, target)
                return target
            except Exception:
                await settled(
                    self.store.write_document, key, {"retry_after": time.time() + 300}
                )
                return None

    @staticmethod
    def save(content, target):
        # Re-encoding strips anything but pixels; transparency is the point.
        target.parent.mkdir(exist_ok=True)
        save_image(
            content,
            target,
            (800, 310),
            mode="RGBA",
            format="PNG",
            limit=8_000_000,
            optimize=True,
        )


def logo_target(store, artist_id):
    token = settings(store).get("fanart_key", "")
    key = identifier(
        "artist-logo",
        mbid(artist_id),
        key_tag(token),
    )
    return store.cover_dir / "logos" / (key + ".png")
