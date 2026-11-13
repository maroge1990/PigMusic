"""YouTube for instant listening, a one-week trial (#193, D-91).

Octo's method: YouTube Music's song search (its song results are the official
audio), then the stream address from yt-dlp's visionos client, whose addresses
play at once. The address is tied to the server's network, so the server
passes the bytes through to the player (`YouTubeStream`), range by range,
holding nothing. Downloads never come from YouTube.

Matching is RemoteAudio's (the shared song rule); this module only searches,
looks addresses up and streams. Addresses are kept until shortly before they
expire. When YouTube refuses an address a fresh one is looked up; when
refusals outlast that, the yt-dlp cache is purged (at most every 15 minutes),
and then yt-dlp updates itself (at most every 6 hours, D-95): only an official
release whose checksum matches yt-dlp's published list, kept on the appdata
volume. The version pinned in the image stays the fallback.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import zipfile
from collections import OrderedDict
from pathlib import Path

import httpx

from .providers.contracts import Context, ProviderError
from .providers.network import client as guarded_client, stream as guarded_stream
from .providers.process import run_checked

log = logging.getLogger("pig_music.youtube")

SEARCH_HOSTS = ("music.youtube.com", "youtube.com")
AUDIO_HOSTS = ("googlevideo.com",)
SEARCH_SECONDS = 5
# The worker's own limit is 12 s (server/providers/youtube_worker.py).
RESOLVE_SECONDS = 15
# An address is used until this long before YouTube's stated expiry.
EXPIRY_MARGIN = 30 * 60
# Without a stated expiry (never seen), an address is kept this long.
DEFAULT_KEEP = 60 * 60
ADDRESSES = 1000
PURGE_SECONDS = 15 * 60
UPDATE_SECONDS = 6 * 60 * 60
# Refusals within this window that outlast a fresh address count towards a
# purge, then an update.
REFUSAL_WINDOW = 10 * 60
REFUSALS_BEFORE_REPAIR = 2
RELEASES = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/"
RELEASE_HOSTS = (
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
)
UPDATE_BYTES = 40_000_000
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")


def version_key(value):
    """yt-dlp versions are dates: 2026.08.19 or 2026.11.02.1."""
    try:
        return tuple(int(part) for part in str(value).split("."))
    except ValueError:
        return ()


def pinned_version():
    from yt_dlp.version import __version__

    return __version__


class GuardedSearch:
    """ytmusicapi's song search, every request through the guarded transport."""

    def __init__(self):
        from ytmusicapi import YTMusic

        session = guarded_client()

        class Guarded(YTMusic):
            def _send_request(self, endpoint, body, additionalParams=""):
                from ytmusicapi.constants import YTM_BASE_API

                body.update(self.context)
                return json.loads(
                    fetch(
                        session,
                        "POST",
                        YTM_BASE_API + endpoint + self.params + additionalParams,
                        dict(self.headers),
                        json.dumps(body).encode(),
                    )
                )

            def _send_get_request(self, url, params=None, use_base_headers=False):
                from ytmusicapi.helpers import initialize_headers

                headers = initialize_headers() if use_base_headers else self.headers
                query = httpx.QueryParams(params or {})
                text = fetch(
                    session,
                    "GET",
                    url + ("?" + str(query) if params else ""),
                    dict(headers),
                    None,
                )

                class Reply:
                    pass

                reply = Reply()
                reply.text = text
                return reply

        self.session = session
        self.music = Guarded()
        self.lock = threading.Lock()

    def songs(self, query, limit=10):
        # ytmusicapi keeps state (visitor ID, headers) per instance.
        with self.lock:
            results = self.music.search(query, filter="songs", limit=limit)
        songs = []
        for item in results[:limit]:
            video_id = item.get("videoId")
            artists = [
                a.get("name") for a in item.get("artists") or [] if a.get("name")
            ]
            if (
                not isinstance(video_id, str)
                or not VIDEO_ID.fullmatch(video_id)
                or not artists
                or not item.get("title")
            ):
                continue
            album = item.get("album") or {}
            songs.append(
                {
                    "video_id": video_id,
                    "channel": ", ".join(artists),
                    "title": item["title"],
                    "album": album.get("name") or "",
                    "duration": item.get("duration_seconds"),
                    "explicit": bool(item.get("isExplicit")),
                }
            )
        return songs


