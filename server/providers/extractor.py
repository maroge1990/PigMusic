"""App-owned identity/format policy around a bounded yt-dlp subprocess."""

import json
import re
import sys
import tempfile
from pathlib import Path

from .contracts import ProviderError, same_name
from .process import run_checked
from ..quality import satisfies

HOSTS = {
    "soundcloud": ("soundcloud.com", "sndcdn.com"),
}
CODECS = {
    "mp3": ("mp3", ".mp3"),
    "aac": ("aac", ".m4a"),
    "opus": ("opus", ".opus"),
    "vorbis": ("vorbis", ".ogg"),
    "flac": ("flac", ".flac"),
}


def recording(info, source):
    """Derive evidence from the upload, never from the requested recording."""
    artist, title, album = (
        info.get("artist"),
        info.get("track"),
        info.get("album") or "",
    )
    evidence = "structured"
    if not (artist and title) and source == "soundcloud":
        parts = (info.get("title") or "").split(" - ", 1)
        if len(parts) == 2 and all(parts):
            artist, title = parts
            evidence = "explicit_credit"
        elif info.get("uploader") and info.get("title"):
            # Official uploads often carry only the song title. The uploader's
            # name is weak evidence (fan accounts look the same), so the worker
            # never transfers these without the owner's review (SC1).
            artist, title = info["uploader"], info["title"]
            evidence = "uploader_name"
    if not (isinstance(artist, str) and isinstance(title, str) and artist and title):
        return None
    if (
        info.get("is_live")
        or info.get("live_status") in {"is_live", "is_upcoming", "post_live"}
        or info.get("has_drm")
        or info.get("is_preview")
    ):
        return None
    try:
        seconds = float(info.get("duration") or 0)
    except (TypeError, ValueError):
        return None
    if not 0 < seconds <= 86400:
        return None
    return {
        "title": title.strip(),
        "channel": artist.strip(),
        "album": album,
        "duration": seconds,
        "isrc": info.get("isrc"),
        "identity_evidence": evidence,
    }


def formats(info, profile):
    results = []
    for fmt in info.get("formats") or []:
        if (
            fmt.get("vcodec") != "none"
            or fmt.get("has_drm")
            or "preview"
            in (str(fmt.get("format_note", "")) + str(fmt.get("format_id", ""))).lower()
        ):
            continue
        raw_codec = fmt.get("acodec") or ""
        codec = "aac" if raw_codec.startswith("mp4a") else raw_codec
        original = (
            fmt.get("format_id") == "download" and fmt.get("format_note") == "Original"
        )
        if original and codec in {"", "unknown"}:
            codec = {
                "mp3": "mp3",
                "m4a": "aac",
                "opus": "opus",
                "ogg": "vorbis",
                "flac": "flac",
            }.get(fmt.get("ext"), "")
        if codec not in CODECS:
            continue
        if fmt.get("protocol") not in {"https", "m3u8_native"}:
            continue
        if fmt.get("protocol") == "m3u8_native" and codec not in {"mp3", "aac"}:
            continue
        extension = CODECS[codec][1]
        # Launch only containers that the app can retain without encoding.
        # WebM Opus is stream-copied by the parent. HLS AAC uses local remux.
        if fmt.get("ext") not in {"mp3", "m4a", "webm", "opus", "ogg", "flac"}:
            continue
        bitrate = fmt.get("abr") or fmt.get("tbr") or 0
        if not satisfies(profile, {"codec": codec, "bitrate": bitrate}):
            # Original downloads omit bitrate/codec metadata. Only try a
            # supported declared type; the measured file must meet the policy.
            possible = (
                profile == "native_standard"
                or profile == "lossless"
                and codec == "flac"
                or profile.startswith("mp3_")
                and codec == "mp3"
                or profile == "aac_256"
                and codec == "aac"
            )
            if not (original and not bitrate and possible):
                continue
        identity = str(fmt.get("format_id") or "")
        if not re.fullmatch(r"[\w.\-]{1,100}", identity):
            continue
        results.append(
            {
                "format_id": identity,
                "extension": extension,
                "original_codec": codec,
                "original_container": fmt.get("ext"),
                "declared_bitrate": bitrate,
                "quality": "Original" if original else f"{bitrate}kbps",
            }
        )
    # Prefer higher declared bitrate within this codec policy; file validation
    # still measures the delivered stream before accepting it.
    return sorted(
        results,
        key=lambda f: (f["format_id"] != "download", -float(f["declared_bitrate"])),
    )


