"""Mix planning must leave the event loop free for API requests, playback setup
and live updates, however large the library (two-week review, finding 3)."""

import asyncio
import json
import secrets
import time

import pytest

from server.auth import Auth
from server.mixes import Mixes
from server.store import Store

SIZE = 12000
# Generous for a shared CI machine; the planning work itself takes seconds.
LONGEST_STALL = 0.5


def household(tmp_path):
    (tmp_path / "library-snapshot.json").write_text(
        json.dumps(
            {
                "tracks": [
                    {
                        "id": str(i),
                        "title": f"Song {i}",
                        "artist": f"Artist {i // 100}",
                        "album_artist": f"Artist {i // 100}",
                        "album": f"Album {i // 10}",
                        "duration": 1,
                        "year": 1970 + i % 50,
                        "available": True,
                    }
                    for i in range(SIZE)
                ]
            }
        )
    )
    store = Store(tmp_path)
    owner = Auth(store).create_owner("owner", secrets.token_urlsafe(32))
    store.user_libraries.add(owner, [str(i) for i in range(SIZE)])
    return store, owner


@pytest.mark.loop_timing
def test_preparing_a_large_library_never_stalls_the_event_loop(tmp_path):
    store, owner = household(tmp_path)
    mixes = Mixes(store, None, None)

    async def candidates(client, projection):
        return [], None  # No provider calls: planning work only.

    mixes.candidates = candidates
    # Keep this to planning: preparing (downloading) staged editions is
    # bounded network work with its own tests.

    async def staged(*args, **kwargs):
        return None

    mixes.prepare_staged = staged

    async def run():
        stalls = []
        last = time.perf_counter()

        async def ticker():
            nonlocal last
            while True:
                await asyncio.sleep(0.01)
                now = time.perf_counter()
                stalls.append(now - last)
                last = now

        probe = asyncio.create_task(ticker())
        await asyncio.sleep(0)
        try:
            await mixes.prepare_person(None, owner, mixes.day())
        finally:
            stalls.append(time.perf_counter() - last)
            probe.cancel()
        return stalls

    stalls = asyncio.run(run())
    with store.connect() as conn:
        kinds = {
            row[0]
            for row in conn.execute(
                "SELECT kind FROM mix_editions WHERE user_id=?", (owner,)
            )
        }
    assert {"daily", "back", "decade", "deep"} <= kinds, kinds
    assert max(stalls) < LONGEST_STALL, f"event loop stalled {max(stalls):.2f}s"
