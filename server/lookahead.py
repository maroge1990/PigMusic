"""Look up the songs on screen before the tap (#192).

When the server answers a screen (a release page, Home's feeds, an online
search), it names the first few songs there that are not in the library.
Each is matched on JioSaavn in the background, so the tap finds the answer
waiting: the match is kept for hours and only re-checked on the tap. The
relayed sources are left to the tap: a look-ahead never searches them or
starts a transfer, so it cannot hold the relay slots a real tap needs, and its
JioSaavn miss is not remembered as a miss.

Per person: at most PER_PERSON lookups run at once, a new screen replaces the
songs the previous one queued and has not started, and a screen may wait a
moment first (a search typed ahead settles before it costs anything).
Lookups run as background metadata work, so a person waiting on a page always
goes first, and JioSaavn keeps its own slots.
"""

import asyncio
import logging

from .catalogue import background_metadata

log = logging.getLogger("pig_music.lookahead")

# Songs looked up per screen, and at once per person.
PER_SCREEN = 5
PER_PERSON = 2


class Person:
    __slots__ = ("pending", "running", "timer", "screen")

    def __init__(self):
        self.pending = []
        self.running = set()
        self.timer = None
        # The screen the pending songs came from.
        self.screen = None


class LookAhead:
    """Event-loop owned: call `screen` from request handlers only."""

    def __init__(self, remote_audio, client, enabled):
        self.remote_audio = remote_audio
        # The shared HTTP client (set once the app starts).
        self.client = client
        # Whether remote playback is on: nothing is looked up otherwise.
        self.enabled = enabled
        self.people = {}
        self.closed = False

    def screen(self, user_id, jobs, delay=0.0, name=None):
        """`jobs`: async callables, each returning the track a tap on that
        song would play, or None (in the library, or unknown). Replaces what
        this person's previous screen queued and has not started; a screen
        answered in parts (Home's feeds) names itself, and its parts add up
        to PER_SCREEN together."""
        if self.closed or not user_id:
            return 0
        jobs = list(jobs)[:PER_SCREEN]
        person = self.people.get(user_id)
        if person is None:
            if not jobs:
                return 0
            person = self.people[user_id] = Person()
        if person.timer is not None:
            person.timer.cancel()
            person.timer = None
        if name is not None and person.screen == name:
            jobs = (person.pending + jobs)[:PER_SCREEN]
        person.screen = name
        person.pending = jobs
        count = len(jobs)
        if delay > 0 and jobs:
            loop = asyncio.get_running_loop()
            person.timer = loop.call_later(delay, self.started, user_id)
        else:
            self.pump(user_id)
        return count

    def started(self, user_id):
        person = self.people.get(user_id)
        if person is not None:
            person.timer = None
            self.pump(user_id)

    def pump(self, user_id):
        person = self.people.get(user_id)
        if person is None:
            return
        if person.timer is not None:
            # A screen still settling (a search typed ahead) starts only when
            # its timer fires, not when earlier work finishes (Codex, round 1).
            return
        while person.pending and len(person.running) < PER_PERSON and not self.closed:
            job = person.pending.pop(0)
            task = asyncio.ensure_future(self.run(job))
            person.running.add(task)
            task.add_done_callback(lambda t, u=user_id: self.finished(u, t))
        self.forget(user_id)

    def finished(self, user_id, task):
        person = self.people.get(user_id)
        if person is not None:
            person.running.discard(task)
            self.pump(user_id)

    def forget(self, user_id):
        person = self.people.get(user_id)
        if (
            person is not None
            and not person.pending
            and not person.running
            and person.timer is None
        ):
            del self.people[user_id]

    async def run(self, job):
        background_metadata.set(True)
        try:
            if not await asyncio.to_thread(self.enabled):
                return
            track = await job()
            if track is None:
                return
            # JioSaavn's match only (Codex, round 1): relayed sources and
            # their transfers wait for the tap, so look-ahead never holds a
            # relay slot a tap needs. A tap joins a match still running.
            await self.remote_audio.match(self.client(), track)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Remembered by RemoteAudio as a miss or failure where it applies;
            # the tap decides what to tell the person.
            log.debug("Look-ahead lookup ended: %s", type(exc).__name__)

    async def close(self):
        self.closed = True
        tasks = []
        for person in self.people.values():
            if person.timer is not None:
                person.timer.cancel()
            person.pending = []
            tasks.extend(person.running)
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except BaseException:
                pass
        self.people.clear()
