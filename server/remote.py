"""Optional remote listening; matching never removes version descriptions."""

import asyncio
import contextvars
import hashlib
import json
import logging
import os
import secrets
import shutil
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from starlette.responses import Response, StreamingResponse

from .request_timing import add_phase, phase
from .library import normalise
from .providers.process import run_checked
from .downloader import FREEMP3CLOUD_RETRY_PAUSE, matches
from .matching import album_title, flavour, recording_kind
from .providers.contracts import ProviderError
from .vendor.jiosaavn import search_jiosaavn

log = logging.getLogger("pig_music.remote")

# After JioSaavn's direct stream, in coverage order (14 October 2026 test).
# Lossless sources are never streamed.
RELAYED = ("zvu4no", "freemp3cloud")
LABELS = {
    "jiosaavn": "JioSaavn",
    "zvu4no": "zvu4no",
    "freemp3cloud": "FreeMp3Cloud",
}
MEDIA_TYPES = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".opus": "audio/ogg"}
# Relayed songs stay for two weeks after they were last played, within 5 GB;
# the least recently played go first. A relay link lasts as long as a signed
# playback link.
RELAY_SECONDS = 14 * 86400
RELAY_BYTES = 5_000_000_000
TOKEN_SECONDS = 6 * 3600
# A song no source has is not searched for again for a while; a failure of a
# source or the network only briefly (REMOTE1: a miss took 30-35 s each time).
MISS_SECONDS = 30 * 60
FAILURE_SECONDS = 2 * 60
# Each relayed source's search gets this long, from when they all start;
# a slower one counts as no match.
SEARCH_SECONDS = 8
SOURCE_FAILURE_LIMIT = 2
SOURCE_COOLDOWN_SECONDS = 120
# A relayed MP3 plays while it arrives once its first bytes prove its length
# (#23). The head must do that within this many bytes (cover art included);
# a listener waits at most this long for bytes that have not arrived yet.
HEAD_BYTES = 1_000_000
WAIT_SECONDS = 20
CHUNK_BYTES = 65536
# The transfer a relay download may serve while it arrives (set per task).
LIVE = contextvars.ContextVar("pig_music_relay_live", default=None)


async def shared_result(task, timeout=None):
    """A caller may leave without cancelling shared work. Unlike shield in
    Python 3.14, this does not log an already-retrieved late failure."""
    done, _ = await asyncio.wait({task}, timeout=timeout)
    if not done:
        raise TimeoutError
    return task.result()


