"""Server-side pre-caching shared by every person and device.

Home, each person's recommendations and every library artist are known ahead
of time, so their pages are prepared in the background: artist portraits,
photographs, logos, popular songs, album lists and covers. Home's albums also
get their recommended edition's tracklist and cover, so they open at once. A search prepares
its top results at once. All provider calls run as background work, so a
person waiting on a page always goes first, and answers close to expiry are
renewed early so nobody waits on an expired cache.
"""

import asyncio
import logging
import time
from collections import OrderedDict

from .artist_identity import cached_identities
from .catalogue import background_metadata, mbid, warming

log = logging.getLogger("pig_music.warmer")

PASS_INTERVAL = 30 * 60
# An artist prepared this recently is not prepared again within a pass.
ARTIST_REUSE = 6 * 3600
LIBRARY_COVERS = 12
# Page covers waiting to be fetched, at most (a few artists' first screens).
COVER_BACKLOG = 240
PAGE_COVER_BATCH = 24
# A browsing session cannot indefinitely postpone Home and Library preparation.
URGENT_BATCHES = 3
# Artists of one Home feed whose pages are prepared.
FEED_ARTISTS = 12

# Lower numbers run first.
SEARCH, HOME, LIBRARY = 0, 1, 2


def entry(artist):
    """A library artist's entry without its artwork, which a fetched portrait
    changes without changing what the warmer prepares."""
    return {
        k: v
        for k, v in artist.items()
        if k not in {"cover_url", "fallback_cover_url", "artwork_metadata"}
    }


