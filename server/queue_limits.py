"""How much a person may queue (D-128, #242).

Each person may have up to 2,000 songs waiting in the queue and add up to
1,000 songs in any 10 minutes; the household may have 10,000 waiting in
total. Everyone is limited, the owner too. Past a limit the songs that fit
are queued, in the order asked, and the rest are left out (their positions
in the answer's `over_limit`): nothing already asked for is lost.

Songs are limited, not calls: the iPhone sends one song per request, the
web up to 500, an Apple Music import up to 200.

"Waiting" is a job not finished (`PENDING`: still to do, under way or
paused): a paused job holds its place, so resuming it never takes a person
past the limit. A song counts for everyone who asked for it, so what costs
a person a waiting slot and a song of their rate is a new job, or joining
someone else's unfinished job; a new job also costs a household slot.
Asking again for a song one is already waiting for costs nothing. Retry in
Downloads revives a job only while every person on it, and the household,
has room; it is not rated (the person asked for the song before).

The rate is an exact rolling window per person, in memory: each request's
admissions are kept with their time for ten minutes. A restart forgets
them (the waiting limits, counted from the queue table, still hold). The
waiting counts are read from the queue table inside the request's own write
transaction, once per request and only when a song would cost a slot. The
queue has no index on its state (adding one is a schema change), so a count
reads the unfinished rows' requester lists; at the household's 10,000 that
costs a few milliseconds, less than the one existing query each song of a
request already makes over the whole queue.

Only what a person asks for is limited. Jobs the server makes on its own
(quality replacements of every kind, the upgrade scan, the owner's library
migration, an artist's discography) are exempt and queued as before, but
they are still counted below: a replacement waits for every library member
it is for, an artist job for the owner, a migration job for the household
only. So a large quality change can leave a person at their limit for
ordinary Adds until those jobs finish.
"""

import collections
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


class QueueFull(Exception):
    """A Retry that would take a waiting limit past its end (D-128)."""

    def __init__(self, limit):
        super().__init__(limit)
        self.limit = limit


def full_answer(limit, left_out=1, retry_after=None, **result):
    """The 429 body for songs a queue limit left out: the code
    `queue_limit`, the limit, and for the rate when to try again."""
    answer = {
        "detail": "Too many songs are waiting. Try again later.",
        "code": "queue_limit",
        "limit": limit,
        "left_out": left_out,
        **result,
    }
    # The error schema's retry_after is an integer: absent, never null, when
    # no wait applies (a waiting limit).
    answer.pop("retry_after", None)
    if retry_after is not None:
        answer["retry_after"] = retry_after
    return answer


class QueueLimits:
    """The limits and each person's admissions; safe from any thread.
    `clock` is injectable for tests (seconds, monotonic)."""

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
        # Person -> their admissions in the window, oldest first, as
        # [time, songs]: one entry per request (at most `songs` of them).
        self.admitted = {}

    def _window(self, user_id, now):
        entries = self.admitted.get(user_id)
        if entries is None:
            return ()
        while entries and entries[0][0] <= now - self.seconds:
            entries.popleft()
        if not entries:
            del self.admitted[user_id]
        return entries

    def available(self, user_id):
        """Songs this person may add now: the rate less those admitted in
        the last `seconds`."""
        with self.lock:
            used = sum(count for _, count in self._window(user_id, self.clock()))
        return max(0, self.songs - used)

    def take(self, user_id, count):
        """Records `count` songs admitted now. Called only once they are
        committed, under the lock that serialises the requests."""
        if count <= 0:
            return
        with self.lock:
            now = self.clock()
            self._window(user_id, now)
            self.admitted.setdefault(user_id, collections.deque()).append([now, count])

    def wait(self, user_id):
        """Whole seconds until one more song fits (at least 1): until the
        oldest admissions that make room have left the window."""
        with self.lock:
            now = self.clock()
            entries = [tuple(entry) for entry in self._window(user_id, now)]
        free = self.songs - sum(count for _, count in entries)
        if free >= 1:
            return 1
        for at, count in entries:
            free += count
            if free >= 1:
                # An admission leaves the window `seconds` after it was made.
                return max(1, math.ceil(at + self.seconds - now))
        return 1

    def room(self, conn, user_id):
        return Room(self, conn, user_id)

    def full(self, conn, people, new_job):
        """For a Retry: the waiting limit that reviving a job (`new_job`) or
        joining people to one would pass, or None."""
        if new_job:
            (household,) = conn.execute(
                "SELECT count(*) FROM queue WHERE status IN " + sql(PENDING)
            ).fetchone()
            if household >= self.household:
                return HOUSEHOLD
        for person in people:
            _, mine = conn.execute(WAITING_COUNT, (person,)).fetchone()
            if mine >= self.person:
                return PERSON
        return None


class Room:
    """One request's room. Counted the first time a song would cost a slot;
    each song that fits is counted at once, so a batch is accepted in order
    up to the first limit. A new job costs a person slot, a household slot
    and a song of the rate; joining someone else's job costs no household
    slot."""

    def __init__(self, limits, conn, user_id):
        self.limits = limits
        self.conn = conn
        self.user_id = user_id
        self.counted = False
        self.person = self.household = self.used = 0
        self.limit = None

    def _count(self):
        household, person = self.conn.execute(WAITING_COUNT, (self.user_id,)).fetchone()
        self.person = self.limits.person - person
        self.household = self.limits.household - household
        self.rate = self.limits.available(self.user_id)
        self.counted = True

    def admit(self, new_job=True):
        """Whether one more song fits; if not, which limit it met (the
        waiting limits are named first: they don't free up in seconds)."""
        if not self.counted:
            self._count()
        checks = [(PERSON, self.person)]
        if new_job:
            checks.append((HOUSEHOLD, self.household))
        checks.append((RATE, self.rate - self.used))
        for name, left in checks:
            if left < 1:
                self.limit = name
                return False
        self.person -= 1
        self.household -= bool(new_job)
        self.used += 1
        return True

    def answer(self):
        return {
            "limit": self.limit,
            "retry_after": (
                self.limits.wait(self.user_id) if self.limit == RATE else None
            ),
        }
