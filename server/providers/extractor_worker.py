"""Isolated extractor process, with one enforced network handler and no encoder.

Only fixed searches or canonical public track IDs enter yt-dlp. Plugins,
external downloaders, runtime updates and remote challenge scripts are disabled.
All extractor HTTP requests (including HLS) use the app's guarded transport.
"""

import email.message
import io
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

import httpx
from yt_dlp import YoutubeDL
from yt_dlp.globals import plugin_dirs
from yt_dlp.downloader.external import FFmpegFD
from yt_dlp.networking.common import RequestHandler, Response
from yt_dlp.networking.exceptions import HTTPError, TransportError
from yt_dlp.utils import DownloadError

from .contracts import Context, ProviderError, same_name
from .extractor import HOSTS, formats, recording
from .network import client, stream, validate_url


class Quiet:
    def debug(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass

    def stdout(self, *args, **kwargs):
        pass


class LimitedBody(io.RawIOBase):
    def __init__(self, response, context, budget):
        self.response, self.context, self.budget = response, context, budget
        self.iterator = response.iter_bytes(65536)
        self.buffer = bytearray()
        self.finished = False

    def readable(self):
        return True

    def read(self, size=-1):
        size = -1 if size is None else size
        self.context.remaining()
        while not self.finished and (size < 0 or len(self.buffer) < size):
            try:
                chunk = next(self.iterator)
            except StopIteration:
                self.finished = True
                break
            self.budget[0] += len(chunk)
            if self.budget[0] > self.context.max_bytes + 10_000_000:
                raise ProviderError("limit")
            self.buffer.extend(chunk)
        length = len(self.buffer) if size < 0 else min(size, len(self.buffer))
        result = bytes(self.buffer[:length])
        del self.buffer[:length]
        return result

    def close(self):
        self.response.close()
        super().close()


class GuardedRH(RequestHandler):
    _SUPPORTED_URL_SCHEMES = ("https",)
    _SUPPORTED_PROXY_SCHEMES = ()
    _SUPPORTED_FEATURES = ()

    def __init__(self, *, source, context, **kwargs):
        super().__init__(**kwargs)
        self.source, self.context = source, context
        self.session = client()
        self.budget = [0]

    def _check_extensions(self, extensions):
        super()._check_extensions(extensions)
        for name in ("cookiejar", "timeout", "legacy_ssl", "keep_header_casing"):
            extensions.pop(name, None)

    def _send(self, request):
        try:
            validate_url(request.url, HOSTS[self.source])
            jar = self._get_cookiejar(request)
            headers = self._get_headers(request)
            headers["Accept-Encoding"] = "identity"
            cookie_request = urllib.request.Request(request.url, headers=headers)
            jar.add_cookie_header(cookie_request)
            headers = dict(cookie_request.header_items())
            reply = stream(
                self.session,
                request.method,
                request.url,
                HOSTS[self.source],
                self.context,
                headers=headers,
                content=request.data,
            )
            message = email.message.Message()
            for key, value in reply.headers.multi_items():
                message.add_header(key, value)

            class CookieResponse:
                def info(self):
                    return message

            jar.extract_cookies(CookieResponse(), cookie_request)
            response = Response(
                LimitedBody(reply, self.context, self.budget),
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


def canonical(source, identity):
    # SoundCloud public permalink paths must remain inside its canonical host.
    if source == "soundcloud" and re.fullmatch(
        r"[A-Za-z0-9_-]{1,150}/[A-Za-z0-9_-]{1,200}", identity
    ):
        return "https://soundcloud.com/" + identity
    raise ProviderError("invalid_response")


def candidate(info, source, profile):
    fields = recording(info, source)
    choices = formats(info, profile)
    if not fields or not choices:
        return None
    from urllib.parse import urlsplit

    url = info.get("webpage_url") or ""
    validate_url(url, ("soundcloud.com",))
    identity = urlsplit(url).path.strip("/")
    canonical(source, identity)
    return {**fields, **choices[0], "source": source, "provider_track_id": identity}


def search(ydl, query, source, profile, limit):
    listing = ydl.extract_info(query, download=False)
    results = []
    for entry in list((listing or {}).get("entries") or [])[:limit]:
        if not entry:
            continue
        try:
            url = entry.get("webpage_url") or entry.get("url") or ""
            validate_url(url, ("soundcloud.com",))
            info = ydl.extract_info(url, download=False)
            result = candidate(info, source, profile)
            if result:
                results.append(result)
                results.extend(
                    {**result, **alternative}
                    for alternative in formats(info, profile)[1:3]
                )
        except DownloadError as exc:
            # Private/protected/preview results are candidate misses;
            # network outages still fail the whole source operation.
            if not any(
                word in str(exc).lower()
                for word in (
                    "drm",
                    "private",
                    "not available",
                    "copyright",
                    "preview",
                    "not found",
                )
            ):
                raise
    return results


def execute(request):
    source, profile = request["source"], request["profile"]
    if source not in HOSTS:
        raise ProviderError("invalid_response")
    context = Context(profile, max_bytes=request.get("max_bytes", 160_000_000))
    options = {
        "quiet": True,
        "no_warnings": True,
        "logger": Quiet(),
        "noplaylist": True,
        "cachedir": False,
        "socket_timeout": 10,
        "retries": 1,
        "extractor_retries": 1,
        "fragment_retries": 1,
        "concurrent_fragment_downloads": 1,
        "skip_unavailable_fragments": False,
        "hls_prefer_native": True,
        "external_downloader": None,
        "postprocessors": [],
        "max_filesize": context.max_bytes,
        # SoundCloud needs no JavaScript runtime; none is offered.
        "js_runtimes": {},
        "remote_components": set(),
        "proxy": "",
        "extract_flat": "in_playlist",
        "allow_unplayable_formats": False,
        "fixup": "never",
        "overwrites": False,
        "noprogress": True,
        "format": "bestaudio",
        "plugin_dirs": [],
    }
    plugin_dirs.value = []

    # HlsFD can delegate unsupported manifests to FFmpeg even with native HLS
    # requested. FFmpeg may only handle local remuxes in the parent process.
    def forbid_external(*args, **kwargs):
        raise ProviderError("network_policy")

    FFmpegFD.real_download = forbid_external
    with YoutubeDL(options) as ydl:
        director = ydl._request_director
        for handler in director.handlers.values():
            handler.close()
        director.handlers.clear()
        director.add_handler(
            GuardedRH(
                source=source, context=context, logger=Quiet(), cookiejar=ydl.cookiejar
            )
        )
        operation = request["operation"]
        if operation == "check":
            with ydl.urlopen("https://soundcloud.com") as response:
                response.read(65536)
            return {"metadata_reachable": True}
        if operation == "search":
            limit = min(request.get("limit", 8), 8)
            query = f"scsearch{limit}:" + request["query"]
            ydl.params["playlistend"] = limit
            return {"candidates": search(ydl, query, source, profile, limit)}
        if operation != "transfer":
            raise ProviderError("invalid_response")
        info = ydl.extract_info(
            canonical(source, request["provider_track_id"]), download=False
        )
        choices = formats(info, profile)
        choice = next(
            (f for f in choices if f["format_id"] == request["format_id"]), None
        )
        fields = recording(info, source)
        if not choice or not fields:
            raise ProviderError("no_match")
        # Re-extraction can find weaker credit than the search did; an
        # uploader-only credit downloads only with the owner's approval of
        # this very uploader. The refusal carries what was found, so the
        # review shows the fresh uploader, not the search's credit.
        if fields["identity_evidence"] == "uploader_name" and not same_name(
            fields["channel"], request.get("uploader_approved")
        ):
            raise ProviderError("review_required", review=fields)
        selected = next(
            f for f in info["formats"] if str(f.get("format_id")) == choice["format_id"]
        )
        directory = Path(request["directory"])
        if not directory.is_dir() or directory.is_symlink():
            raise ProviderError("invalid_response")
        filename = "audio." + selected["ext"]
        ydl.params["outtmpl"] = {"default": str(directory / filename)}

        def progress(value):
            context.remaining()
            if (
                sum(p.stat().st_size for p in directory.iterdir() if p.is_file())
                > context.max_bytes
            ):
                raise ProviderError("limit")

        ydl.add_progress_hook(progress)
        # Use only this already checked single audio format, no implicit muxing.
        download_info = {**info, **selected}
        for key in (
            "requested_formats",
            "requested_downloads",
            "__postprocessors",
            "section_start",
            "section_end",
        ):
            download_info.pop(key, None)
        ydl.process_info(download_info)
        fresh = {
            **fields,
            **choice,
            "source": source,
            "provider_track_id": request["provider_track_id"],
        }
        remuxed = selected["ext"] == "webm" or selected["protocol"] == "m3u8_native"
        return {"candidate": fresh, "filename": filename, "remuxed": remuxed}


def main():
    try:
        request = json.loads(Path(sys.argv[1]).read_text())
        value = execute(request)
    except ProviderError as exc:
        value = {"error": exc.code}
        if exc.review:
            value["review"] = exc.review
    except DownloadError as exc:
        text = str(exc).lower()
        code = "unavailable"
        if any(
            word in text
            for word in ("sign in", "login", "cookies", "confirm you", "403")
        ):
            code = "access_required"
        elif any(
            word in text
            for word in ("drm", "private", "copyright", "not available", "not found")
        ):
            code = "protected"
        elif "allowed network policy" in text:
            code = "network_policy"
        value = {"error": code}
    except Exception:
        value = {"error": "invalid_response"}
    print(json.dumps(value))
    return 1 if "error" in value else 0


if __name__ == "__main__":
    sys.exit(main())
