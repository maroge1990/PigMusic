"""Optional remote listening; matching never removes version descriptions."""

import asyncio
import contextlib
import hashlib
import json
import logging
import re
import secrets
import time
from collections import Counter, OrderedDict, deque
from urllib.parse import urlsplit

import httpx

from .request_timing import add_phase
from .library import normalise
from .downloader import duration, matches, mismatch
from .matching import (
    DISTINCT,
    album_title,
    flavour,
    lead_artist,
    primary_artist,
    recording_kind,
)
from .performance import DETAIL_CHARS
from .providers.contracts import ProviderError
from .vendor.jiosaavn import search_jiosaavn
from .youtube import YouTubeStream, search_query

log = logging.getLogger("pig_music.remote")

# Lossless sources are never streamed.
# Speed- and effect-altered uploads are other recordings even when only the
# album says so ("Song (Slowed)"); remote playback alone checks these (the
# shared song rule does not know them).
ALTERED = re.compile(
    r"\b(sped|speed(?:ed)? up|slowed|nightcore|reverb|8d|daycore)\b", re.I
)


def altered(*texts):
    """The alterations a title or album names, speed synonyms as one kind:
    a reverb upload is not the 8D one, nor slowed + reverb the slowed one."""
    found = ALTERED.findall(" ".join(t or "" for t in texts))
    return frozenset(
        "sped" if word.lower().startswith(("sped", "speed")) else word.lower()
        for word in found
    )


LABELS = {
    "jiosaavn": "JioSaavn",
    "youtube": "YouTube",
}
# A stream link the server passes through (YouTube's, #193) lasts as long as
# a signed playback link.
TOKEN_SECONDS = 6 * 3600
# Tokens kept at once, per person and in all. A person playing YouTube songs
# back to back without pause makes about 2 a minute at most (~700 in a
# token's 6 hours, nearly all for songs long finished); a player only ever
# uses its current and next song's. When full, the oldest token goes: a
# player still using it would get 404 and must play the song again.
STREAMS_PER_PERSON = 1000
STREAMS = 10_000
# A song no source has is not searched for again for a while; a failure of a
# source or the network only briefly (REMOTE1: a miss took 30-35 s each time).
MISS_SECONDS = 30 * 60
FAILURE_SECONDS = 2 * 60
# A JioSaavn match is kept this long, so a song looked up before the tap
# (#192) still plays at once; its address is checked again (one small range
# request) once it is older than PROBE_SECONDS, and looked up afresh if the
# CDN no longer serves it.
RESOLVED_SECONDS = 6 * 3600
PROBE_SECONDS = 90
RESOLVED_ENTRIES = 1000
# JioSaavn searches look-ahead may run at once, across everyone (#192).
LOOKAHEAD_SLOTS = 2


async def shared_result(task, timeout=None):
    """A caller may leave without cancelling shared work. Unlike shield in
    Python 3.14, this does not log an already-retrieved late failure."""
    done, _ = await asyncio.wait({task}, timeout=timeout)
    if not done:
        raise TimeoutError
    return task.result()


def acceptable(track, c):
    """The shared song rule for an instant source's search result (`matches`:
    lead artist, title, live/remix/demo words in title and album, length within
    5 s, agreeing IDs); a song is the same on any edition (PRODUCT), so the
    album must agree only for an edition chosen explicitly."""
    return rejection(track, c) is None


# Why a search result was not the requested song, in the order the song rule
# checks; a later reason means the result came closer (#282).
REASONS = (
    "ids",
    "artist",
    "title",
    "version",
    "no length",
    "length",
    "album kind",
    "altered",
    "album",
    "isrc",
    "matched",
)
# A no-match event's detail: the query, how many results came back and the
# first few, cut to a size the events table keeps whole.
DETAIL_RESULTS = 3


# Album names that mark other people's recordings of a song (D-130): with no
# known length an instant listen takes the song from any other album, but
# not from these. Read in `normalise`d text (case-folded, full-width letters
# plain, punctuation as word breaks).
NOT_ORDINARY = re.compile(
    r"\b(tributes?|lullab(?:y|ies)|covers|made popular by|originally performed by|"
    r"8 ?bit)\b"
)
# An album naming another recording of its songs (Codex, D-130 review): the
# title's version words (`DISTINCT`), the alterations (`ALTERED`), stripped
# versions and re-recordings ("Fearless (Taylor's Version)"). Edition words
# ("Remastered", "Deluxe", "Anniversary Edition") are not among them.
RERECORDED = re.compile(r"\b(stripped|re ?record(?:ed|ing|ings)?|\w+ s version)\b")


