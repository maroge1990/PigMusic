"""Concurrent, recoverable acquisition with guarded NAS publication.

Source parsers are reused from Music Grabber; transfer, validation, tagging
and publication are app-owned. Preview/test storage remains isolated by default.
"""

import asyncio
import base64
import hashlib
import logging
import math
import os
import re
import shutil
import time
import threading
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from mutagen.id3 import (
    APIC,
    ID3,
    ID3NoHeaderError,
    TALB,
    TDRC,
    TIT2,
    TPE1,
    TPE2,
    TPOS,
    TRCK,
    TXXX,
)
from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm
from mutagen.flac import FLAC, Picture
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis
from .lossless_check import lossy_cutoff
from .soulseek import Soulseek

from .audio import probe_file, validate_streams
from .providers import REGISTRY, SEARCH_PROVIDERS, supports
from .providers.process import run_checked
from .providers.contracts import Context, ProviderError
from .providers.runtime import Runtime
from .providers.legacy import search as legacy_search
from .providers.network import transfer as http_transfer
from .matching import ACQUISITION_TOLERANCE, primary_artist, recording_kind
from .matching import title_key
from .quality import CODEC_EXTENSIONS, policy, satisfies
from .storage import download_root, file_sha256, library_root

PROVIDERS = SEARCH_PROVIDERS
log = logging.getLogger("pig_music.downloads")
# Lossy profiles a verified lossless original may be converted down to
# (D-92): encoder, container and bitrate.
CONVERT_TO = {
    "mp3_320": ("libmp3lame", ".mp3", "320k"),
    "aac_256": ("aac", ".m4a", "256k"),
}
VERSION_WORDS = {
    "live",
    "remix",
    "remaster",
    "remastered",
    "acoustic",
    "demo",
    "karaoke",
    "instrumental",
    "cover",
}
# Words naming another edition of the same recording, not another recording.
EDITION_WORDS = {"remaster", "remastered"}


def folded(text):
    return " ".join(
        re.findall(
            r"\w+",
            "".join(
                c
                for c in unicodedata.normalize("NFKD", text or "").casefold()
                if not unicodedata.combining(c)
            ),
        )
    )


def duration(value):
    if isinstance(value, (int, float)):
        return (
            float(value) if not isinstance(value, bool) and math.isfinite(value) else 0
        )
    try:
        parts = [float(v) for v in str(value).split(":")]
        return sum(v * 60**i for i, v in enumerate(reversed(parts)))
    except ValueError:
        return 0


def matches(target, candidate):
    for key in ("musicbrainz_recording_id", "musicbrainz_release_id"):
        if target.get(key) and candidate.get(key) and target[key] != candidate[key]:
            return False

    def isrcs(value):
        raw = value.get("isrcs") or value.get("isrc") or []
        if isinstance(raw, str):
            raw = [raw]
        return {str(code).strip().upper() for code in raw if code}

    requested_isrcs, candidate_isrcs = isrcs(target), isrcs(candidate)
    if (
        requested_isrcs
        and candidate_isrcs
        and requested_isrcs.isdisjoint(candidate_isrcs)
    ):
        return False
    strict = target.get("explicit_edition")
    if (
        strict
        and candidate.get("source") == "monochrome"
        and target.get("album")
        and candidate.get("album")
        and folded(target["album"]) != folded(candidate["album"])
    ):
        return False
    if strict:
        if folded(candidate.get("channel")) != folded(target["artist"]):
            return False
        if folded(candidate.get("title")) != folded(target["title"]):
            return False
    elif primary_artist(candidate.get("channel")) != primary_artist(
        target["artist"]
    ) or title_key(candidate.get("title")) != title_key(target["title"]):
        return False
    versions = VERSION_WORDS if strict else VERSION_WORDS - EDITION_WORDS
    expected_versions = (
        set(folded(target["title"] + " " + target["album"]).split())
        | (set() if strict else set(recording_kind(target)))
    ) & versions
    actual_versions = (
        set(
            folded(
                candidate.get("title", "") + " " + candidate.get("album", "")
            ).split()
        )
        & versions
    )
    if expected_versions != actual_versions:
        return False
    expected, actual = duration(target.get("duration")), duration(
        candidate.get("duration")
    )
    return actual > 0 and (
        not expected or abs(actual - expected) <= ACQUISITION_TOLERANCE
    )


def safe_name(value):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")[:100] or "Unknown"
    if value.upper().split(".")[0] in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        value = "_" + value
    return value


# The queue loop's passes: at most one per PASS_GAP seconds, and the upgrade
# scan (also the fallback for changes made outside this process) at least
# every UPGRADE_SCAN_EVERY.
PASS_GAP = 0.5
UPGRADE_SCAN_EVERY = 60
# Seconds a job's check may reuse the NAS identity check and the settings.
CHECK_REUSE = 1