def fetch(session, method, url, headers, content):
    context = Context("native_standard", deadline=time.monotonic() + SEARCH_SECONDS)
    reply = guarded_stream(
        session,
        method,
        url,
        SEARCH_HOSTS,
        context,
        headers=headers,
        content=content,
    )
    try:
        body = bytearray()
        for chunk in reply.iter_bytes(65536):
            context.remaining()
            body.extend(chunk)
            if len(body) > 8_000_000:
                raise ProviderError("limit")
        if reply.status_code >= 400:
            raise ProviderError(
                "access_required"
                if reply.status_code in {401, 403, 429}
                else "unavailable"
            )
        return body.decode("utf-8", "replace")
    finally:
        reply.close()


class YouTube:
    def __init__(self, directory):
        # {directory}/cache: yt-dlp's player cache; {directory}/update: a
        # verified newer yt-dlp (D-95).
        self.directory = Path(directory)
        self.search_slots = asyncio.Semaphore(2)
        self.resolve_slots = asyncio.Semaphore(2)
        # Event-loop owned: {video_id: (usable until (wall clock), address)}.
        self.addresses = OrderedDict()
        self.resolving = {}
        self.searcher = None
        self.refusals = []
        self.purged_at = float("-inf")
        self.refusal_lock = threading.Lock()
        self.update_task = None
        self.wants_update = False
        self.clock = time.time
        # Counts refusals for Admin's instant sources (#191); set by the app.
        self.performance = None

    # Search -----------------------------------------------------------------

    async def songs(self, track):
        async with self.search_slots:
            if self.searcher is None:
                self.searcher = await asyncio.to_thread(GuardedSearch)
            query = f"{track['artist']} {track['title']}"
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(self.searcher.songs, query), SEARCH_SECONDS + 1
                )
            except ProviderError:
                raise
            except (TimeoutError, httpx.HTTPError, OSError) as exc:
                raise TimeoutError("YouTube search unavailable.") from exc
            except Exception as exc:
                # ytmusicapi parses YouTube's own layout; a change there is a
                # provider problem, never a server failure.
                raise ProviderError("invalid_response") from exc

    # Addresses ----------------------------------------------------------------

    def kept(self, video_id):
        entry = self.addresses.get(video_id)
        if entry and entry[0] > self.clock():
            self.addresses.move_to_end(video_id)
            return entry[1]
        self.addresses.pop(video_id, None)
        return None

    async def address(self, video_id, fresh=False):
        if not VIDEO_ID.fullmatch(video_id or ""):
            raise ValueError("Not a YouTube video.")
        if fresh:
            self.addresses.pop(video_id, None)
        else:
            found = self.kept(video_id)
            if found:
                return found
        task = self.resolving.get(video_id)
        if task is None:
            task = asyncio.ensure_future(self._address(video_id))
            self.resolving[video_id] = task

            def done(finished, video_id=video_id):
                if self.resolving.get(video_id) is finished:
                    del self.resolving[video_id]

            task.add_done_callback(done)
            task.add_done_callback(lambda t: t.cancelled() or t.exception())
        await asyncio.wait({task})
        return task.result()

    async def _address(self, video_id):
        async with self.resolve_slots:
            try:
                found = await asyncio.to_thread(self.resolve, video_id)
            finally:
                self.maybe_update()
        expires = found.get("expires")
        until = (
            expires - EXPIRY_MARGIN
            if isinstance(expires, int)
            else self.clock() + DEFAULT_KEEP
        )
        if until <= self.clock():
            raise TimeoutError("YouTube gave an address that has already expired.")
        self.addresses[video_id] = (until, found)
        self.addresses.move_to_end(video_id)
        while len(self.addresses) > ADDRESSES:
            self.addresses.popitem(last=False)
        return found

    def update_path(self):
        """The verified newer yt-dlp, or None for the pinned one."""
        record = self.directory / "update" / "verified.json"
        try:
            saved = json.loads(record.read_text())
            path = self.directory / "update" / saved["file"]
            if (
                version_key(saved["version"]) > version_key(pinned_version())
                and hashlib.sha256(path.read_bytes()).hexdigest() == saved["sha256"]
            ):
                return path
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None

    def resolve(self, video_id):
        """Worker thread: the isolated lookup (server/providers/youtube_worker.py)."""
        cache = self.directory / "cache"
        cache.mkdir(parents=True, exist_ok=True)
        update = self.update_path()
        with tempfile.TemporaryDirectory(prefix="pig-youtube-") as folder:
            request = Path(folder) / "request.json"
            request.write_text(json.dumps({"video_id": video_id, "cache": str(cache)}))
            result = self.run_worker(request, update)
        try:
            value = json.loads(result.stdout.decode().strip().splitlines()[-1])
        except (ValueError, IndexError) as exc:
            raise ProviderError("invalid_response") from exc
        if "error" in value:
            if value["error"] == "access_required":
                self.refused_now()
            raise ProviderError(value["error"])
        return value

    def run_worker(self, request, update):
        # -I keeps the user's site-packages and environment out; the app's
        # folder is put back so the worker can import its guarded transport.
        root = str(Path(__file__).resolve().parent.parent)
        return run_checked(
            [
                sys.executable,
                "-I",
                "-c",
                "import sys, runpy; sys.path.insert(0, sys.argv.pop(1)); "
                "runpy.run_module('server.providers.youtube_worker', "
                "run_name='__main__', alter_sys=True)",
                root,
                str(request),
                str(update or ""),
            ],
            lambda: None,
            RESOLVE_SECONDS,
            max_output_bytes=200_000,
        )

    # Refusals, purge and self-update (D-95) -----------------------------------

    def refused_now(self):
        """YouTube refused (a lookup asked to sign in, or a fresh address was
        refused again). Thread-safe: called from worker threads too."""
        if self.performance is not None:
            self.performance.record("remote-source", "youtube", "refused")
        with self.refusal_lock:
            now = time.monotonic()
            self.refusals = [t for t in self.refusals if now - t < REFUSAL_WINDOW] + [
                now
            ]
            if len(self.refusals) < REFUSALS_BEFORE_REPAIR:
                return
            if now - self.purged_at >= PURGE_SECONDS:
                self.purged_at = now
                shutil.rmtree(self.directory / "cache", ignore_errors=True)
                log.warning("YouTube refused repeatedly: yt-dlp cache purged")
                return
            self.wants_update = True

    def maybe_update(self):
        """Event loop: start a self-update if refusals asked for one."""
        if not self.wants_update:
            return
        self.wants_update = False
        if self.update_task is not None and not self.update_task.done():
            return
        self.update_task = asyncio.ensure_future(asyncio.to_thread(self.update))
        self.update_task.add_done_callback(lambda t: t.cancelled() or t.exception())

    def update(self):
        """Worker thread: fetch yt-dlp's latest official release if it is
        newer than what runs now and its checksum matches the published list."""
        folder = self.directory / "update"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = folder / "checked"
        try:
            if time.time() - stamp.stat().st_mtime < UPDATE_SECONDS:
                return None
        except OSError:
            pass
        stamp.touch()
        session = guarded_client()
        try:
            sums = download(session, RELEASES + "SHA2-256SUMS", 100_000).decode()
            wanted = None
            for line in sums.splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1].lstrip("*") == "yt-dlp":
                    wanted = parts[0].lower()
            if not wanted or not re.fullmatch(r"[0-9a-f]{64}", wanted):
                raise ProviderError("invalid_response")
            body = download(session, RELEASES + "yt-dlp", UPDATE_BYTES)
        finally:
            session.close()
        if hashlib.sha256(body).hexdigest() != wanted:
            log.warning("yt-dlp update refused: checksum mismatch")
            return None
        version = release_version(body)
        current = self.update_path()
        running = (
            json.loads((folder / "verified.json").read_text())["version"]
            if current
            else pinned_version()
        )
        if version_key(version) <= version_key(running):
            return None
        name = f"yt-dlp-{version}.zip"
        handle, temporary = tempfile.mkstemp(dir=folder, prefix=".yt-dlp-")
        with os.fdopen(handle, "wb") as out:
            out.write(body)
        os.replace(temporary, folder / name)
        record = {"version": version, "sha256": wanted, "file": name}
        handle, temporary = tempfile.mkstemp(dir=folder, prefix=".verified-")
        with os.fdopen(handle, "w") as out:
            json.dump(record, out)
        os.replace(temporary, folder / "verified.json")
        for old in folder.glob("yt-dlp-*.zip"):
            if old.name != name:
                old.unlink(missing_ok=True)
        log.warning("yt-dlp updated itself to %s (D-95)", version)
        return version