class RemoteAudio:
    """Short-lived source resolution cache; remote listening never queues a download."""

    def __init__(self, search=None, directory=None, runtime=None, relay_search=None):
        self.search = search or search_jiosaavn
        self.cache = {}
        # JioSaavn lookups and relays wait for their own slots: a lookup
        # never queues behind another song's transfer.
        self.slots = asyncio.Semaphore(3)
        self.relay_slots = asyncio.Semaphore(3)
        # {name: (monotonic expiry, exception type, message)}
        self.misses = {}
        # Other sources are fetched by the server into a short-lived cache and
        # served from this origin; without a directory only JioSaavn is used.
        self.directory = Path(directory) if directory else None
        self.runtime = runtime
        self.relay_search = relay_search
        self.relayed = {}
        self.inflight = {}
        # Event-loop owned: {name: RelayTransfer} still arriving and already
        # playing; another play of the recording joins it.
        self.transfers = {}
        # Event-loop owned: {(source, operation): (failure streak, cooldown expiry)}.
        # A successful search proves nothing about transfer access, and a
        # successful transfer must not reset another search's newer failures.
        self.source_health = {}

    async def search_source(self, source, track):
        began = time.monotonic()
        if any(
            self.source_health.get((source, operation), (0, 0))[1] > began
            for operation in ("search", "transfer")
        ):
            add_phase(f"{source}-cooldown", 0)
            raise TimeoutError("Source is cooling down.")
        search = asyncio.ensure_future(asyncio.to_thread(self.find, source, track))
        # A timed-out worker may still finish or raise. Always retrieve its result.
        search.add_done_callback(lambda t: t.cancelled() or t.exception())
        try:
            candidate = await shared_result(search, SEARCH_SECONDS)
        except (TimeoutError, ProviderError) as exc:
            self.source_failed(source, "search", exc)
            raise
        except ValueError:
            self.source_answered(source, "search", began)
            raise
        else:
            self.source_answered(source, "search", began)
            return candidate

    def source_failed(self, source, operation, exc):
        if not isinstance(exc, TimeoutError) and not (
            isinstance(exc, ProviderError) and exc.code == "access_required"
        ):
            return
        key = (source, operation)
        failures, until = self.source_health.get(key, (0, 0))
        failures += 1
        if failures >= SOURCE_FAILURE_LIMIT:
            now = time.monotonic()
            if until <= now:
                log.warning(
                    "Remote source %s cooling down for %d s (%s %s)",
                    source,
                    SOURCE_COOLDOWN_SECONDS,
                    operation,
                    "timeout" if isinstance(exc, TimeoutError) else exc.code,
                )
            until = now + SOURCE_COOLDOWN_SECONDS
        self.source_health[key] = (failures, until)

    def source_answered(self, source, operation, began):
        # An older concurrent success cannot undo a newly opened cooldown.
        key = (source, operation)
        failures, until = self.source_health.get(key, (0, 0))
        if until <= began:
            self.source_health.pop(key, None)
            if failures >= SOURCE_FAILURE_LIMIT:
                log.info("Remote source %s healthy again (%s)", source, operation)

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
        relay token for audio the server fetched from another source."""
        found = await self.shared(client, track)
        if "url" in found:
            return found
        token = secrets.token_urlsafe(24)
        self.relayed[token] = (
            time.monotonic() + TOKEN_SECONDS,
            found.get("transfer") or found["path"],
            user_id,
        )
        return {"relay": token, "source": found["source"]}

    def prepare(self, client, tracks):
        """Warm the cache for songs about to play; nobody waits for it."""
        for track in tracks:
            task = asyncio.ensure_future(self.shared(client, track))
            task.add_done_callback(lambda t: t.cancelled() or t.exception())
        return len(tracks)

    async def shared(self, client, track):
        # Playing a song that is being prepared joins that work.
        name = self.name(track)
        missed = self.misses.get(self.miss_key(track))
        if missed and missed[0] > time.monotonic():
            raise missed[1](missed[2])
        task = self.inflight.get(name)
        if task is None:
            task = asyncio.ensure_future(self.locate(client, track))
            self.inflight[name] = task
            task.add_done_callback(lambda _: self.inflight.pop(name, None))
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
        # A copy already relayed for this recording plays at once, before any
        # provider is asked again.
        if self.directory is not None:
            with phase("relay-cache"):
                found = await asyncio.to_thread(self.cached, track)
            if found:
                return {"path": found[0], "source": found[1]}
            live = self.transfers.get(self.name(track))
            if live is not None and live.error is None and not live.complete:
                add_phase("relay-joined", 0)
                return {"transfer": live, "source": live.source}
        began = time.monotonic()
        # Where the time went, for the server log: playback waits for all of it.
        steps = []

        def step(name, outcome, since):
            seconds = time.monotonic() - since
            steps.append(f"{name} {outcome} {seconds:.1f} s")
            # The same steps in Server-Timing: "zvu4no-found", "jiosaavn-miss".
            outcome = {
                "no match": "miss",
                "transfer failed": "failed",
                "too slow": "timeout",
            }.get(outcome, outcome)
            add_phase(f"{name}-{outcome}".lower(), seconds * 1000)

        try:
            url = await self.resolve(client, track)
            step("jiosaavn", "found", began)
            self.timing(track, "jiosaavn", began, steps)
            return {"url": url, "source": "jiosaavn"}
        except (ValueError, KeyError, TypeError) as exc:
            failure = exc
        except (httpx.HTTPError, TimeoutError) as exc:
            failure = exc
        step("jiosaavn", "no match", began)
        if self.directory is None:
            self.timing(track, None, began, steps)
            raise failure
        # Only a song every source said it does not have is a miss (404,
        # remembered long); a timeout, a provider error or a failed transfer
        # leaves it uncertain (503, remembered briefly).
        uncertain = isinstance(failure, (httpx.HTTPError, TimeoutError, ProviderError))
        # A transfer that plays while it arrives keeps its slot until it ends.
        await self.relay_slots.acquire()
        handed_off = False
        try:
            # Every source searches at once; the best in priority order wins
            # and only it is transferred.
            searched = time.monotonic()
            searches = [
                asyncio.ensure_future(self.search_source(source, track))
                for source in RELAYED
            ]
            for search in searches:
                search.add_done_callback(lambda t: t.cancelled() or t.exception())
            for source, search in zip(RELAYED, searches):
                try:
                    candidate = await shared_result(search)
                except TimeoutError:
                    step(source, "too slow", searched)
                    uncertain = True
                    continue
                except ProviderError:  # Down, limited or answering oddly.
                    step(source, "failed", searched)
                    uncertain = True
                    continue
                except ValueError:
                    step(source, "no match", searched)
                    continue
                except Exception:  # One broken source must not stop the next.
                    step(source, "failed", searched)
                    uncertain = True
                    continue
                step(source, "found", searched)
                fetched = time.monotonic()
                live = RelayTransfer(source)
                context = LIVE.set(live)
                try:
                    transfer = asyncio.ensure_future(
                        asyncio.to_thread(self.download, source, track, candidate)
                    )
                finally:
                    LIVE.reset(context)
                transfer.add_done_callback(lambda t: t.cancelled() or t.exception())
                await asyncio.wait(
                    {transfer, live.ready}, return_when=asyncio.FIRST_COMPLETED
                )
                if not transfer.done():
                    # Its head proved the recording and its length: it plays
                    # while the rest arrives (#23).
                    step(source, "streaming", fetched)
                    self.timing(track, source, began, steps)
                    handed_off = True
                    name = self.name(track)
                    self.transfers[name] = live
                    transfer.add_done_callback(
                        lambda t: self.relay_finished(t, live, track, name, fetched)
                    )
                    return {"transfer": live, "source": source}
                try:
                    path = transfer.result()
                except Exception as exc:
                    self.source_failed(source, "transfer", exc)
                    step(source, "transfer failed", fetched)
                    uncertain = True
                    continue
                step(source, "transfer", fetched)
                self.source_answered(source, "transfer", fetched)
                self.timing(track, source, began, steps)
                return {"path": path, "source": source}
        finally:
            if not handed_off:
                self.relay_slots.release()
        self.timing(track, None, began, steps)
        if uncertain:
            raise TimeoutError("A source did not answer. Try again shortly.")
        raise ValueError("No source has this recording.")

    def relay_finished(self, task, live, track, name, fetched):
        """A transfer that was already playing ended: its health counts as
        any transfer's, and its listeners learn how it ended."""
        self.relay_slots.release()
        if self.transfers.get(name) is live:
            del self.transfers[name]
        seconds = time.monotonic() - fetched
        exc = (
            ConnectionError("The relay transfer stopped.")
            if task.cancelled()
            else task.exception()
        )
        if exc is None:
            self.source_answered(live.source, "transfer", fetched)
            live.finish(task.result())
            log.info(
                "Remote relay: %s - %s from %s arrived in %.1f s",
                track.get("artist"),
                track.get("title"),
                live.source,
                seconds,
            )
            return
        self.source_failed(live.source, "transfer", exc)
        live.fail(exc)
        log.warning(
            "Remote relay: %s - %s from %s failed while playing after %.1f s (%s)",
            track.get("artist"),
            track.get("title"),
            live.source,
            seconds,
            exc.code if isinstance(exc, ProviderError) else type(exc).__name__,
        )

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

    def cached(self, track):
        """A relayed copy of this recording that has not expired, marked as
        just played so the cache keeps what people listen to."""
        if not self.directory or not self.directory.is_dir():
            return None
        for path in self.directory.glob(self.name(track) + ".*"):
            parts = path.name.split(".")
            if (
                len(parts) != 3
                or parts[1] not in RELAYED
                or path.suffix not in MEDIA_TYPES
            ):
                continue
            if time.time() - path.stat().st_mtime > RELAY_SECONDS:
                continue
            os.utime(path)
            return path, parts[1]
        return None

    def relay(self, token, user_id):
        """The cached file behind a token, only for the person it was made for."""
        entry = self.relayed.get(token)
        if not entry or entry[0] <= time.monotonic() or entry[2] != user_id:
            return None
        path = entry[1]
        if isinstance(path, RelayTransfer):
            if path.error is not None:
                return None
            if not path.complete:
                return path
            path = path.final
        return (path, MEDIA_TYPES[path.suffix]) if path.is_file() else None

    def fetch(self, source, track):
        return self.download(source, track, self.find(source, track))

    @staticmethod
    def context(source):
        from .providers.contracts import Context

        context = Context("native_standard", deadline=time.monotonic() + 30)
        context.max_bytes = 40_000_000
        return context

    def find(self, source, track):
        """This source's best confident copy of the recording."""
        from .providers import REGISTRY
        from .providers.legacy import search as legacy_search

        context = self.context(source)
        if self.relay_search:
            candidates = self.relay_search(source, track)
        elif REGISTRY[source].adapter:
            candidates = self.runtime.search(source, track, context)
        else:
            query = track["artist"] + " " + track["title"]
            candidates = legacy_search(source, query, context)
            if not candidates and source == "freemp3cloud":
                # It silently answers empty when busy; acquisition retries once too.
                time.sleep(FREEMP3CLOUD_RETRY_PAUSE)
                candidates = legacy_search(source, query, context)
        # Acquisition's identity rules decide the match. These sites list one
        # recording several times, so take the best copy as downloads do:
        # formats every Apple player opens first, then the higher bitrate.
        found = [c for c in candidates if matches(track, c)]
        found = [c for c in found if REGISTRY[source].extension(c) in MEDIA_TYPES]
        if not found:
            raise ValueError("No recording matched.")
        candidate = min(
            found,
            key=lambda c: (
                REGISTRY[source].extension(c) == ".opus",
                c.get("quality") != "320kbps",
            ),
        )
        return candidate

    def download(self, source, track, candidate):
        from .audio import probe_file
        from .providers import REGISTRY
        from .providers.network import transfer

        context = self.context(source)
        extension = REGISTRY[source].extension(candidate)
        self.prune()
        self.directory.mkdir(parents=True, exist_ok=True)
        # Fetched under a private name, then published under the recording's.
        path = self.directory / ("." + secrets.token_hex(16) + extension)
        final = self.directory / f"{self.name(track)}.{source}{extension}"
        expected = float(track.get("duration") or 0)
        live = LIVE.get()
        if live is not None and extension == ".mp3":
            live.arriving(path, final, expected)
            context.progress = live.progress
        else:
            live = None
        try:
            if REGISTRY[source].adapter:
                self.runtime.transfer(source, candidate, path, context)
            else:
                transfer(
                    candidate["source_url"],
                    REGISTRY[source].download_hosts,
                    path,
                    context,
                )
            probe = probe_file(path)
            if not probe or (expected and abs(probe["duration"] - expected) > 5):
                raise ValueError("The remote file was not the expected recording.")
            # A file already playing keeps its bytes: its head carried the
            # length, and a listener's next range must find the same audio.
            if extension == ".mp3" and not (live and live.playing):
                add_length_header(path)
            path.replace(final)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return final

    def prune(self):
        """Drop expired and unfinished copies, then the least recently played
        until the cache fits its budget."""
        if not self.directory or not self.directory.is_dir():
            return
        now = time.time()
        files = []
        for path in self.directory.iterdir():
            if not path.is_file():
                continue
            stat = path.stat()
            unfinished = path.name.startswith(".") and now - stat.st_mtime > 3600
            if unfinished or now - stat.st_mtime > RELAY_SECONDS:
                path.unlink(missing_ok=True)
            elif not path.name.startswith("."):
                files.append((stat.st_mtime, stat.st_size, path))
        total = sum(size for _, size, _ in files)
        for _, size, path in sorted(files):
            if total <= RELAY_BYTES:
                break
            path.unlink(missing_ok=True)
            total -= size
        live = time.monotonic()
        self.relayed = {k: v for k, v in self.relayed.items() if v[0] > live}

    async def resolve(self, client, track):
        key = self.key(track)
        cached = self.cache.get(key)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        async with self.slots:
            try:
                results = await asyncio.wait_for(
                    asyncio.to_thread(
                        self.search, track["artist"] + " " + track["title"], 40
                    ),
                    timeout=5,
                )
            except RuntimeError as exc:
                # The vendored search wraps any failure, a network error
                # included: JioSaavn is uncertain, the relays still answer.
                raise TimeoutError("JioSaavn search unavailable.") from exc
            # The shared song rule (`matches`: lead artist, title, live/remix/
            # demo words in title and album, length within 5 s, agreeing IDs)
            # decides; a song is the same on any edition (PRODUCT), so the
            # album must agree only for an edition chosen explicitly.
            candidates = [
                c
                for c in results
                if matches(track, c)
                # The album names the same kind of recording ("The Remixes",
                # "Demos", "Live at Leeds" are those recordings even when the
                # song title says nothing) as the request.
                and flavour(c.get("album")) == recording_kind(track)
                and (
                    not track.get("explicit_edition")
                    or normalise(c.get("album", "")) == normalise(track["album"])
                )
                and (
                    not track.get("isrcs")
                    or not c.get("isrc")
                    or c["isrc"] in track["isrcs"]
                )
            ]
            urls = {c.get("source_url"): c for c in candidates if c.get("source_url")}
            if not urls:
                self.rejected(track, results, 0)
                raise ValueError(
                    "No remote recording matched this song. Downloading remains available."
                )
            # Several copies of the recording: the same album first (without
            # " - Single" and edition words), then a matching ISRC, then a
            # fixed order, so the same song picks the same copy every time.
            wanted = album_title(track.get("album") or "", track.get("artist"))
            url = min(
                urls,
                key=lambda u: (
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
            if len(self.cache) > 200:
                self.cache.clear()
            self.cache[key] = (time.monotonic() + 90, url)
            return url

    @staticmethod
    def rejected(track, results, matched):
        """Why JioSaavn's results did not give one address (REMOTE1: the same
        song matched on one run and not the next)."""
        reasons = []
        for c in results[:8]:
            if not matches(track, c):
                why = "not the recording"
            elif track.get("explicit_edition") and normalise(
                c.get("album", "")
            ) != normalise(track["album"]):
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


def add_length_header(path):
    """Rewrite a relayed MP3 with an Xing/Info length header, copying the audio
    as it is (never re-encoded). Players otherwise estimate a VBR file's length
    from its first frames, which can show double the real time and mis-seek."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return
    fixed = path.with_name(path.name + ".fixed.mp3")
    try:
        run_checked(
            [
                ffmpeg,
                "-nostdin",
                "-v",
                "error",
                "-i",
                str(path),
                "-map",
                "0:a:0",
                "-c:a",
                "copy",
                "-write_xing",
                "1",
                "-f",
                "mp3",
                "-y",
                str(fixed),
            ],
            lambda: None,
            timeout=60,
        )
        if fixed.stat().st_size > 0:
            fixed.replace(path)
    except Exception:  # The original still plays; only its length estimate suffers.
        pass
    finally:
        fixed.unlink(missing_ok=True)


# Layer III bitrates (kbps) by MPEG version, and sample rates by version bits.
MP3_BITRATES = {
    1: (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
    2: (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
}
MP3_RATES = {
    3: (44100, 48000, 32000),
    2: (22050, 24000, 16000),
    0: (11025, 12000, 8000),
}


class NotProgressive(Exception):
    """The head cannot prove the file's length; it arrives whole first."""


def mp3_frame(data, at):
    """An MPEG Layer III frame header at `at`, or None if more bytes are needed."""
    if at + 4 > len(data):
        return None
    b = data[at : at + 4]
    version, layer = (b[1] >> 3) & 3, (b[1] >> 1) & 3
    bitrate_index, rate_index = b[2] >> 4, (b[2] >> 2) & 3
    if (
        b[0] != 0xFF
        or b[1] & 0xE0 != 0xE0
        or version == 1
        or layer != 1
        or bitrate_index in (0, 15)
        or rate_index == 3
    ):
        raise NotProgressive("No MP3 frame where one was expected.")
    mpeg1 = version == 3
    bitrate = MP3_BITRATES[1 if mpeg1 else 2][bitrate_index] * 1000
    rate = MP3_RATES[version][rate_index]
    samples = 1152 if mpeg1 else 576
    mono = b[3] >> 6 == 3
    return {
        "bitrate": bitrate,
        "rate": rate,
        "samples": samples,
        "length": samples // 8 * bitrate // rate + ((b[2] >> 1) & 1),
        "side": (17 if mono else 32) if mpeg1 else (9 if mono else 17),
    }


def mp3_head(data, size):
    """The playing time an MP3's first bytes prove for a file of `size` bytes,
    from its Xing/Info or VBRI frame count: None when more bytes are needed;
    NotProgressive when the head cannot prove it. Matching early frames never
    prove a constant bitrate, so a file without such a header arrives whole."""
    start = 0
    if data[:3] == b"ID3":
        if len(data) < 10:
            return None
        tag = data[6:10]
        if any(byte & 0x80 for byte in tag):
            raise NotProgressive("Malformed ID3 tag.")
        start = 10 + (tag[0] << 21 | tag[1] << 14 | tag[2] << 7 | tag[3])
        if data[5] & 0x10:
            start += 10
    first = mp3_frame(data, start)
    if first is None or len(data) < start + 64:
        return None
    xing = start + 4 + first["side"]
    audio = size - start
    if data[xing : xing + 4] in (b"Xing", b"Info"):
        flags = data[xing + 7]
        if not flags & 1:
            raise NotProgressive("Length header without a frame count.")
        frames = int.from_bytes(data[xing + 8 : xing + 12], "big")
        stated = (
            int.from_bytes(data[xing + 12 : xing + 16], "big") if flags & 2 else None
        )
        seconds = frames * first["samples"] / first["rate"]
        # Info declares a constant bitrate: the declared size must give the
        # same length the frame count does.
        if (
            data[xing : xing + 4] == b"Info"
            and abs(audio * 8 / first["bitrate"] - seconds) > 1
        ):
            raise NotProgressive("A constant bitrate the size disagrees with.")
    elif data[start + 36 : start + 40] == b"VBRI":
        stated = int.from_bytes(data[start + 46 : start + 50], "big")
        frames = int.from_bytes(data[start + 50 : start + 54], "big")
        seconds = frames * first["samples"] / first["rate"]
    else:
        raise NotProgressive("No length header.")
    # The header's own byte count, when given, must match the declared size
    # (trailing tags allowed).
    if stated is not None and abs(stated - audio) > max(4096, audio // 100):
        raise NotProgressive("A length header for another size.")
    return seconds


class RelayTransfer:
    """A relay download that plays while it arrives (#23). The worker thread
    only reports progress; every listener-facing field is owned by the loop."""

    def __init__(self, source):
        self.source = source
        self.loop = asyncio.get_running_loop()
        self.ready = self.loop.create_future()
        self.size = 0
        self.arrived = 0
        self.complete = False
        self.error = None
        self.media_type = MEDIA_TYPES[".mp3"]
        self._changed = asyncio.Event()
        # Worker-thread fields, set before the transfer starts.
        self.path = self.final = None
        self.expected = 0
        self.playing = False
        self.decided = False

    # Worker thread.

    def arriving(self, path, final, expected):
        self.path, self.final, self.expected = path, final, expected

    def progress(self, received, total):
        """After each written chunk. The head is checked once, before any
        byte is served: a recording of another length stops the transfer."""
        if total and received > total:
            # More than the source declared: not the file whose length was
            # promised to listeners.
            raise ProviderError("invalid_response")
        try:
            arrived = os.stat(self.path).st_size
        except OSError:
            return
        if self.playing:
            self._post(self._grew, arrived)
            return
        if self.decided:
            return
        if not total:
            self.decided = True  # No declared size, so no Content-Length to give.
            return
        with open(self.path, "rb") as head:
            data = head.read(min(arrived, HEAD_BYTES))
        try:
            seconds = mp3_head(data, total)
        except NotProgressive:
            self.decided = True
            return
        if seconds is None:
            self.decided = arrived >= HEAD_BYTES
            return
        self.decided = True
        if self.expected and abs(seconds - self.expected) > 5:
            raise ValueError("The remote file was not the expected recording.")
        self.playing = True
        self._post(self._started, total, arrived)

    def _post(self, callback, *args):
        try:
            self.loop.call_soon_threadsafe(callback, *args)
        except RuntimeError:  # The server is stopping.
            pass

    def read(self, position, count):
        """Bytes already written. Each read opens the file afresh, so a
        cancelled response holds no descriptor; a finished transfer has moved
        to its published name (the same file, so the same bytes)."""
        for path in (self.path, self.final):
            try:
                descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            except FileNotFoundError:
                continue
            try:
                return os.pread(descriptor, count, position)
            finally:
                os.close(descriptor)
        return b""

    # Event loop.

    def _started(self, size, arrived):
        self.size = size
        self._grew(arrived)
        if not self.ready.done():
            self.ready.set_result(None)

    def _grew(self, arrived):
        self.arrived = max(self.arrived, min(arrived, self.size))
        self._changed.set()
        self._changed = asyncio.Event()

    def finish(self, final):
        self.final = final
        self.complete = True
        self._grew(self.size)

    def fail(self, exc):
        self.error = exc
        self._grew(0)

    @property
    def servable(self):
        """Bytes a listener may have: the file's last byte only once the whole
        file has been validated and published, so no body completes for a
        file that was then rejected."""
        if self.error is not None:
            return 0
        return self.arrived if self.complete else min(self.arrived, self.size - 1)

    def limit(self, end):
        """Bytes a response ending at byte `end` may send. Until the whole
        file is validated and published, a response holds back its own last
        byte, so no body completes for audio later rejected. Only a player's
        two-byte probe (`bytes=0-1`, a range ending by byte 1) completes at
        once: no header byte count is trusted to say where audio begins."""
        if self.complete or end <= 1:
            return min(self.servable, end + 1)
        return min(self.servable, end)

    async def until(self, position, seconds, end):
        """Whether byte `position` of a response ending at `end` may be served
        within `seconds`; never once the transfer has failed."""
        deadline = self.loop.time() + seconds
        while self.error is None and self.limit(end) <= position and not self.complete:
            remaining = deadline - self.loop.time()
            if remaining <= 0:
                return False
            try:
                await asyncio.wait_for(self._changed.wait(), remaining)
            except TimeoutError:
                return False
        return self.limit(end) > position

    async def response(self, method, range_header):
        size = self.size
        try:
            span = byte_range(range_header, size)
        except NotProgressive:
            return Response(
                status_code=416, headers={"Content-Range": f"bytes */{size}"}
            )
        start, end = span or (0, size - 1)
        headers = {
            "Accept-Ranges": "bytes",
            "Cache-Control": "private, no-store",
            "Content-Length": str(end - start + 1),
            "Content-Type": self.media_type,
        }
        status = 200
        if span:
            status = 206
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        if method == "HEAD":
            return Response(status_code=status, headers=headers)
        # A range past what has arrived waits for its first byte before any
        # header is sent, so running out of time is a clean, retryable answer.
        if not await self.until(start, WAIT_SECONDS, end):
            if self.error is not None:
                return Response(status_code=404, headers={"Cache-Control": "no-store"})
            return Response(
                status_code=503,
                headers={"Retry-After": "2", "Cache-Control": "no-store"},
            )
        return GrowingResponse(self, start, end, status, headers)


def byte_range(header, size):
    """One `bytes=` range as (first, last); None for the whole file (several
    ranges or a malformed header are answered whole, as HTTP allows)."""
    if not header:
        return None
    unit, _, spec = header.partition("=")
    first, dash, last = spec.strip().partition("-")
    first, last = first.strip(), last.strip()
    if (
        unit.strip().lower() != "bytes"
        or not dash
        or "," in spec
        or not (first or last)
        or (first and not first.isdigit())
        or (last and not last.isdigit())
    ):
        return None
    if not first:
        if int(last) == 0:
            raise NotProgressive("Empty suffix range.")
        return max(0, size - int(last)), size - 1
    start = int(first)
    end = min(int(last), size - 1) if last else size - 1
    if start >= size or end < start:
        raise NotProgressive("Range outside the file.")
    return start, end


class GrowingResponse(StreamingResponse):
    """A range of a relay file served as its bytes arrive, never past them.
    Its own last byte waits for the validated file (see RelayTransfer.limit); a failed, rejected or
    stalled transfer ends the body short of its length, which closes the
    connection rather than presenting a whole song."""

    def __init__(self, transfer, start, end, status, headers):
        self.transfer, self.start, self.end = transfer, start, end
        super().__init__(iter(()), status_code=status, headers=headers)

    async def stream_response(self, send):
        await send(
            {
                "type": "http.response.start",
                "status": self.status_code,
                "headers": self.raw_headers,
            }
        )
        transfer, position = self.transfer, self.start
        while position <= self.end:
            if not await transfer.until(position, WAIT_SECONDS, self.end):
                return
            count = min(
                CHUNK_BYTES,
                self.end + 1 - position,
                transfer.limit(self.end) - position,
            )
            data = await asyncio.to_thread(transfer.read, position, count)
            if not data or transfer.error is not None:
                return
            position += len(data)
            await send({"type": "http.response.body", "body": data, "more_body": True})
        await send({"type": "http.response.body", "body": b"", "more_body": False})
