"""KeyedLocks (#170): per-key exclusion, and no entry left behind."""

import asyncio

import pytest

from server.keyed_locks import KeyedLocks


def test_one_holder_per_key_and_the_entry_goes_when_all_are_done():
    async def scenario():
        locks, inside, most = KeyedLocks(), {"a": 0}, {"a": 0}

        async def worker():
            async with locks.hold("a"):
                inside["a"] += 1
                most["a"] = max(most["a"], inside["a"])
                await asyncio.sleep(0.01)
                inside["a"] -= 1

        async def other():
            async with locks.hold("b"):
                assert len(locks) == 2  # "a" is still held or awaited.

        await asyncio.gather(worker(), worker(), worker(), other())
        assert most["a"] == 1
        assert len(locks) == 0

    asyncio.run(scenario())


def test_a_cancelled_waiter_or_a_failure_leaves_no_entry_and_no_lock_held():
    async def scenario():
        locks = KeyedLocks()
        release = asyncio.Event()

        async def holder():
            async with locks.hold("a"):
                await release.wait()

        first = asyncio.create_task(holder())
        await asyncio.sleep(0)
        waiter = asyncio.create_task(holder())
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        release.set()
        await first
        assert len(locks) == 0
        with pytest.raises(RuntimeError):
            async with locks.hold("a"):
                raise RuntimeError
        assert len(locks) == 0
        # Still usable: a new holder gets a fresh lock at once.
        async with locks.hold("a"):
            assert len(locks) == 1

    asyncio.run(scenario())


def test_artwork_keeps_no_lock_for_covers_it_has_finished(tmp_path):
    """Before #170 every cover ever asked for left a lock in Artwork.locks."""
    import io
    import uuid

    import httpx
    from PIL import Image

    from server.artwork import Artwork
    from server.store import Store

    stream = io.BytesIO()
    Image.new("RGB", (20, 20), "red").save(stream, "PNG")
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=stream.getvalue())
    )
    artwork = Artwork(Store(tmp_path), catalogue=None)

    async def scenario():
        async with httpx.AsyncClient(transport=transport) as client:
            for _ in range(5):
                found = await artwork.catalogue_cover(
                    client, "release-group", str(uuid.uuid4())
                )
                assert found and found.is_file()

    asyncio.run(scenario())
    assert len(artwork.locks) == 0
