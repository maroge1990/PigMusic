"""Durable artist acquisition with generation-guarded cancellation/progress."""

import asyncio
import json
import threading
import time
import uuid

from .catalogue import (
    Catalogue,
    RELEASE_INC,
    edition_rank,
    release_tracks,
    mbid,
    background_metadata,
)
from .releases import release_query


class ArtistJobs:
    def __init__(self, store, catalogue, user_id=None, root=None):
        self.store, self.catalogue = store, catalogue
        self.user_id = user_id
        self.stopping = root.stopping if root else asyncio.Event()
        self.lock = root.lock if root else threading.RLock()

    def owner(self):
        if self.user_id:
            return self.user_id
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT id FROM users WHERE role='owner' ORDER BY created_at LIMIT 1"
            ).fetchone()
        return row[0] if row else None

    def key(self, identity):
        owner = self.owner()
        return "artist-job:" + (owner + ":" if owner else "") + mbid(identity)

    def scoped(self, user_id):
        return ArtistJobs(self.store, self.catalogue, user_id, self)

    def active(self):
        owner = self.owner()
        if not owner:
            return True
        with self.store.connect() as conn:
            return (
                conn.execute(
                    "SELECT 1 FROM users WHERE id=? AND disabled=0", (owner,)
                ).fetchone()
                is not None
            )

    def save(self, key, value):
        with self.lock, self.store.connect() as conn:
            conn.execute(
                "INSERT INTO documents VALUES (?,?) ON CONFLICT(name) DO UPDATE SET payload=excluded.payload",
                (key, json.dumps(value)),
            )

    def state(self, identity):
        return self.store.read_document(self.key(identity)) or {
            "status": "idle",
            "monitor": False,
            "kinds": ["album"],
        }

    def configure(self, identity, kinds, monitor, request=False, periodic=False):
        """Save artist settings; ``request`` queues an acquisition pass.

        Monitoring means *new* releases: turning it on (or widening its kinds)
        without a request records the existing catalogue as a baseline instead
        of downloading it. Periodic monitor checks keep the done list so only
        release groups first seen later are acquired. An explicit request
        still acquires the whole catalogue for the chosen kinds.
        """
        identity = mbid(identity)
        if not kinds or not set(kinds) <= {"album", "single", "ep"}:
            raise ValueError("Choose albums, singles or EPs.")
        with self.lock:
            state = self.state(identity)
            if state.get("status") in {"queued", "running"} and request:
                raise ValueError("This artist already has a request running.")
            starting = (
                monitor
                and not request
                and (
                    not state.get("monitor")
                    or not set(kinds) <= set(state.get("kinds", []))
                )
            )
            state.update(
                id=identity, user_id=self.owner(), kinds=kinds, monitor=monitor
            )
            if starting:
                state["baseline_pending"] = True
            if request and periodic:
                state.update(status="queued", error=None, generation=uuid.uuid4().hex)
            elif request:
                state.update(
                    status="queued",
                    done=[],
                    created=0,
                    error=None,
                    baseline_pending=False,
                    generation=uuid.uuid4().hex,
                )
            self.save(self.key(identity), state)
            return state

    def cancel(self, identity):
        with self.lock:
            state = self.state(identity)
            state.update(status="cancelled", monitor=False, generation=uuid.uuid4().hex)
            self.save(self.key(identity), state)
            return state

    def current(self, identity, generation):
        state = self.state(identity)
        return (
            state
            if self.active()
            and state.get("status") in {"queued", "running"}
            and state.get("generation") == generation
            else None
        )

    def progress(self, identity, generation, **changes):
        with self.lock:
            state = self.current(identity, generation)
            if state is None:
                return None
            state.update(changes)
            self.save(self.key(identity), state)
            return state

    def start(self, identity, state):
        with self.lock:
            current = self.state(identity)
            if not self.active() or current.get("status") not in {"queued", "running"}:
                return None
            generation = current.get("generation") or uuid.uuid4().hex
            current.update(generation=generation, status="running", error=None)
            self.save(self.key(identity), current)
            state["generation"] = generation
            return current

    def submit(self, identity, generation, group_id, tracks):
        # Generation validation and queue submission stay under one lock.
        with self.lock:
            current = self.current(identity, generation)
            if current is None:
                return False
            result = self.store.submit_catalogue(tracks, user_id=self.owner())
            self.progress(
                identity,
                generation,
                created=current.get("created", 0) + result["created"],
                done=[*current.get("done", []), group_id],
            )
            return True

    async def acquire(self, client, identity, state):
        # Every store call runs in a worker thread: submit_catalogue waits on
        # lifecycle_lock, which a download may hold for minutes (LOOP1).
        state = await asyncio.to_thread(self.start, identity, state)
        if state is None:
            return
        generation = state["generation"]
        groups = []
        for kind in state["kinds"]:
            offset = 0
            while not self.stopping.is_set() and await asyncio.to_thread(
                self.current, identity, generation
            ):
                data = await self.catalogue.get(
                    client,
                    "release-group/",
                    query=release_query(identity, kind),
                    limit=100,
                    offset=offset,
                )
                rows = data.get("release-groups", [])
                groups.extend(rows)
                offset += len(rows)
                if not rows or offset >= data.get("count", 0):
                    break
        if not await asyncio.to_thread(
            self.progress, identity, generation, total=len(groups)
        ):
            return
        for group in groups:
            current = await asyncio.to_thread(self.current, identity, generation)
            if self.stopping.is_set() or current is None:
                return
            if group["id"] in current.get("done", []):
                continue
            if current.get("baseline_pending"):
                # Existing releases at follow time are known, not requested.
                await asyncio.to_thread(
                    self.progress,
                    identity,
                    generation,
                    done=[*current.get("done", []), group["id"]],
                )
                continue
            editions = await self.catalogue.get(
                client,
                "release/",
                **{"release-group": group["id"], "inc": "media", "limit": 100},
            )
            ranked = sorted(editions.get("releases", []), key=edition_rank)
            tracks = []
            if ranked:
                release = await self.catalogue.get(
                    client,
                    "release/" + ranked[0]["id"],
                    inc=RELEASE_INC,
                )
                tracks = await asyncio.to_thread(
                    Catalogue(self.store).presence, release_tracks(release)
                )
            if not await asyncio.to_thread(
                self.submit, identity, generation, group["id"], tracks
            ):
                return
        await asyncio.to_thread(
            self.progress,
            identity,
            generation,
            status="completed",
            checked_at=time.time(),
            baseline_pending=False,
        )

    def pending(self):
        from .user_library import initialise_owner

        initialise_owner(self.store)
        with self.store.connect() as conn:
            keys = [
                key
                for (key,) in conn.execute(
                    "SELECT name FROM documents WHERE name LIKE 'artist-job:%'"
                )
            ]
        jobs = []
        for key in keys:
            parts = key.split(":")
            if len(parts) == 2:
                if self.owner():
                    continue  # Legacy rows are retained, but migrated owner jobs run instead.
                scoped, identity = self, parts[1]
            elif len(parts) == 3:
                scoped, identity = self.scoped(parts[1]), parts[2]
            else:
                continue
            jobs.append((scoped, identity))
        return jobs

    def due(self, identity):
        """The job to run now, if any. Reads its state afresh under the lock,
        so a cancellation or request since `pending()` is never undone."""
        with self.lock:
            if not self.active():
                return None
            state = self.state(identity)
            if (
                state.get("monitor")
                and time.time() - state.get("checked_at", 0) > 86400
                and state.get("status") not in {"queued", "running"}
            ):
                state = self.configure(
                    identity, state["kinds"], True, request=True, periodic=True
                )
            return state if state.get("status") in {"queued", "running"} else None

    async def run(self, client):
        background_metadata.set(True)
        while not self.stopping.is_set():
            for jobs, identity in await asyncio.to_thread(self.pending):
                if self.stopping.is_set():
                    return
                state = None
                try:
                    state = await asyncio.to_thread(jobs.due, identity)
                    if state is not None:
                        await jobs.acquire(client, identity, state)
                except Exception:
                    if state is not None:
                        await asyncio.to_thread(
                            jobs.progress,
                            identity,
                            state.get("generation"),
                            status="failed",
                            error="Artist lookup unavailable. Retry this request.",
                            checked_at=time.time(),
                        )
            try:
                await asyncio.wait_for(self.stopping.wait(), timeout=15)
            except TimeoutError:
                pass