def ordinary(album):
    """An album the song itself may be on (D-130): named, and not one of
    NOT_ORDINARY. Compilations ("Summer Hits 2026") are ordinary."""
    text = normalise(album or "")
    return bool(text) and not NOT_ORDINARY.search(text)


def markers(*texts):
    """The other-recording words in these texts, one form each ("Demos" is
    "demo", "Sped Up" is "sped", "re-recorded" is "re record")."""
    text = normalise(" ".join(t or "" for t in texts))
    found = {re.sub(r"(?<=x)es$|s$", "", m[0]) for m in DISTINCT.finditer(text)}
    found |= altered(text)
    for word in RERECORDED.findall(text):
        found.add("re record" if word.startswith("re") else word)
    return frozenset(found)


def without_the(artist):
    """The lead artist for instant listening (D-130): a leading "The", a
    word of its own in the credit as written, does not count ("The Goo Goo
    Dolls" is "Goo Goo Dolls"). "The The", "The-Dream" and "Theory of a
    Deadman" keep their names."""
    found = re.match(r"\s*the\s+(\S.*)$", lead_artist(artist), re.I)
    if found and primary_artist(artist) != "the the":
        return primary_artist(found[1])
    return primary_artist(artist)


def aliased(track, c):
    """A result credited to the artist only once a leading "The" is dropped:
    among one source's matches an exact credit is chosen first (Codex)."""
    return primary_artist(c.get("channel")) != primary_artist(track["artist"])


def rejection(track, c):
    """The song rule's checks in order: the first one a search result fails
    (one of REASONS), or None when it is the song (`acceptable`). Instant
    listening only: downloads keep `matches` (D-130)."""
    if (
        not track.get("explicit_edition")
        and primary_artist(c.get("channel")) != primary_artist(track["artist"])
        and without_the(c.get("channel")) == without_the(track["artist"])
    ):
        # The artist differs only by a leading "The" (D-130): the rest of
        # the walk reads the result as by the requested artist.
        c = {**c, "channel": track["artist"]}
    failed = mismatch(track, c)
    if failed:
        return failed
    # The album names the same kind of recording ("The Remixes", "Demos",
    # "Live at Leeds" are those recordings even when the song title says
    # nothing) as the request.
    if flavour(c.get("album")) != recording_kind(track):
        return "album kind"
    if altered(c.get("title"), c.get("album")) != altered(
        track.get("title"), track.get("album")
    ):
        return "altered"
    # Only a plain studio song may come from another release: any release
    # when its length is known; when it is not (D-130), any ordinary album
    # naming no other recording the request does not name itself. A
    # live, demo, remix, session or altered recording is one performance
    # (another concert is another recording), so it must be the requested
    # album or carry one of its ISRCs; so must an edition chosen explicitly.
    if not (
        (
            not track.get("explicit_edition")
            and not recording_kind(track)
            and not altered(track.get("title"), track.get("album"))
            and (
                track.get("duration")
                or ordinary(c.get("album"))
                and markers(c.get("album"))
                <= markers(track.get("title"), track.get("album"))
            )
        )
        or normalise(c.get("album", "")) == normalise(track["album"])
        or bool(track.get("isrcs") and c.get("isrc") and c["isrc"] in track["isrcs"])
    ):
        return "album"
    if not (not track.get("isrcs") or not c.get("isrc") or c["isrc"] in track["isrcs"]):
        return "isrc"
    return None


def why(track, c):
    """`rejection` for diagnostics: "matched" for the song."""
    return rejection(track, c) or "matched"


# What a no match records when its detail could not be made (#282).
UNRECORDED = '{"unrecorded":true}'
# A length beyond this is no length (seconds; a day).
LONGEST = 86400


def text_of(value):
    """Any value as text SQLite can store: lone surrogates and other
    unencodable characters become "?"."""
    text = str(value if value is not None else "")
    return text.encode("utf-8", "replace").decode("utf-8")