class Warmer:
    def __init__(self, store, home, client):
        self.store = store
        self.home = home
        self.client = client
        # Wired once the catalogue routes exist.
        self.warm_artist = None
        self.library_identity = None
        # When each library artist's caches next need renewing (#145).
        self.warm_due = None
        # Each library artist's entry as last prepared, by library ID.
        self.entries = {}
        self.cover = None
        # Prepares a release group's editions and recommended edition; returns
        # that edition's ID.
        self.warm_album = None
        # Called after each pass, e.g. to show portraits the pass fetched.
        self.after_pass = None
        self.albums = OrderedDict()
        # Home cards' covers handed to the queue, by (kind, ID), and when.
        self.cards = OrderedDict()
        self.queue = asyncio.PriorityQueue()
        self.pending = {}
        # Page covers queued and not yet fetched, so each waits only once.
        self.cover_pending = set()
        self.done = OrderedDict()
        self.serial = 0
        self.tasks = []
        self.state = {
            "running": False,
            "last_pass_started": None,
            "last_pass_finished": None,
            "last_pass_seconds": None,
            "artists_prepared": 0,
            "covers_prepared": 0,
            "failures": 0,
        }

    def start(self):
        self.state["running"] = True
        self.tasks = [
            asyncio.create_task(self.schedule()),
            asyncio.create_task(self.work()),
        ]

    async def close(self):
        for task in self.tasks:
            task.cancel()
        for task in self.tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.state["running"] = False

    def status(self):
        return {
            **self.state,
            "queued": self.queue.qsize(),
            "artists_ready": len(self.done),
        }

    def submit(self, artist_id, full=False, priority=LIBRARY, covers=()):
        """Ask for an artist page (and any extra covers) to be prepared."""
        try:
            artist_id = mbid(artist_id)
        except (ValueError, TypeError, AttributeError):
            return False
        prepared = self.done.get(artist_id)
        if (
            prepared
            and prepared[1] >= full
            and time.time() - prepared[0] < (ARTIST_REUSE)
        ):
            if covers:
                self.serial += 1
                self.queue.put_nowait(
                    (priority, self.serial, None, False, covers, False)
                )
            return False
        current = self.pending.get(artist_id)
        if current is not None and current[0] <= priority and current[1] >= full:
            return False
        self.pending[artist_id] = (priority, full)
        self.serial += 1
        self.queue.put_nowait(
            (priority, self.serial, artist_id, full, tuple(covers), False)
        )
        return True

    def covers(self, items):
        """Covers a page shows next, with a bounded share ahead of the pass.
        Each is queued once while it waits, within COVER_BACKLOG, and only
        while warming runs; returns how many were queued."""
        if not self.state["running"]:
            return 0
        room = max(0, COVER_BACKLOG - len(self.cover_pending))
        fresh = [
            item for item in dict.fromkeys(items) if item not in self.cover_pending
        ]
        fresh = fresh[:room]
        if fresh:
            self.cover_pending.update(fresh)
            for offset in range(0, len(fresh), PAGE_COVER_BATCH):
                self.serial += 1
                batch = tuple(fresh[offset : offset + PAGE_COVER_BATCH])
                self.queue.put_nowait((SEARCH, self.serial, None, False, batch, True))
        return len(fresh)

    def background_job(self):
        """Take one waiting Home/Library job, preserving urgent job order and
        Queue.join accounting. Only the worker removes jobs from this queue."""
        deferred, found = [], None
        while not self.queue.empty():
            job = self.queue.get_nowait()
            if job[0] >= HOME:
                found = job
                break
            deferred.append(job)
        for job in deferred:
            # Put before task_done: join must never see a transient empty pass.
            self.queue.put_nowait(job)
            self.queue.task_done()
        return found

    def search(self, artist_ids):
        """A search's likely next taps: essentials for two, all for the first.

        Type-ahead sends a search at each pause in typing, so this stays small;
        the rest is prepared when an artist is opened.
        """
        for index, artist_id in enumerate(artist_ids[:2]):
            self.submit(artist_id, full=index < 1, priority=SEARCH)

    async def work(self):
        background_metadata.set(True)
        warming.set(True)
        urgent = 0
        while True:
            job = self.background_job() if urgent >= URGENT_BATCHES else None
            priority, _, artist_id, full, covers, page_covers = (
                job or await self.queue.get()
            )
            urgent = urgent + 1 if priority == SEARCH else 0
            try:
                found = []
                if artist_id:
                    queued = self.pending.get(artist_id)
                    if queued and (queued[0] < priority or queued[1] > full):
                        continue  # A more urgent request for this artist is queued.
                    found = await self.warm_artist(artist_id, full)
                    self.done[artist_id] = (time.time(), full)
                    self.done.move_to_end(artist_id)
                    while len(self.done) > 5000:
                        self.done.popitem(last=False)
                    self.state["artists_prepared"] += 1
                limit = LIBRARY_COVERS if priority == LIBRARY else 24
                for kind, item_id in list(covers) + found[:limit]:
                    if kind == "album":
                        item_id = await self.album(item_id)
                        if not item_id:
                            continue
                        kind = "release"
                    if await self.cover(kind, item_id):
                        self.state["covers_prepared"] += 1
            except asyncio.CancelledError:
                raise
            except Exception:
                self.state["failures"] += 1
                log.exception("Preparing an artist page failed")
            finally:
                if artist_id and self.pending.get(artist_id) == (priority, full):
                    self.pending.pop(artist_id, None)
                if page_covers:
                    self.cover_pending.difference_update(covers)
                self.queue.task_done()

    async def schedule(self):
        background_metadata.set(True)
        warming.set(True)
        await asyncio.sleep(20)
        while True:
            started = time.monotonic()
            self.state["last_pass_started"] = time.time()
            try:
                await self.run_pass()
                await self.queue.join()
                if self.after_pass:
                    await asyncio.to_thread(self.after_pass)
                seconds = round(time.monotonic() - started, 1)
                self.state["last_pass_finished"] = time.time()
                self.state["last_pass_seconds"] = seconds
                log.info(
                    "Pre-cache pass complete in %ss: %s artist pages and %s covers "
                    "prepared so far, %s failures",
                    seconds,
                    self.state["artists_prepared"],
                    self.state["covers_prepared"],
                    self.state["failures"],
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                self.state["failures"] += 1
                log.exception("Pre-cache pass failed")
            await asyncio.sleep(PASS_INTERVAL)

    async def run_pass(self):
        """Queue Home, each person's recommendations and every library artist."""
        client = self.client()
        feeds = []
        try:
            feeds.append(await self.home.popular(client))
        except Exception:
            self.state["failures"] += 1
        for user_id in await asyncio.to_thread(self.people):
            try:
                projection = await asyncio.to_thread(
                    self.store.user_libraries.projection, user_id
                )
                feeds.append(await self.home.recommended(client, projection))
            except Exception:
                self.state["failures"] += 1
        for feed in feeds:
            self.queue_feed(feed)
        artists = [
            artist
            for artist in list(self.store.library.get("artists", []))
            if not artist.get("compilation_only")
        ]
        # Known identities in one read, not one connection per artist (#96).
        known = await asyncio.to_thread(cached_identities, self.store, artists)
        waiting = []
        for artist in artists:
            identity = known.get(artist["id"])
            identity = (
                identity["id"]
                if identity
                else await self.library_identity(artist["id"])
            )
            try:
                identity = mbid(identity) if identity else None
            except (ValueError, TypeError, AttributeError):
                identity = None
            if identity and not self.prepared(identity):
                waiting.append((artist, identity))
        current = {artist["id"] for artist in artists}
        self.entries = {k: v for k, v in self.entries.items() if k in current}
        # An artist prepared before is prepared again only when its library
        # entry changed or one of its caches is due for renewal: every 6 h,
        # each cost reads, stats and its covers' checks (#145).
        unchanged = [
            identity
            for artist, identity in waiting
            if self.entries.get(artist["id"]) == entry(artist) and identity in self.done
        ]
        due = {}
        if self.warm_due and unchanged:
            try:
                due = await asyncio.to_thread(self.warm_due, unchanged)
            except Exception:
                # Unknown: prepare them all, as before #145.
                log.exception("Reading the library artists' cache expiry failed")
        now = time.time()
        for artist, identity in waiting:
            if identity in due:
                when, retry = due[identity]
                if retry:
                    when = min(when, self.done.get(identity, (0,))[0] + ARTIST_REUSE)
                if when > now:
                    continue
            if self.submit(identity, full=True, priority=LIBRARY):
                self.entries[artist["id"]] = entry(artist)

    def prepared(self, artist_id):
        """Whether `submit` would turn a library artist away as recent."""
        prepared = self.done.get(artist_id)
        return bool(
            prepared and prepared[1] and time.time() - prepared[0] < ARTIST_REUSE
        )

    def queue_feed(self, feed):
        covers = [("release-group", a["id"]) for a in feed.get("albums", [])] + [
            ("release", s["release_id"])
            for s in feed.get("songs", [])
            if s.get("release_id")
        ]
        artists = list(
            dict.fromkeys(
                item["artist_id"]
                for kind in ("artists", "albums", "songs")
                for item in feed.get(kind, [])
                if item.get("artist_id")
            )
        )
        for index, artist_id in enumerate(artists[:FEED_ARTISTS]):
            self.submit(artist_id, full=index < 2, priority=HOME)
            # The covers are their own job, right after the first artist:
            # riding on its job, they were dropped whenever that artist was
            # already queued (#97).
            if index == 0 and covers:
                self.queue_covers(covers)
        if not artists and covers:
            self.queue_covers(covers)
        self.prepare_albums(feed)

    def queue_covers(self, covers):
        self.serial += 1
        self.queue.put_nowait((HOME, self.serial, None, False, tuple(covers), False))

    async def album(self, group_id):
        if self.warm_album is None:
            return None
        try:
            return await self.warm_album(group_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.state["failures"] += 1
            return None

    def prepare_feed(self, feed):
        """Queue what a Home feed's cards ask for the moment it is shown
        (#168): each album's release-group cover and each listed artist's
        photo, then the album pages a tap opens. Only what the feed lists,
        each once per ARTIST_REUSE; the one worker fetches them in turn."""
        now = time.time()
        covers = []
        for album in feed.get("albums", []):
            try:
                item = ("release-group", mbid(album.get("id")))
            except (ValueError, TypeError, AttributeError):
                continue
            if now - self.cards.get(item, 0) < ARTIST_REUSE:
                continue
            self.cards[item] = now
            self.cards.move_to_end(item)
            covers.append(item)
        while len(self.cards) > 5000:
            self.cards.popitem(last=False)
        if covers:
            self.queue_covers(covers)
        # The photo is part of the artist's page; `submit` turns away an
        # artist prepared recently or already waiting.
        artists = dict.fromkeys(
            a["artist_id"] for a in feed.get("artists", []) if a.get("artist_id")
        )
        for artist_id in list(artists)[:FEED_ARTISTS]:
            self.submit(artist_id, priority=HOME)
        self.prepare_albums(feed)
        return len(covers)

    def prepare_albums(self, feed):
        """Queue a Home feed's album pages not prepared recently."""
        now = time.time()
        fresh = []
        for album in feed.get("albums", []):
            group_id = album.get("id")
            if not group_id or now - self.albums.get(group_id, 0) < ARTIST_REUSE:
                continue
            self.albums[group_id] = now
            self.albums.move_to_end(group_id)
            fresh.append(("album", group_id))
        while len(self.albums) > 5000:
            self.albums.popitem(last=False)
        if fresh:
            self.serial += 1
            self.queue.put_nowait((HOME, self.serial, None, False, tuple(fresh), False))
        return len(fresh)

    def people(self):
        with self.store.connect() as conn:
            return [
                row[0] for row in conn.execute("SELECT id FROM users WHERE disabled=0")
            ]