class Extractor:
    def __init__(self, source):
        if source not in HOSTS:
            raise ValueError("Unsupported extractor source")
        self.source = source

    def invoke(self, request, context):
        with tempfile.TemporaryDirectory(prefix=".extractor-") as directory:
            manifest = Path(directory) / "request.json"
            manifest.write_text(
                json.dumps(
                    {
                        **request,
                        "source": self.source,
                        "profile": context.profile,
                        "max_bytes": context.max_bytes,
                    }
                )
            )
            result = run_checked(
                [
                    sys.executable,
                    "-m",
                    "server.providers.extractor_worker",
                    str(manifest),
                ],
                lambda: context.remaining(),
                timeout=context.remaining(),
                max_output_bytes=3_000_000,
            )
            try:
                value = json.loads(result.stdout)
            except (ValueError, UnicodeError) as exc:
                raise ProviderError("invalid_response") from exc
            if result.returncode or "error" in value:
                raise ProviderError(
                    value.get("error", "unavailable"), review=value.get("review")
                )
            return value

    def search(self, target, context, limit=8):
        request = {
            "operation": "search",
            "query": f"{target['artist']} {target['title']}",
            "limit": min(limit, 8),
        }
        return self.invoke(request, context)["candidates"]

    def transfer(self, candidate, path, context):
        # Re-extract immediately before transfer; never persist expiring URLs.
        from ..downloader import matches
        from ..audio import remux_opus, remux_aac, remux_mp3

        with tempfile.TemporaryDirectory(
            prefix=".extractor-audio-", dir=path.parent
        ) as directory:
            result = self.invoke(
                {
                    "operation": "transfer",
                    "provider_track_id": candidate["provider_track_id"],
                    "format_id": candidate["format_id"],
                    "directory": directory,
                    "uploader_approved": candidate.get("uploader_approved") or None,
                },
                context,
            )
            fresh = result["candidate"]
            # The parent never trusts the worker alone: weaker credit than
            # the search found needs the owner's approval (SC1).
            if fresh.get("identity_evidence") == "uploader_name" and not same_name(
                fresh.get("channel"), candidate.get("uploader_approved")
            ):
                raise ProviderError("review_required", review=fresh)
            expected = {
                "artist": candidate["channel"],
                "title": candidate["title"],
                "album": candidate.get("album", ""),
                "duration": candidate["duration"],
                "isrc": candidate.get("isrc"),
            }
            if (
                not matches(expected, fresh)
                or fresh["extension"] != candidate["extension"]
            ):
                raise ProviderError("no_match")
            original = Path(directory) / result["filename"]
            if (
                original.parent != Path(directory)
                or original.is_symlink()
                or not original.is_file()
            ):
                raise ProviderError("invalid_response")
            if not 0 < original.stat().st_size <= context.max_bytes:
                raise ProviderError("limit")
            context.remaining()
            context.progress(original.stat().st_size, original.stat().st_size)
            remuxed = result["remuxed"]
            if remuxed and fresh["original_codec"] == "opus":
                remux_opus(original, path, lambda: context.remaining())
            elif remuxed and fresh["original_codec"] == "aac":
                remux_aac(original, path, lambda: context.remaining())
            elif remuxed and fresh["original_codec"] == "mp3":
                remux_mp3(original, path, lambda: context.remaining())
            elif remuxed:
                # A WebM/HLS stream must never be renamed into another container.
                raise ProviderError("invalid_response")
            else:
                import os

                os.link(original, path)
            return {
                k: fresh[k]
                for k in (
                    "format_id",
                    "original_codec",
                    "original_container",
                    "declared_bitrate",
                    "identity_evidence",
                )
            } | {"remuxed": remuxed}

    def check(self, context):
        self.invoke({"operation": "check"}, context)