def no_match_detail(track, query, results):
    """What a source's search gave for a song it had no match for, for the
    performance event (#282): the query, the number of results, every
    result's reason counted and the first few. Song names only, never who
    asked; JSON, always whole within DETAIL_CHARS. Best effort: anything it
    cannot read becomes null or UNRECORDED, never an error."""
    try:

        def cut(value, size):
            value = text_of(value)
            return value if len(value) <= size else value[: size - 1] + "…"

        def seconds(value):
            try:
                found = duration(value)
                return round(found) if 0 < found <= LONGEST else None
            except Exception:
                return None

        def reason(c):
            try:
                return why(track, c)
            except Exception:
                return "unknown"

        reasons = [reason(c) for c in results]
        value = {
            "query": cut(query, 120),
            "seconds": seconds(track.get("duration")),
            "results": len(results),
            # Every result's reason, counted: a near miss below the first few.
            "reasons": dict(Counter(reasons)),
            "top": [
                {
                    "title": cut(c.get("title"), 60),
                    "artist": cut(c.get("channel") or c.get("artist"), 40),
                    "album": cut(c.get("album"), 40),
                    "seconds": seconds(c.get("duration")),
                    "why": why,
                }
                for c, why in zip(results[:DETAIL_RESULTS], reasons)
            ],
        }

        def encoded():
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

        text = encoded()
        while len(text) > DETAIL_CHARS and value["top"]:
            value["top"].pop()
            text = encoded()
        if len(text) > DETAIL_CHARS:
            value["query"] = cut(query, 40)
            text = encoded()
        return text if len(text) <= DETAIL_CHARS else UNRECORDED
    except Exception:
        return UNRECORDED


class NoMatch(ValueError):
    """A source searched and had no match. It keeps what the search found;
    the detail is made later, by `missed`, so making it can never change
    what the lookup answers."""

    def __init__(self, message, track=None, query=None, results=None, detail=None):
        super().__init__(message)
        self.searched = (track, query, results) if results is not None else None
        self.detail = detail


def missed(exc):
    """The detail for a "no match" step: what the search found, or, when a
    source counted as no match without searching to the end, why. Never
    raises."""
    try:
        if getattr(exc, "detail", None):
            return text_of(exc.detail)[:DETAIL_CHARS]
        if getattr(exc, "searched", None):
            return no_match_detail(*exc.searched)
        return json.dumps({"error": type(exc).__name__})
    except Exception:
        return UNRECORDED


class Resolving:
    """A JioSaavn lookup in flight: a look-ahead's is `speculative`, and
    `started` once it has its budget."""

    __slots__ = ("task", "started", "speculative")

    def __init__(self, speculative):
        self.task = None
        self.started = False
        self.speculative = speculative