def download(session, url, limit):
    context = Context("native_standard", deadline=time.monotonic() + 60)
    reply = guarded_stream(session, "GET", url, RELEASE_HOSTS, context)
    try:
        if reply.status_code != 200:
            raise ProviderError("unavailable")
        body = bytearray()
        for chunk in reply.iter_bytes(65536):
            context.remaining()
            body.extend(chunk)
            if len(body) > limit:
                raise ProviderError("limit")
        return bytes(body)
    finally:
        reply.close()


def release_version(body):
    """The version inside a yt-dlp zipapp (yt_dlp/version.py)."""
    import io

    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        text = archive.read("yt_dlp/version.py").decode()
    found = re.search(r"__version__\s*=\s*['\"]([0-9.]+)['\"]", text)
    if not found:
        raise ProviderError("invalid_response")
    return found.group(1)


class YouTubeStream:
    """A YouTube address played through the server: each range the player
    asks for is fetched from YouTube's CDN and passed on as it arrives."""

    def __init__(self, youtube, video_id, address, client):
        self.youtube = youtube
        self.video_id = video_id
        self.address = address
        self.client = client
        self.source = "youtube"

    async def response(self, method, range_header):
        from fastapi.responses import Response, StreamingResponse

        headers = {"Cache-Control": "private, no-store", "Accept-Ranges": "bytes"}
        if method == "HEAD" and self.address.get("size"):
            return Response(
                status_code=200,
                headers={
                    **headers,
                    "Content-Length": str(self.address["size"]),
                    "Content-Type": "audio/mp4",
                },
            )
        upstream = await self.open(method, range_header)
        if upstream.status_code in {401, 403}:
            # The address was refused: one fresh address, then give up.
            await upstream.aclose()
            await asyncio.to_thread(self.youtube.refused_now)
            self.youtube.maybe_update()
            try:
                self.address = await self.youtube.address(self.video_id, fresh=True)
            except (ProviderError, TimeoutError, ValueError):
                return Response(status_code=503, headers=headers)
            upstream = await self.open(method, range_header)
            if upstream.status_code in {401, 403}:
                await upstream.aclose()
                await asyncio.to_thread(self.youtube.refused_now)
                self.youtube.maybe_update()
                return Response(status_code=503, headers=headers)
        if upstream.status_code not in {200, 206}:
            status = 416 if upstream.status_code == 416 else 502
            await upstream.aclose()
            return Response(status_code=status, headers=headers)
        for name in ("Content-Length", "Content-Range"):
            if name in upstream.headers:
                headers[name] = upstream.headers[name]
        if method == "HEAD":
            await upstream.aclose()
            return Response(status_code=upstream.status_code, headers=headers)

        async def body():
            try:
                async for chunk in upstream.aiter_raw(65536):
                    yield chunk
            finally:
                await upstream.aclose()

        return StreamingResponse(
            body(),
            status_code=upstream.status_code,
            headers=headers,
            media_type="audio/mp4",
        )

    async def open(self, method, range_header):
        url = self.address["url"]
        from .providers.network import validate_url

        validate_url(url, AUDIO_HOSTS)
        headers = dict(self.address.get("headers") or {})
        headers["Accept-Encoding"] = "identity"
        if range_header:
            headers["Range"] = range_header
        request = self.client.build_request(
            "GET" if method == "HEAD" else method,
            url,
            headers=headers,
            timeout=httpx.Timeout(10, read=20),
        )
        return await self.client.send(request, stream=True, follow_redirects=False)
