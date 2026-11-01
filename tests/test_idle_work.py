"""#63: with nobody using the server, its background loops do no work until
something changes or falls due (live 1 Nov: ~3,500 worker-thread wakeups a
second and 4.6% of a core while idle)."""

import asyncio
import json
import time

from server.downloader import Downloader
from server.store import Store


def queue_row(store, request_id, **payload):
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO queue(id,payload,status) VALUES (?,?,'waiting_for_sources')",
            (request_id, json.dumps({"id": request_id, **payload})),
        )


def enable_downloads(store):
    store.write_document(
        "downloads", {"enabled": True, "sources": [], "concurrent_downloads": 3}
    )


async def run_for(worker, seconds, during=None):
    task = asyncio.create_task(worker.run())
    try:
        if during:
            await during()
        await asyncio.sleep(seconds)
    finally:
        worker.stopping.set()
        await task


def count(worker, name, calls):
    original = getattr(worker.store, name)

    def counted(*args, **kwargs):
        calls.append(time.monotonic())
        return original(*args, **kwargs)

    setattr(worker.store, name, counted)


def test_an_idle_download_queue_is_not_polled(tmp_path):
    # The old loop read the NAS marker, created two NAS folders and queried
    # SQLite twice every half second with an empty queue.
    store = Store(tmp_path)
    worker = Downloader(store, providers={})
    passes = []
    original = worker.prepare_storage
    worker.prepare_storage = lambda: (passes.append(1), original())
    asyncio.run(run_for(worker, 2.2))
    assert len(passes) == 1


def test_a_new_request_wakes_the_download_queue_at_once(tmp_path):
    store = Store(tmp_path)
    worker = Downloader(store, providers={})
    claims = []
    count(worker, "claim_request", claims)

    async def add_request():
        await asyncio.sleep(0.8)
        claims.clear()
        await asyncio.to_thread(queue_row, store, "new")
        added = time.monotonic()
        while not claims and time.monotonic() - added < 3:
            await asyncio.sleep(0.02)
        assert claims and claims[0] - added < 0.3

    asyncio.run(run_for(worker, 0, add_request))


def test_a_deferred_request_is_claimed_when_it_falls_due(tmp_path):
    store = Store(tmp_path)
    enable_downloads(store)
    queue_row(store, "later", retry_after=time.time() + 1.2)
    worker = Downloader(store, providers={})
    claims = []
    count(worker, "claim_request", claims)
    started = time.monotonic()
    asyncio.run(run_for(worker, 2.5))
    due = [moment - started for moment in claims if moment - started > 1.1]
    assert due and due[0] < 1.8


def test_changing_download_settings_wakes_the_download_queue(tmp_path):
    store = Store(tmp_path)
    woken = []
    store.queue_listeners.append(lambda: woken.append(1))
    store.save_download_settings({"concurrent_downloads": 2})
    assert woken