class RemoteAudio:
    """Short-lived source resolution cache; remote listening never queues a download."""

    def __init__(self, search=None, youtube=None):
        self.search = search or search_jiosaavn
        # YouTube for instant listening (#193), asked together with JioSaavn,
        # which wins when both have the song (D-89).
        self.youtube = youtube
        # Event-loop owned: {key: (keep until, video ID)}, oldest use first.
        self.videos = OrderedDict()
        # Look-ahead's YouTube budget: one song at a time, across everyone.
        self.youtube_ahead = asyncio.Semaphore(1)
        # {key: keep until}: songs JioSaavn has no match for.
        self.unmatched = {}
        # {key: (keep until, address, checked at)}, oldest use first.
        self.cache = OrderedDict()
        self.clock = time.monotonic
        # JioSaavn lookups for taps.
        self.slots = asyncio.Semaphore(3)
        # Look-ahead has its own JioSaavn budget, so a tap never waits behind
        # songs nobody tapped (Codex, #192 round 2); taps keep `slots`.
        self.speculative_slots = asyncio.Semaphore(LOOKAHEAD_SLOTS)
        # Event-loop owned: {key: Resolving} JioSaavn lookups running; a tap
        # joins one that is searching and overtakes one still waiting.
        self.resolving = {}
        # {name: (monotonic expiry, exception type, message)}
        self.misses = {}
        # Event-loop owned: {token: (monotonic expiry, YouTubeStream, user ID)}
        # for the streams the server passes through, in issue order, and
        # {user ID: deque of that person's tokens}, in the same order.
        self.streams = OrderedDict()
        self.held = {}
        # Counts each source's answer for the parity targets (#191); set by
        # the app.
        self.performance = None
        self.inflight = {}

    @staticmethod
    def key(track):
        return (
            track.get("musicbrainz_release_id"),
            track.get("musicbrainz_recording_id"),
            track["artist"],
            track["title"],
            track["album"],
            track.get("duration"),
            # Every constraint resolution applies, so a cached answer for an
            # ordinary request never serves a stricter one (Codex, 5 Nov).
            bool(track.get("explicit_edition")),
            tuple(sorted(track.get("isrcs") or [])),
            tuple(sorted(recording_kind(track))),
        )

    async def stream(self, client, track, user_id):
        """The first source with a confident match: JioSaavn's address, or a
        token for YouTube's audio, which the server passes through."""
        found = await self.shared(client, track)
        if "url" in found:
            return found
        now = time.monotonic()
        # Every token lives TOKEN_SECONDS, so the oldest issued expires first:
        # only expired ones are taken, from the front (Codex, #201 round 1).
        while self.streams and next(iter(self.streams.values()))[0] <= now:
            self.forget(next(iter(self.streams)))
        held = self.held.setdefault(user_id, deque())
        if len(held) >= STREAMS_PER_PERSON:
            self.forget(held[0])
        if len(self.streams) >= STREAMS:
            self.forget(next(iter(self.streams)))
        token = secrets.token_urlsafe(24)
        # Played through the server: the address is tied to its network.
        target = YouTubeStream(self.youtube, found["youtube"], found["address"], client)
        self.streams[token] = (now + TOKEN_SECONDS, target, user_id)
        self.held.setdefault(user_id, deque()).append(token)
        return {"token": token, "source": found["source"]}

    def forget(self, token):
        """Drop a token. Each person's tokens and the whole map are both in
        issue order, so a token taken from either front is its owner's oldest."""
        _, _, user_id = self.streams.pop(token)
        held = self.held[user_id]
        if held and held[0] == token:
            held.popleft()
        else:  # Never expected; kept correct rather than fast.
            held.remove(token)
        if not held:
            del self.held[user_id]

    def streamed(self, token, user_id):
        """The stream behind a token, only for the person it was made for."""
        entry = self.streams.get(token)
        if not entry or entry[0] <= time.monotonic() or entry[2] != user_id:
            return None
        return entry[1]

    def prepare(self, client, tracks):
        """Warm the cache for songs about to play; nobody waits for it."""
        for track in tracks:
            task = asyncio.ensure_future(self.shared(client, track))
            task.add_done_callback(lambda t: t.cancelled() or t.exception())
        return len(tracks)

    async def shared(self, client, track):
        # Playing a song that is being prepared joins that work, only when it
        # asks under the same constraints (`key`, as the provider caches): an
        # ordinary request's answer never serves an edition chosen explicitly.
        name = self.key(track)
        missed = self.misses.get(self.miss_key(track))
        if missed and missed[0] > time.monotonic():
            raise missed[1](missed[2])
        task = self.inflight.get(name)
        if task is None:
            task = asyncio.ensure_future(self.locate(client, track))
            self.inflight[name] = task
            task.add_done_callback(
                lambda done: self.inflight.get(name) is done
                and self.inflight.pop(name, None)
            )
            task.add_done_callback(lambda t: t.cancelled() or t.exception())
        return await shared_result(task)

    async def locate(self, client, track):
        try:
            return await self._locate(client, track)
        except ProviderError as exc:  # A provider down or limited: uncertain.
            self.remember_miss(track, TimeoutError, exc, FAILURE_SECONDS)
            raise TimeoutError(str(exc)) from exc
        except (ValueError, KeyError, TypeError) as exc:
            self.remember_miss(track, ValueError, exc, MISS_SECONDS)
            raise
        except (httpx.HTTPError, TimeoutError) as exc:
            self.remember_miss(track, type(exc), exc, FAILURE_SECONDS)
            raise

    def remember_miss(self, track, kind, exc, seconds):
        if len(self.misses) > 2000:
            now = time.monotonic()
            self.misses = {k: v for k, v in self.misses.items() if v[0] > now}
        # httpx errors need a request to be raised again; TimeoutError does not.
        if not issubclass(kind, (ValueError, TimeoutError)):
            kind = TimeoutError
        self.misses[self.miss_key(track)] = (
            time.monotonic() + seconds,
            kind,
            str(exc),
        )

    def miss_key(self, track):
        """A miss holds only for a request with the same constraints: an
        ISRC, a length or an edition another request lacks may still match."""
        constraints = [
            self.name(track),
            sorted(track.get("isrcs") or []),
            track.get("duration"),
            bool(track.get("explicit_edition")),
            track.get("recording_kind"),
        ]
        return hashlib.sha256(json.dumps(constraints, default=str).encode()).hexdigest()

    async def _locate(self, client, track):
        began = time.monotonic()
        # Where the time went, for the server log: playback waits for all of it.
        steps = []

        def step(name, outcome, since, detail=None):
            seconds = time.monotonic() - since
            steps.append(f"{name} {outcome} {seconds:.1f} s")
            if self.performance is not None:
                try:
                    self.performance.record(
                        "remote-source", name, outcome, seconds * 1000, detail
                    )
                except Exception:  # Counting never changes a lookup.
                    log.exception("Recording a remote source step failed")
            # The same steps in Server-Timing: "zvu4no-found", "jiosaavn-miss".
            outcome = {
                "no match": "miss",
                "too slow": "timeout",
            }.get(outcome, outcome)
            add_phase(f"{name}-{outcome}".lower(), seconds * 1000)

        # JioSaavn and YouTube are asked together; JioSaavn's 320 kbps copy
        # wins when both have the song (D-89).
        youtube = None
        if self.youtube is not None:
            youtube = asyncio.ensure_future(self.on_youtube(track))
            youtube.add_done_callback(lambda t: t.cancelled() or t.exception())
        try:
            url = await self.resolve(client, track)
        except (ValueError, KeyError, TypeError) as exc:
            failure = exc
        except (httpx.HTTPError, TimeoutError) as exc:
            failure = exc
        except BaseException:
            if youtube is not None:
                youtube.cancel()
            raise
        else:
            if youtube is not None:
                youtube.cancel()
            step("jiosaavn", "found", began)
            self.timing(track, "jiosaavn", began, steps)
            return {"url": url, "source": "jiosaavn"}
        step("jiosaavn", "no match", began, missed(failure))
        # Only a song every source said it does not have is a miss (404,
        # remembered long); a timeout or a provider error
        # leaves it uncertain (503, remembered briefly).
        uncertain = isinstance(failure, (httpx.HTTPError, TimeoutError, ProviderError))
        if youtube is not None:
            try:
                found = await shared_result(youtube)
            except ProviderError:  # Refused, down or answering oddly.
                step("youtube", "failed", began)
                uncertain = True
            except ValueError as exc:
                step("youtube", "no match", began, missed(exc))
            except (TimeoutError, httpx.HTTPError, OSError):
                step("youtube", "too slow", began)
                uncertain = True
            except Exception:  # A broken source must not stop the next.
                log.exception("YouTube lookup failed")
                step("youtube", "failed", began)
                uncertain = True
            else:
                step("youtube", "found", began)
                self.timing(track, "youtube", began, steps)
                return found
        self.timing(track, None, began, steps)
        if youtube is None:
            raise failure
        if uncertain:
            raise TimeoutError("A source did not answer. Try again shortly.")
        raise ValueError("No source has this recording.")

    @staticmethod
    def timing(track, source, began, steps):
        log.info(
            "Remote playback: %s - %s %s in %.1f s (%s)",
            track.get("artist"),
            track.get("title"),
            f"from {source}" if source else "unavailable",
            time.monotonic() - began,
            "; ".join(steps),
        )

    def name(self, track):
        # Players describe a catalogue song by its release and recording; the
        # other fields can differ slightly between a preparation and a play.
        if track.get("musicbrainz_release_id") and track.get(
            "musicbrainz_recording_id"
        ):
            key = [track["musicbrainz_release_id"], track["musicbrainz_recording_id"]]
        else:
            key = [normalise(track.get(k) or "") for k in ("artist", "title", "album")]
        return hashlib.sha256(json.dumps(key).encode()).hexdigest()[:32]

    async def match(self, client, track):
        """Look-ahead (#192): JioSaavn's match, then YouTube's, kept for the
        tap. A miss here is not remembered: the tap still asks every source."""
        missed = self.misses.get(self.miss_key(track))
        if missed and missed[0] > time.monotonic():
            return None
        try:
            return await self.resolve(client, track, speculative=True)
        except (ValueError, KeyError, TypeError, httpx.HTTPError, TimeoutError):
            if self.youtube is None:
                raise
        # YouTube's search and lookup, one look-ahead at a time, so taps keep
        # YouTube's slots (#193).
        async with self.youtube_ahead:
            return await self.on_youtube(track, speculative=True)

    async def on_youtube(self, track, speculative=False):
        """YouTube's copy of the song: the video, kept like JioSaavn's match,
        and its current address."""
        key = self.key(track)
        kept = self.videos.get(key)
        if kept and kept[0] > time.monotonic():
            self.videos.move_to_end(key)
            video = kept[1]
        else:
            songs = await self.youtube.songs(track, speculative)
            found = [c for c in songs if acceptable(track, c)]
            if not found:
                raise NoMatch(
                    "YouTube has no match for this song.",
                    track,
                    search_query(track),
                    songs,
                )
            wanted = album_title(track.get("album") or "", track.get("artist"))
            # An exact artist credit first (D-130), then the same album,
            # then a fixed order: the same song picks the same video every
            # time.
            video = min(
                found,
                key=lambda c: (
                    aliased(track, c),
                    album_title(c.get("album") or "", c.get("channel")) != wanted,
                    c["video_id"],
                ),
            )["video_id"]
            self.videos[key] = (time.monotonic() + RESOLVED_SECONDS, video)
            self.videos.move_to_end(key)
            while len(self.videos) > RESOLVED_ENTRIES:
                self.videos.popitem(last=False)
        address = await self.youtube.address(video)
        return {"youtube": video, "address": address, "source": "youtube"}

    async def resolve(self, client, track, speculative=False):
        # One JioSaavn lookup per song at a time. A tap joins a lookup that is
        # already searching, but overtakes a look-ahead still waiting for its
        # budget; a look-ahead joins whatever is running.
        key = self.key(track)
        running = self.resolving.get(key)
        if running is not None and (
            speculative or not running.speculative or running.started
        ):
            # Only a look-ahead still waiting for its turn is overtaken; taps
            # always share one lookup (Codex, #192 confirmation).
            return await shared_result(running.task)
        entry = Resolving(speculative)
        entry.task = asyncio.ensure_future(
            self._resolve(client, track, entry, speculative)
        )
        self.resolving[key] = entry

        def done(_, key=key, entry=entry):
            if self.resolving.get(key) is entry:
                del self.resolving[key]

        entry.task.add_done_callback(done)
        entry.task.add_done_callback(lambda t: t.cancelled() or t.exception())
        return await shared_result(entry.task)

    async def _resolve(self, client, track, entry=None, speculative=False):
        key = self.key(track)
        if speculative:
            async with self.speculative_slots:
                if self.resolving.get(key) is entry:
                    entry.started = True
                    # Its own budget only: never one of the taps' slots.
                    return await self._lookup(
                        client, track, key, contextlib.nullcontext()
                    )
            # A tap overtook this look-ahead. Only other look-aheads wait on
            # it, so it never waits in turn (waiting while holding the budget
            # could deadlock, Codex confirmation 3): a cached answer, or done.
            cached = self.cache.get(key)
            if cached and cached[0] > self.clock():
                return cached[1]
            raise ValueError("Looked up by a tap already.")
        if entry is not None:
            entry.started = True
        return await self._lookup(client, track, key, self.slots)

    async def _lookup(self, client, track, key, slots):
        cached = self.cache.get(key)
        now = self.clock()
        if cached and cached[0] > now:
            self.cache.move_to_end(key)
            if now - cached[2] < PROBE_SECONDS:
                return cached[1]
            try:
                await self.probe(client, cached[1])
            except ValueError:
                # The CDN no longer serves it: look the song up again.
                self.cache.pop(key, None)
            else:
                self.cache[key] = (cached[0], cached[1], self.clock())
                return cached[1]
        if self.unmatched.get(key, 0) > time.monotonic():
            raise NoMatch(
                "JioSaavn has no match for this song.",
                detail=json.dumps({"remembered": True}),
            )
        async with slots:
            query = search_query(track)
            try:
                results = await asyncio.wait_for(
                    asyncio.to_thread(self.search, query, 40),
                    timeout=5,
                )
            except RuntimeError as exc:
                # The vendored search wraps any failure, a network error
                # included: JioSaavn is uncertain, YouTube still answers.
                raise TimeoutError("JioSaavn search unavailable.") from exc
            candidates = [c for c in results if acceptable(track, c)]
            urls = {c.get("source_url"): c for c in candidates if c.get("source_url")}
            if not urls:
                self.rejected(track, results, 0)
                # Remembered so a tap on a song look-ahead found on YouTube
                # does not wait for JioSaavn to miss again.
                self.unmatched[key] = time.monotonic() + MISS_SECONDS
                if len(self.unmatched) > RESOLVED_ENTRIES:
                    now = time.monotonic()
                    self.unmatched = {
                        k: v for k, v in self.unmatched.items() if v > now
                    }
                raise NoMatch(
                    "No remote recording matched this song. Downloading remains available.",
                    track,
                    query,
                    results,
                )
            # Several copies of the recording: an exact artist credit first
            # (D-130), then the same album (without " - Single" and edition
            # words), then a matching ISRC, then a fixed order, so the same
            # song picks the same copy every time.
            wanted = album_title(track.get("album") or "", track.get("artist"))
            url = min(
                urls,
                key=lambda u: (
                    aliased(track, urls[u]),
                    album_title(urls[u].get("album") or "", urls[u].get("channel"))
                    != wanted,
                    not (track.get("isrcs") and urls[u].get("isrc") in track["isrcs"]),
                    u,
                ),
            )
            parsed = urlsplit(url)
            if (
                parsed.scheme != "https"
                or parsed.username
                or parsed.password
                or parsed.port not in {None, 443}
                or not (parsed.hostname or "").endswith(".saavncdn.com")
            ):
                raise ValueError("Remote source returned an unsupported audio address.")
            await self.probe(client, url)
            checked = self.clock()
            self.cache[key] = (checked + RESOLVED_SECONDS, url, checked)
            self.cache.move_to_end(key)
            while len(self.cache) > RESOLVED_ENTRIES:
                self.cache.popitem(last=False)
            return url

    @staticmethod
    async def probe(client, url):
        """The CDN serves audio at this address: a 32-byte range request."""
        async with client.stream(
            "GET",
            url,
            headers={"Range": "bytes=0-31", "Accept": "audio/*"},
            timeout=3,
            follow_redirects=False,
        ) as stream:
            if stream.status_code in {408, 429} or stream.status_code >= 500:
                # The CDN is busy or failing, not missing the song.
                raise TimeoutError("JioSaavn's audio did not answer.")
            if stream.status_code not in {200, 206} or not stream.headers.get(
                "content-type", ""
            ).split(";")[0].startswith(("audio/", "application/octet-stream")):
                raise ValueError("Remote source did not return an audio stream.")

    @staticmethod
    def rejected(track, results, matched):
        """Why JioSaavn's results did not give one address (REMOTE1: the same
        song matched on one run and not the next)."""
        reasons = []
        for c in results[:8]:
            if not matches(track, c):
                why = "not the recording"
            elif flavour(c.get("album")) != recording_kind(track) or altered(
                c.get("title"), c.get("album")
            ) != altered(track.get("title"), track.get("album")):
                why = "another recording (album " + repr(c.get("album", "")) + ")"
            elif (
                track.get("explicit_edition")
                or not track.get("duration")
                or recording_kind(track)
                or altered(track.get("title"), track.get("album"))
            ) and normalise(c.get("album", "")) != normalise(track["album"]):
                why = "album " + repr(c.get("album", ""))
            elif (
                track.get("isrcs") and c.get("isrc") and c["isrc"] not in track["isrcs"]
            ):
                why = "ISRC " + c["isrc"]
            else:
                why = "matched"
            reasons.append(
                f"{c.get('channel') or c.get('artist')} - {c.get('title')} "
                f"({c.get('duration')} s): {why}"
            )
        log.info(
            "JioSaavn: %s - %s (%s): %d results, %d addresses matched; %s",
            track.get("artist"),
            track.get("title"),
            track.get("album"),
            len(results),
            matched,
            "; ".join(reasons) or "no results",
        )