class Cancelled(Exception):
    pass


class QualityDeadline(Exception):
    pass


class SourceDeadline(Exception):
    """One source's time is up; the next source is tried (#132)."""


def describe(candidate):
    """A copy found, for the job's log (#197): format and size, no peer."""
    extension = str(candidate.get("extension") or "").lstrip(".").upper()
    quality = str(candidate.get("quality") or "")
    size = candidate.get("size")
    return {
        "format": " ".join(p for p in (extension, quality) if p) or None,
        "mb": round(size / 1_000_000, 1) if isinstance(size, (int, float)) else None,
    }


def outcome_of(exc):
    if isinstance(exc, Cancelled):
        return "cancelled"
    if isinstance(exc, (QualityDeadline, SourceDeadline)):
        return "time limit"
    if isinstance(exc, ProviderError):
        return exc.code
    if isinstance(exc, ValueError):
        return str(exc)[:80]
    return type(exc).__name__


class Timings:
    """Where one acquisition's time went (#134): the queue wait, then each
    step (resolve, search, transfer, validate, tag, decode, hash, commit) with
    its source, outcome and seconds. Kept in the request's payload and logged
    as one line when the job ends."""

    # Steps kept in the payload; the log line has them all.
    KEPT = 40

    def __init__(self, queued=None, resolved=None):
        self.began = time.monotonic()
        self.queued = queued
        self.steps = []
        # "source/profile" of the copy kept, once one is (#191).
        self.won = None
        if resolved is not None:
            self.steps.append(
                {"step": "resolve", "outcome": "ok", "seconds": round(resolved, 2)}
            )

    @contextmanager
    def step(self, name, **details):
        entry = {"step": name, **details}
        began = time.monotonic()
        try:
            yield entry
            entry.setdefault("outcome", "ok")
        except BaseException as exc:
            entry.setdefault("outcome", outcome_of(exc))
            raise
        finally:
            entry["seconds"] = round(time.monotonic() - began, 2)
            self.steps.append(entry)

    def payload(self):
        return {
            "queued_seconds": (
                None if self.queued is None else round(max(0, self.queued), 1)
            ),
            "total_seconds": round(time.monotonic() - self.began, 1),
            "steps": self.steps[-self.KEPT :],
        }

    def line(self, job, result):
        parts = []
        if self.queued is not None:
            parts.append(f"queued {max(0, self.queued):.1f} s")
        for entry in self.steps:
            words = [entry["step"]]
            words += [
                str(entry[key])
                for key in ("source", "profile", "peer", "state")
                if entry.get(key) is not None
            ]
            if entry.get("candidates") is not None:
                words.append(f"{entry['candidates']} candidates")
            if entry.get("bytes"):
                words.append(f"{entry['bytes'] / 1_000_000:.1f} MB")
            words.append(entry["outcome"])
            words.append(f"{entry['seconds']:.1f} s")
            parts.append(" ".join(words))
        log.info(
            "Download: %s - %s %s in %.1f s (%s)",
            job.get("artist"),
            job.get("title"),
            result,
            time.monotonic() - self.began,
            "; ".join(parts),
        )


