"""One asyncio lock per key, forgotten once nobody holds or waits for it (#170).

The artwork, Fanart, Home and popularity caches each kept a dictionary of
locks that grew by one entry per key ever asked for. Event-loop only: the
counts are changed without awaiting, so no other task runs in between.
"""

import asyncio
from contextlib import asynccontextmanager


class KeyedLocks:
    def __init__(self):
        self.entries = {}  # key -> [lock, tasks holding or waiting]

    def __len__(self):
        return len(self.entries)

    @asynccontextmanager
    async def hold(self, key):
        entry = self.entries.get(key)
        if entry is None:
            entry = self.entries[key] = [asyncio.Lock(), 0]
        entry[1] += 1
        try:
            async with entry[0]:
                yield
        finally:
            entry[1] -= 1
            if not entry[1] and self.entries.get(key) is entry:
                del self.entries[key]
