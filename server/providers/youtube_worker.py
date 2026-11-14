"""Isolated YouTube stream lookup for instant listening (#193, D-91).

One video ID in, the address of its AAC audio out: yt-dlp's visionos client,
whose addresses play at once. Nothing is downloaded, no JavaScript runtime or
remote component is offered, plugins are off, and every request goes through
the app's guarded transport to YouTube's own hosts. The parent may name a
self-updated yt-dlp (D-95), checked against yt-dlp's published checksums,
which is imported in place of the pinned one.
"""

import json
import re
import sys
from pathlib import Path

# Before yt-dlp is imported: a verified update replaces the pinned copy.
if __name__ == "__main__" and len(sys.argv) > 2 and sys.argv[2]:
    sys.path.insert(0, sys.argv[2])

import email.message  # noqa: E402
import io  # noqa: E402
import time  # noqa: E402
import urllib.request  # noqa: E402

import httpx  # noqa: E402
from yt_dlp import YoutubeDL  # noqa: E402
from yt_dlp.globals import plugin_dirs  # noqa: E402
from yt_dlp.networking.common import RequestHandler, Response  # noqa: E402
from yt_dlp.networking.exceptions import HTTPError, TransportError  # noqa: E402
from yt_dlp.utils import DownloadError  # noqa: E402
from yt_dlp.version import __version__  # noqa: E402

from .contracts import Context, ProviderError  # noqa: E402
from .network import client, stream, validate_url  # noqa: E402

# Pages, the player API and the audio CDN; nothing else is reachable.
HOSTS = ("youtube.com", "googlevideo.com", "youtubei.googleapis.com")
AUDIO_HOSTS = ("googlevideo.com",)
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
# AAC in MP4 plays in every PigMusic player (Safari, AVPlayer, the web);
# Opus in WebM does not play on the iPhone.
FORMAT = "140/bestaudio[ext=m4a][acodec^=mp4a]"
MAX_BODY = 8_000_000
# The whole lookup; the parent's own limit is a little longer.
DEADLINE = 12


class Quiet:
    def debug(self, *args, **kwargs):
        pass

    info = warning = error = stdout = debug


class GuardedRH(RequestHandler):
    _SUPPORTED_URL_SCHEMES = ("https",)
    _SUPPORTED_PROXY_SCHEMES = ()
    _SUPPORTED_FEATURES = ()

    def __init__(self, *, context, **kwargs):
        super().__init__(**kwargs)
        self.context = context
        self.session = client()

    def _check_extensions(self, extensions):
        super()._check_extensions(extensions)
        for name in ("cookiejar", "timeout", "legacy_ssl", "keep_header_casing"):
            extensions.pop(name, None)

    def _send(self, request):
        try:
            validate_url(request.url, HOSTS)
            jar = self._get_cookiejar(request)
            headers = self._get_headers(request)
            headers["Accept-Encoding"] = "identity"
            cookie_request = urllib.request.Request(request.url, headers=headers)
            jar.add_cookie_header(cookie_request)
            reply = stream(
                self.session,
                request.method,
                request.url,
                HOSTS,
                self.context,
                headers=dict(cookie_request.header_items()),
                content=request.data,
            )
            message = email.message.Message()
            for key, value in reply.headers.multi_items():
                message.add_header(key, value)

            class CookieResponse:
                def info(self):
                    return message

            jar.extract_cookies(CookieResponse(), cookie_request)
            body = bytearray()
            try:
                for chunk in reply.iter_bytes(65536):
                    self.context.remaining()
                    body.extend(chunk)
                    if len(body) > MAX_BODY:
                        raise ProviderError("limit")
            finally:
                reply.close()
            response = Response(
                io.BytesIO(bytes(body)),
                str(reply.url),
                dict(reply.headers),
                reply.status_code,
            )
            if reply.status_code >= 400:
                raise HTTPError(response)
            return response
        except (httpx.HTTPError, ProviderError) as exc:
            raise TransportError(str(exc)) from exc

    def close(self):
        self.session.close()


def resolve(video_id, cache):
    if not isinstance(video_id, str) or not VIDEO_ID.fullmatch(video_id):
        raise ProviderError("invalid_response")
    context = Context(
        "native_standard", deadline=time.monotonic() + DEADLINE, max_bytes=MAX_BODY
    )
    options = {
        "quiet": True,
        "no_warnings": True,
        "logger": Quiet(),
        "noplaylist": True,
        # Player data and signatures kept between lookups (purged by the
        # parent on refusals); False when the parent gives no folder.
        "cachedir": cache or False,
        "socket_timeout": 8,
        "retries": 0,
        "extractor_retries": 0,
        "external_downloader": None,
        "postprocessors": [],
        "js_runtimes": {},
        "remote_components": set(),
        "proxy": "",
        "allow_unplayable_formats": False,
        "format": FORMAT,
        "plugin_dirs": [],
        "extractor_args": {"youtube": {"player_client": ["visionos"]}},
    }
    plugin_dirs.value = []
    with YoutubeDL(options) as ydl:
        director = ydl._request_director
        for handler in director.handlers.values():
            handler.close()
        director.handlers.clear()
        director.add_handler(
            GuardedRH(context=context, logger=Quiet(), cookiejar=ydl.cookiejar)
        )
        info = ydl.extract_info(
            "https://www.youtube.com/watch?v=" + video_id, download=False
        )
    if not info or info.get("id") != video_id:
        raise ProviderError("invalid_response")
    if info.get("is_live") or info.get("live_status") in {
        "is_live",
        "is_upcoming",
        "post_live",
    }:
        raise ProviderError("protected")
    url = info.get("url") or ""
    validate_url(url, AUDIO_HOSTS)
    if info.get("ext") != "m4a" or not str(info.get("acodec") or "").startswith("mp4a"):
        raise ProviderError("no_match")
    expires = re.search(r"[?&]expire=(\d+)", url)
    size = info.get("filesize")
    return {
        "url": url,
        "expires": int(expires.group(1)) if expires else None,
        "size": size if isinstance(size, int) and size > 0 else None,
        "duration": info.get("duration"),
        "headers": {
            k: v
            for k, v in (info.get("http_headers") or {}).items()
            if k.lower() in {"user-agent", "accept", "accept-language"}
        },
        "version": __version__,
    }


def main():
    try:
        request = json.loads(Path(sys.argv[1]).read_text())
        if request.get("operation") == "version":
            # The parent's smoke test of an update (D-95): it imports and runs.
            value = {"version": __version__}
        else:
            value = resolve(request.get("video_id"), request.get("cache"))
    except ProviderError as exc:
        value = {"error": exc.code}
    except DownloadError as exc:
        text = str(exc).lower()
        code = "unavailable"
        if any(
            word in text
            for word in ("sign in", "login", "cookies", "confirm you", "403", "bot")
        ):
            code = "access_required"
        elif any(
            word in text
            for word in ("drm", "private", "copyright", "not available", "removed")
        ):
            code = "protected"
        value = {"error": code}
    except Exception:
        value = {"error": "invalid_response"}
    print(json.dumps(value))
    return 1 if "error" in value else 0


if __name__ == "__main__":
    sys.exit(main())