class Downloader:
    # How the parity counts file this worker's downloads (#191).
    counted_as = "library"

    def __init__(self, store, providers=None, runtime=None, resolver=None):
        self.store = store
        # Files songs requested without an explicit edition under the
        # recommended edition of the artist's album (see `editions`).
        self.resolver = resolver
        self.providers = providers if providers is not None else PROVIDERS
        self.runtime = runtime or Runtime(store)
        self.soulseek = Soulseek(store)
        self.stopping = asyncio.Event()
        # Set when the queue or download settings change (Store.queue_listeners).
        self.wake = asyncio.Event()
        # Set once staged downloads are recovered (and the library refreshed).
        self.recovered = threading.Event()
        self.deadlines = {}
        self.settings_cache = (float("-inf"), None)
        self.context = threading.local()
        configured = Path(
            os.environ.get("PIG_MUSIC_DOWNLOAD_ROOT") or store.directory / "downloads"
        )
        self.library_root = configured / "library"
        self.staging = configured / "staging"
        (store.directory / "downloads" / "staging").mkdir(parents=True, exist_ok=True)
        try:
            self.prepare_storage()
        except (OSError, ValueError):
            pass  # Cached browsing and settings remain available without the NAS.

    def prepare_storage(self):
        download_root(self.store)  # Validate identity before creating any NAS path.
        self.library_root.mkdir(parents=True, exist_ok=True)
        self.staging.mkdir(parents=True, exist_ok=True)

    def check(self, job_id):
        # Called every 50 ms while a tool runs and per network chunk (#135):
        # the NAS identity check and the settings are reused for a second
        # (writes check the mount again themselves); cancellation is read
        # each time.
        download_root(self.store, max_age=CHECK_REUSE)
        if self.stopping.is_set() or not self.store.request_active(
            job_id, getattr(self.context, "claim_token", None)
        ):
            raise Cancelled()
        if self.deadlines.get(job_id, float("inf")) <= time.monotonic():
            raise QualityDeadline()
        if not self.recent_settings()["enabled"]:
            self.state(job_id, "waiting_for_sources")
            raise Cancelled()

    def recent_settings(self):
        """The download settings, read at most once per CHECK_REUSE."""
        read_at, settings = self.settings_cache
        if time.monotonic() - read_at >= CHECK_REUSE:
            settings = self.store.download_settings()
            self.settings_cache = (time.monotonic(), settings)
        return settings

    def source_check(self, job_id, deadline):
        """`check`, and SourceDeadline once `deadline` (monotonic) passes:
        what the Soulseek engine is given, whose waits ignored the
        per-source limit (#132)."""
        self.check(job_id)
        if time.monotonic() >= deadline:
            raise SourceDeadline()

    def timings(self):
        timings = getattr(self.context, "timings", None)
        if timings is None:
            timings = self.context.timings = Timings()
        return timings

    def state(self, request_id, status, **details):
        self.store.request_state(
            request_id,
            status,
            _claim_token=getattr(self.context, "claim_token", None),
            **details,
        )

    async def run(self):
        # Store calls wait on lifecycle_lock and the NAS, so none runs on the
        # event loop (LOOP1): a long download holding the lock stalled every
        # request the server answered.
        tasks = set()
        next_scan = 0
        loop = asyncio.get_running_loop()

        def changed():
            try:
                loop.call_soon_threadsafe(self.wake.set)
            except RuntimeError:
                pass  # The loop has closed; nothing is waiting.

        self.store.queue_listeners.append(changed)
        try:
            while not self.stopping.is_set():
                started = time.monotonic()
                # Cleared before the queue is read: a change from here on
                # starts another pass.
                self.wake.clear()
                try:
                    await asyncio.to_thread(self.prepare_storage)
                except (OSError, ValueError):
                    try:
                        await asyncio.wait_for(self.stopping.wait(), timeout=5)
                    except TimeoutError:
                        pass
                    continue
                if not self.recovered.is_set():
                    await asyncio.to_thread(self.store.recover_requests)
                    self.recovered.set()
                tasks = {t for t in tasks if not t.done()}
                if time.monotonic() >= next_scan:
                    await asyncio.to_thread(self.store.schedule_upgrades)
                    # At most hourly: partial files of transfers the engine
                    # reports as failed or cancelled (#96).
                    await asyncio.to_thread(self.soulseek.sweep)
                    next_scan = time.monotonic() + UPGRADE_SCAN_EVERY
                settings = await asyncio.to_thread(self.store.download_settings)
                while (
                    len(tasks) < settings["concurrent_downloads"]
                    and not self.stopping.is_set()
                ):
                    job = await asyncio.to_thread(self.store.claim_request)
                    if not job:
                        break
                    # A claimed job always gets its handler, even when stopping
                    # began meanwhile: process() then cancels it as before.
                    task = asyncio.create_task(self.handle(job))
                    task.add_done_callback(lambda _: self.wake.set())
                    tasks.add(task)
                # Nothing is polled while idle (#63): the next pass is for a
                # queue or settings change, a finished download, a deferred
                # request falling due, or the upgrade scan.
                until = next_scan
                if (
                    settings["enabled"]
                    and len(tasks) < settings["concurrent_downloads"]
                ):
                    retry = await asyncio.to_thread(self.store.next_retry)
                    if retry is not None:
                        until = min(until, time.monotonic() + retry - time.time())
                await self.rest(started, until)
        finally:
            self.store.queue_listeners.remove(changed)
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    async def rest(self, started, until):
        """Wait for a wake or `until` (monotonic), but start the next pass no
        sooner than PASS_GAP after `started`: a download's progress writes
        wake it often, and passes stay as frequent as the old poll at most."""
        try:
            await asyncio.wait_for(
                self.stopping.wait(),
                timeout=max(0, started + PASS_GAP - time.monotonic()),
            )
            return
        except TimeoutError:
            pass
        waits = [
            asyncio.create_task(self.stopping.wait()),
            asyncio.create_task(self.wake.wait()),
        ]
        try:
            await asyncio.wait(
                waits,
                timeout=max(0, until - time.monotonic()),
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for wait in waits:
                wait.cancel()
            await asyncio.gather(*waits, return_exceptions=True)

    async def handle(self, job):
        # Quality replacements keep the file's own identity.
        resolved = None
        if self.resolver and not job.get("library_track_id"):
            from .editions import Deferred, Unresolved

            began = time.monotonic()
            try:
                changes = await self.resolver.prepare(job)
            except Deferred as later:
                await asyncio.to_thread(
                    self.store.defer_request, job, later.seconds, later.attempts
                )
                return
            except Unresolved as error:
                # Never filed on a guess (#110): it fails with the reason and
                # a fresh budget, so Retry resolves it once MusicBrainz is back.
                await asyncio.to_thread(
                    self.store.request_state,
                    job["id"],
                    "failed",
                    _claim_token=job.get("claim_token"),
                    error=str(error),
                    resolution_attempts=0,
                )
                return
            except Exception:
                changes = {}
            resolved = time.monotonic() - began
            if changes:
                job = {**job, **changes}
                await asyncio.to_thread(
                    self.store.request_state,
                    job["id"],
                    "searching",
                    _claim_token=job.get("claim_token"),
                    **changes,
                )
            # The household may already have this song on another edition.
            found = self.store.index.match(job).track
            if found and await asyncio.to_thread(
                self.store.fulfil_request, job, found["id"]
            ):
                return
        if resolved is not None:
            job = {**job, "resolve_seconds": resolved}
        await asyncio.to_thread(self.process, job)

    def transfer(self, job_id, candidate, path):
        source = candidate["source"]
        if source == "soulseek":
            self.soulseek.reports.current = getattr(
                self.context, "transfer_report", None
            )
            deadline = getattr(self.context, "source_deadline", float("inf"))
            try:
                self.soulseek.transfer(
                    candidate,
                    path,
                    lambda: self.source_check(job_id, deadline),
                    lambda received, total: self.progress(job_id, received, total),
                )
            finally:
                self.soulseek.reports.current = None
            return {}
        context = Context(
            candidate.get("effective_profile", "native_standard"),
            check=lambda: self.check(job_id),
            progress=lambda received, total: self.progress(job_id, received, total),
            deadline=min(
                self.deadlines.get(job_id, float("inf")),
                getattr(self.context, "source_deadline", float("inf")),
                time.monotonic() + 180,
            ),
        )
        if REGISTRY[source].adapter:
            return self.runtime.transfer(source, candidate, path, context)
        context.max_bytes = 80_000_000
        http_transfer(
            candidate["source_url"], REGISTRY[source].download_hosts, path, context
        )
        return {}

    def progress(self, job_id, received, total):
        self.store.progress(
            job_id, received, total, getattr(self.context, "claim_token", None)
        )

    def validate(self, path, target, profile, decode=True, check=lambda: None):
        probe = probe_file(path, check)
        expected_codec = (
            "flac"
            if profile == "lossless"
            else "aac" if profile == "aac_256" else "mp3"
        )
        if not probe or (
            profile != "native_standard" and probe["codec"] != expected_codec
        ):
            raise ValueError(
                f"Downloaded content is not readable {expected_codec.upper()} for this quality profile"
            )
        validate_streams(path, check)
        # A request for a particular edition must match its tags exactly. Any
        # other request accepts what source selection accepted (#69): the same
        # lead artist ("feat." credits aside) and edition-free title, so a
        # "2004 Remaster" tag passes while live, remix or demo stays distinct.
        explicit = target.get("explicit_edition")
        same = {
            "artist": folded if explicit else primary_artist,
            "title": folded if explicit else title_key,
        }
        for key, identity in same.items():
            if probe.get(key) and identity(probe[key]) != identity(target[key]):
                raise ValueError(
                    "Embedded recording metadata does not match the request"
                )
        versions = VERSION_WORDS if explicit else VERSION_WORDS - EDITION_WORDS
        expected_versions = (
            set(folded(target["title"] + " " + target["album"]).split()) & versions
        )
        actual_versions = (
            set(
                folded(
                    (probe.get("title") or "") + " " + (probe.get("album") or "")
                ).split()
            )
            & versions
        )
        if actual_versions and actual_versions != expected_versions:
            raise ValueError("Embedded recording version does not match the request")
        if not satisfies(profile, probe):
            raise ValueError(
                "Downloaded audio does not meet the selected quality profile"
            )
        if CODEC_EXTENSIONS.get(probe["codec"]) != path.suffix.lower():
            raise ValueError("Downloaded codec does not match its audio container")
        if probe["duration"] <= 0:
            raise ValueError("Downloaded recording has no readable duration")
        if (
            target.get("duration")
            and abs(probe["duration"] - target["duration"]) > ACQUISITION_TOLERANCE
        ):
            raise ValueError("Downloaded recording has the wrong duration")
        if probe["codec"] == "flac":
            # A FLAC made from an MP3 or AAC keeps the encoder's cutoff (#195).
            cutoff = lossy_cutoff(
                path, probe.get("sample_rate_hz") or 0, probe["duration"], check
            )
            if cutoff:
                raise ValueError(
                    "Lossless download looks like lossy audio "
                    f"(nothing above about {cutoff / 1000:.1f} kHz)"
                )
        if decode:
            self.decode(path, check)
        return probe

    def decode(self, path, check=lambda: None):
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise ValueError("FFmpeg is required to validate the complete recording")
        result = run_checked(
            [
                ffmpeg,
                "-v",
                "error",
                "-xerror",
                "-protocol_whitelist",
                "file,pipe",
                "-i",
                str(path),
                "-f",
                "null",
                "-",
            ],
            check,
            timeout=90,
        )
        if result.returncode:
            raise ValueError("The downloaded audio failed complete decoding")

    def tag(self, path, target, source, clear=(), cover=None):
        """Write `target`'s fields; a field named in `clear` that `target`
        lacks is removed rather than left as the file had it. `cover`: the
        stored cover's SHA, embedded in the same save (#137)."""
        art = self.cover_bytes(cover)
        if path.suffix in {".flac", ".opus", ".ogg"}:
            audio = {".flac": FLAC, ".opus": OggOpus, ".ogg": OggVorbis}[path.suffix](
                path
            )
            if audio.tags is None:
                audio.add_tags()
            for field, key in (
                ("title", "TITLE"),
                ("artist", "ARTIST"),
                ("album", "ALBUM"),
                ("album_artist", "ALBUMARTIST"),
                ("disc_number", "DISCNUMBER"),
                ("track_number", "TRACKNUMBER"),
                ("year", "DATE"),
                ("musicbrainz_release_id", "MUSICBRAINZ_ALBUMID"),
                ("musicbrainz_recording_id", "MUSICBRAINZ_TRACKID"),
            ):
                if target.get(field) is not None:
                    audio[key] = str(target[field])
                elif field in clear and key in audio:
                    del audio[key]
            audio["PIG_MUSIC_SOURCE"] = source
            if art is not None:
                self.put_cover(audio, path.suffix, art)
            audio.save()
            return
        if path.suffix == ".m4a":
            audio = MP4(path)
            if audio.tags is None:
                audio.add_tags()
            for key, field in (
                ("\xa9nam", "title"),
                ("\xa9ART", "artist"),
                ("\xa9alb", "album"),
                ("aART", "album_artist"),
            ):
                audio[key] = [target[field]]
            for key, field in (("trkn", "track_number"), ("disk", "disc_number")):
                if target.get(field) is not None:
                    audio[key] = [(target[field], 0)]
                elif field in clear:
                    audio.pop(key, None)
            if target.get("year"):
                audio["\xa9day"] = [str(target["year"])]
            elif "year" in clear:
                audio.pop("\xa9day", None)
            audio["----:com.apple.iTunes:PIG_MUSIC_SOURCE"] = [
                MP4FreeForm(source.encode())
            ]
            for field, description in (
                ("musicbrainz_release_id", "MusicBrainz Album Id"),
                ("musicbrainz_recording_id", "MusicBrainz Track Id"),
            ):
                if target.get(field):
                    audio["----:com.apple.iTunes:" + description] = [
                        MP4FreeForm(target[field].encode())
                    ]
                elif field in clear:
                    audio.pop("----:com.apple.iTunes:" + description, None)
            if art is not None:
                self.put_cover(audio, path.suffix, art)
            audio.save()
            return
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            tags = ID3()
        for key, cls, field in [
            ("TIT2", TIT2, "title"),
            ("TPE1", TPE1, "artist"),
            ("TALB", TALB, "album"),
            ("TPE2", TPE2, "album_artist"),
            ("TRCK", TRCK, "track_number"),
            ("TPOS", TPOS, "disc_number"),
        ]:
            if target.get(field) is None and field in clear:
                tags.delall(key)
                continue
            tags.delall(key)
            tags.add(cls(encoding=3, text=str(target[field])))
        if target.get("year"):
            tags.delall("TDRC")
            tags.add(TDRC(encoding=3, text=str(target["year"])))
        elif "year" in clear:
            tags.delall("TDRC")
        tags.add(TXXX(encoding=3, desc="PIG_MUSIC_SOURCE", text=source))
        for field, description in (
            ("musicbrainz_release_id", "MusicBrainz Album Id"),
            ("musicbrainz_recording_id", "MusicBrainz Track Id"),
        ):
            if target.get(field):
                tags.delall("TXXX:" + description)
                tags.add(TXXX(encoding=3, desc=description, text=target[field]))
            elif field in clear:
                tags.delall("TXXX:" + description)
        if art is not None:
            self.put_cover(tags, path.suffix, art)
        tags.save(path, v2_version=3)

    def cover_bytes(self, sha):
        cover = self.store.covers.path(sha) if sha else None
        return cover.read_bytes() if cover else None

    @staticmethod
    def put_cover(audio, suffix, data):
        """Put `data` (JPEG) in `audio`'s tags as the front cover, replacing
        any it carried; the caller saves."""
        if suffix == ".flac":
            picture = Picture()
            picture.type, picture.mime, picture.data = 3, "image/jpeg", data
            audio.clear_pictures()
            audio.add_picture(picture)
        elif suffix == ".m4a":
            audio["covr"] = [MP4Cover(data, imageformat=MP4Cover.FORMAT_JPEG)]
        elif suffix in {".opus", ".ogg"}:
            picture = Picture()
            picture.type, picture.mime, picture.data = 3, "image/jpeg", data
            audio["METADATA_BLOCK_PICTURE"] = [
                base64.b64encode(picture.write()).decode("ascii")
            ]
        else:
            audio.delall("APIC")
            audio.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="", data=data))

    def embed_cover(self, path, sha):
        """Write the stored cover into the file, replacing any it carried."""
        data = self.cover_bytes(sha)
        if data is None:
            return
        if path.suffix == ".flac":
            audio = FLAC(path)
        elif path.suffix == ".m4a":
            audio = MP4(path)
        elif path.suffix in {".opus", ".ogg"}:
            audio = {".opus": OggOpus, ".ogg": OggVorbis}[path.suffix](path)
        else:
            audio = ID3(path)
        self.put_cover(audio, path.suffix, data)
        if isinstance(audio, ID3):
            audio.save(path, v2_version=3)
        else:
            audio.save()

    def complete(self, job, path, extension, source, profile, provenance=None):
        self.check(job["id"])
        self.state(job["id"], "validating", effective_quality=profile)
        timings = self.timings()
        with timings.step("validate"):
            probe = self.validate(
                path, job, profile, decode=False, check=lambda: self.check(job["id"])
            )
            if (
                job.get("exact_quality")
                and profile == "mp3_128"
                and probe["bitrate_kbps"] > 144
            ):
                raise ValueError("Replacement must be a genuine MP3 128 kbps source")
        with timings.step("tag"):
            self.tag(path, job, source, cover=job.get("cover_sha"))
        with timings.step("decode"):
            self.decode(path, lambda: self.check(job["id"]))
        probe["acquisition"] = {
            "provider": source,
            "quality_profile": profile,
            "codec": probe["codec"],
            "container": probe["container"],
            "bitrate_kbps": probe["bitrate_kbps"],
            "duration": probe["duration"],
            "sample_rate_hz": probe["sample_rate_hz"],
            "channels": probe["channels"],
            "validated_at": int(time.time()),
            **(provenance or {}),
        }
        self.check(job["id"])
        filename = f"{job['disc_number']:02}-{job['track_number']:02} {safe_name(job['title'])} [{job['id'][:8]}]{extension}"
        target = (
            self.library_root
            / safe_name(job["album_artist"])
            / safe_name(job["album"])
            / filename
        )
        with timings.step("hash"):
            sha = file_sha256(path)
        # The payload keeps where the time went; the commit's own time is in
        # the log line only.
        self.state(job["id"], "validating", timings=timings.payload())
        with timings.step("commit"):
            self.store.publish_request(job, path, target, probe, sha)

    def convert_down(self, job, path, profile):
        """A lossy `profile` copy of the lossless download at `path`, and what
        the original was. The original is validated as lossless first (the
        recording checks and #195's fake-lossless check); more than two
        channels are refused rather than silently downmixed. Converting up
        is never possible here: only a verified FLAC is converted (D-92)."""
        if profile not in CONVERT_TO:
            raise ValueError("Only MP3 320 and AAC 256 are made by conversion")
        check = lambda: self.check(job["id"])
        with self.timings().step("validate", profile="lossless"):
            original = self.validate(path, job, "lossless", check=check)
        if (original.get("channels") or 0) > 2:
            raise ValueError("Lossless original has more than two channels")
        codec, extension, bitrate = CONVERT_TO[profile]
        output = path.with_name(path.stem + "-converted" + extension)
        rate = original.get("sample_rate_hz") or 0
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise ValueError("FFmpeg is required to convert a lossless download")
        with self.timings().step("convert", profile=profile):
            result = run_checked(
                [ffmpeg, "-v", "error", "-nostdin", "-y", "-i", str(path)]
                + ["-map", "0:a:0", "-vn", "-map_metadata", "-1"]
                + ([] if rate in (44100, 48000) else ["-ar", "44100"])
                + ["-c:a", codec, "-b:a", bitrate, str(output)],
                check,
                timeout=180,
            )
            if result.returncode or not output.is_file():
                output.unlink(missing_ok=True)
                raise ValueError("Converting the lossless original failed")
        return output, {
            "codec": "flac",
            "sample_rate_hz": rate,
            "bits_per_sample": original.get("bits_per_sample"),
            "channels": original.get("channels"),
            "lossless_checked": True,
        }

    def attempt(self, job, profile, errors, convert=False):
        """Try each enabled source for `profile`. With `convert` (D-92, #196),
        search for a lossless original instead, check it in full (including
        the fake-lossless check, #195) and convert it down to `profile`."""
        wanted = "lossless" if convert else profile
        effective = {**job, "quality_profile": wanted}
        for source in self.store.download_settings()["sources"]:
            self.check(job["id"])
            if not supports(source, wanted):
                continue
            self.context.source_deadline = min(
                self.deadlines.get(job["id"], float("inf")), time.monotonic() + 240
            )
            try:
                if (
                    source not in self.providers
                    and source != "soulseek"
                    and not REGISTRY[source].adapter
                ):
                    continue
                self.state(
                    job["id"],
                    "searching",
                    source=source,
                    effective_quality=profile,
                    timings=self.timings().payload(),
                )
                context = Context(
                    wanted,
                    check=lambda: self.check(job["id"]),
                    deadline=min(
                        self.context.source_deadline,
                        time.monotonic() + 90,
                    ),
                )
                with self.timings().step(
                    "search", source=source, profile=wanted
                ) as searched:
                    if REGISTRY[source].adapter:
                        candidates = self.runtime.search(source, effective, context)
                    else:
                        with self.runtime.health.operation(source):
                            if source == "soulseek":
                                candidates = self.soulseek.search(
                                    effective,
                                    lambda: self.source_check(
                                        job["id"], context.deadline
                                    ),
                                )
                            elif self.providers[source] is REGISTRY[source].search:
                                candidates = legacy_search(
                                    source, f"{job['artist']} {job['title']}", context
                                )
                            else:
                                candidates = self.providers[source](
                                    f"{job['artist']} {job['title']}", 20
                                )
                            self.runtime.health.outcome(source)
                    candidates = [c for c in candidates if matches(job, c)]
                    searched["candidates"] = len(candidates)
                    searched["found"] = [describe(c) for c in candidates[:3]]
                if job.get("exact_quality") and profile == "mp3_128":
                    exact = lambda c: not re.search(
                        r"(?<!\d)(?:192|256|320)(?!\d)", str(c.get("quality", ""))
                    )
                    candidates = [c for c in candidates if exact(c)]
                candidates.sort(key=lambda c: c.get("quality") != "320kbps")
                for candidate in candidates[:3]:
                    self.check(job["id"])
                    extension = REGISTRY[source].extension(candidate)
                    path = (
                        self.staging
                        / f"{job['id']}-{job.get('claim_token','legacy')}{extension}"
                    )
                    transferred = False
                    converted = None
                    try:
                        self.state(
                            job["id"],
                            "downloading",
                            source=source,
                            effective_quality=profile,
                            timings=self.timings().payload(),
                        )
                        with self.timings().step(
                            "transfer", source=source, peer=candidate.get("username")
                        ) as moved:
                            self.context.transfer_report = moved
                            try:
                                provenance = self.transfer(
                                    job["id"],
                                    {
                                        **candidate,
                                        "source": source,
                                        "effective_profile": wanted,
                                    },
                                    path,
                                )
                            finally:
                                self.context.transfer_report = None
                                try:
                                    moved["bytes"] = path.stat().st_size
                                except OSError:
                                    pass
                        transferred = True
                        if convert:
                            converted, original = self.convert_down(job, path, profile)
                            provenance = {
                                **(provenance or {}),
                                "converted_from": original,
                            }
                            self.complete(
                                job,
                                converted,
                                converted.suffix,
                                source,
                                profile,
                                provenance,
                            )
                        else:
                            self.complete(
                                job, path, extension, source, profile, provenance
                            )
                        self.timings().won = (
                            source
                            + "/"
                            + profile
                            + (" from lossless" if convert else "")
                        )
                        self.runtime.health.outcome(source, transfer=True)
                        return True
                    except (Cancelled, QualityDeadline, SourceDeadline):
                        raise
                    except Exception as exc:
                        if transferred:
                            self.runtime.health.outcome(
                                source, error=ProviderError("no_match"), transfer=True
                            )
                        elif not REGISTRY[source].adapter:
                            self.runtime.health.outcome(
                                source,
                                error=(
                                    exc
                                    if isinstance(exc, ProviderError)
                                    else ProviderError("no_match")
                                ),
                                transfer=True,
                            )
                        message = (
                            "transfer failed"
                            if isinstance(exc, httpx.HTTPError)
                            else (
                                str(exc)[:160]
                                if isinstance(exc, ValueError)
                                else "candidate validation failed"
                            )
                        )
                        errors.append(f"{source}: {message}")
                    finally:
                        path.unlink(missing_ok=True)
                        if converted is not None:
                            converted.unlink(missing_ok=True)
                if not candidates:
                    errors.append(
                        f"{source}: no candidate passed recording/version matching"
                    )
            except (Cancelled, QualityDeadline):
                raise
            except SourceDeadline:
                # Not the source's fault (a slow peer, a busy engine): its
                # health is left alone.
                errors.append(f"{source}: time limit reached")
            except ProviderError as exc:
                errors.append(f"{source}: {exc}")
            except Exception:
                self.runtime.health.outcome(source, error=ProviderError())
                errors.append(f"{source}: search unavailable")
        return False

    def process(self, job):
        # Measurements of this claim (#134), never stored with the request.
        job = dict(job)
        queued = job.pop("queued_seconds", None)
        resolved = job.pop("resolve_seconds", None)
        self.context.claim_token = job.get("claim_token")
        timings = self.context.timings = Timings(queued, resolved)
        result = "interrupted"
        errors = []
        desired = job.get(
            "quality_policy",
            policy(self.store.download_settings(), job.get("quality_profile")),
        )
        if desired["preferred"] == "mp3_128":
            job = {**job, "exact_quality": True}
        # Each quality level gets its budget, and the whole job at most two
        # (#133): three full budgets in a row made a song with no live
        # source take half an hour to fail.
        ceiling = time.monotonic() + 2 * desired["fallback_minutes"] * 60

        def budget():
            return min(time.monotonic() + desired["fallback_minutes"] * 60, ceiling)

        try:
            preferred = desired["preferred"]
            mix_cache = getattr(self.store, "mix_cache", None)
            if mix_cache is not None and type(self) is Downloader:
                try:
                    if mix_cache.promote(self, job, preferred):
                        result = "completed from the mix cache"
                        return
                except (Cancelled, QualityDeadline):
                    raise
                except (OSError, ValueError):
                    pass  # A stale/unsuitable cache copy never blocks acquisition.
            self.deadlines[job["id"]] = budget()
            try:
                if self.attempt(job, preferred, errors):
                    result = "completed"
                    return
            except QualityDeadline:
                errors.append("Preferred quality attempt reached its time limit")
            finally:
                self.deadlines.pop(job["id"], None)
            # A verified lossless original converted down (D-92, D-98, #196):
            # not for genuine-128 replacements or quality upgrades.
            if (
                preferred in CONVERT_TO
                and not job.get("exact_quality")
                and not job.get("upgrade_only")
            ):
                self.deadlines[job["id"]] = budget()
                try:
                    if self.attempt(job, preferred, errors, convert=True):
                        result = "completed"
                        return
                except QualityDeadline:
                    errors.append("Conversion from lossless reached its time limit")
                finally:
                    self.deadlines.pop(job["id"], None)
            fallback = desired["fallback"]
            if (
                preferred == "lossless"
                and fallback != "none"
                and not job.get("upgrade_only")
            ):
                self.deadlines[job["id"]] = budget()
                try:
                    if self.attempt(job, fallback, errors):
                        result = "completed"
                        return
                except QualityDeadline:
                    errors.append("Fallback quality attempt reached its time limit")
                finally:
                    self.deadlines.pop(job["id"], None)
            # Last resort: AAC from JioSaavn, only when the owner enabled it as
            # a source, and only for requests made since this fallback existed.
            if (
                job.get("quality_policy", {}).get("aac_fallback")
                and "jiosaavn" in self.store.download_settings()["sources"]
                and not job.get("upgrade_only")
                and preferred != "aac_256"
                and not (preferred == "lossless" and fallback == "none")
            ):
                self.deadlines[job["id"]] = budget()
                try:
                    if self.attempt(job, "aac_256", errors):
                        result = "completed"
                        return
                except QualityDeadline:
                    errors.append("AAC fallback attempt reached its time limit")
                finally:
                    self.deadlines.pop(job["id"], None)
            result = "failed"
            self.state(
                job["id"],
                "failed",
                error="; ".join(errors)[-1000:]
                or "No enabled source supports this request's quality profile",
                timings=timings.payload(),
            )
        except Cancelled:
            result = "cancelled"
        except Exception:
            # A failure like any other for the completion count (#191).
            result = "failed"
            self.state(
                job["id"],
                "failed",
                error="Acquisition interrupted; existing music retained. Retry this request.",
                timings=timings.payload(),
            )
        finally:
            self.deadlines.pop(job["id"], None)
            timings.line(job, result)
            self.count(timings, result)
            self.context.timings = None

    def count(self, timings, result):
        """A finished download for the parity targets (#191): mix songs and
        library requests apart, with the source and profile that won."""
        performance = getattr(self.store, "performance", None)
        if performance is None or result == "interrupted":
            return
        if result == "completed from the mix cache":
            result, timings.won = "completed", "mix cache"
        performance.record(
            "download",
            self.counted_as,
            result,
            (time.monotonic() - timings.began) * 1000,
            timings.won if result == "completed" else None,
        )
