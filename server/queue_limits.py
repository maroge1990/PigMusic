"""How much a person may queue (D-128, #242).

Each person may have up to 2,000 songs waiting in the queue and add up to
1,000 new songs in any 10 minutes; the household may have 10,000 waiting in
total. Everyone is limited, the owner too. Past a limit the songs that fit
are queued, in the order asked, and the rest are answered `over_limit`:
nothing already asked for is lost, and asking again for a song already
queued or paused never counts.

Songs are limited, not calls: the iPhone sends one song per request, the
web up to 500, an Apple Music import up to 200.

"Waiting" is a job not finished (`PENDING`: still to do, under way or
paused): a paused job holds its place, so resuming it never takes a person
past the limit. A song counts for everyone who asked for it.

The rate is a token bucket per person, in memory: a restart refills it.
The waiting counts are read from the queue table in the request's own write
transaction, once per request and only when a song would make a new job.
The queue has no index on its state (adding one is a schema change), so the
count reads the unfinished rows' requester lists; at the household's 10,000
it costs a few milliseconds, less than the one existing query each song of
a request already makes over the whole queue.
"""

import math
import threading
import time

from .queue_states import PENDING, sql

PERSON_WAITING = 2000
HOUSEHOLD_WAITING = 10000
RATE_SONGS = 1000
RATE_SECONDS = 600

# Which limit left a song out, as the answer names it.
PERSON = "person_waiting"
HOUSEHOLD = "household_waiting"
RATE = "rate"

WAITING_COUNT = (
    "SELECT count(*),coalesce(sum(EXISTS(SELECT 1 FROM "
    "json_each(payload,'$.requested_by') WHERE value=?)),0) "
    "FROM queue WHERE status IN " + sql(PENDING)
)


class QueueLimits:
    """The limits and each person's bucket; safe from any thread. `clock`
    is injectable for tests (seconds, monotonic)."""

    def __init__(
        self,
        person=PERSON_WAITING,
        household=HOUSEHOLD_WAITING,
        songs=RATE_SONGS,
        seconds=RATE_SECONDS,
        clock=time.monotonic,
    ):
        self.person = person
        self.household = household
        self.songs = songs
        self.seconds = seconds
        self.clock = clock
        self.lock = threading.Lock()
        # Person -> (songs left, when that was worked out): one entry for
        # each person who has added a song since the server started.
        self.buckets = {}

    def _level(self, user_id, now):
        left, at = self.buckets.get(user_id, (self.songs, now))
        return min(self.songs, left + (now - at) * self.songs / self.seconds)

    def available(self, user_id):
        """Whole songs this person may add now."""
        with self.lock:
            return int(self._level(user_id, self.clock()))

    def take(self, user_id, count):
        if count <= 0:
            return
        with self.lock:
            now = self.clock()
            self.buckets[user_id] = (self._level(user_id, now) - count, now)

    def wait(self, user_id):
        """Seconds until one more song fits (at least 1)."""
        with self.lock:
            short = 1 - self._level(user_id, self.clock())
        return max(1, math.ceil(short * self.seconds / self.songs))

    def room(self, conn, user_id):
        return Room(self, conn, user_id)


class Room:
    """One request's room for new jobs. Counted the first time a song would
    make one; each song that fits is counted at once, so a batch is
    accepted in order up to the first limit."""

    def __init__(self, limits, conn, user_id):
        self.limits = limits
        self.conn = conn
        self.user_id = user_id
        self.left = None
        self.used = 0
        self.limit = None

    def _count(self):
        household, person = self.conn.execute(WAITING_COUNT, (self.user_id,)).fetchone()
        rooms = (
            (PERSON, self.limits.person - person),
            (HOUSEHOLD, self.limits.household - household),
            (RATE, self.limits.available(self.user_id)),
        )
        # On a tie the waiting limits are named: waiting songs don't free up
        # in a second, as the rate does.
        self.binding, left = min(rooms, key=lambda r: r[1])
        self.left = max(0, left)

    def take(self):
        """Whether one more new job fits; if not, which limit it met."""
        if self.left is None:
            self._count()
        if self.used < self.left:
            self.used += 1
            return True
        self.limit = self.binding
        return False

    def answer(self):
        return {
            "limit": self.limit,
            "retry_after": (
                self.limits.wait(self.user_id) if self.limit == RATE else None
            ),
        }
