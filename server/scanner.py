"""Application-owned, read-only ingestion. No Music Grabber index is required."""

from .lifecycle import coordinated

import hashlib
import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from mutagen import File
from .audio import measured_bitrate

EXTENSIONS = {
    ".mp3",
    ".flac",
    ".m4a",
    ".aac",
    ".ogg",
    ".opus",
    ".wav",
    ".aiff",
    ".aif",
    ".wma",
    ".ape",
    ".wv",
}
IGNORED = {
    "#recycle",
    "@eadir",
    ".trash",
    "trash",
    ".staging",
    ".pig-music-trash",
    ".pig-music-work",
}


def tag(tags, *keys, default=""):
    for key in keys:
        try:
            value = tags.get(key)
        except (ValueError, KeyError):
            continue  # Vorbis rejects MP4's non-ASCII tag names.
        if value is None:
            continue
        if hasattr(value, "text"):
            value = value.text
        while isinstance(value, (list, tuple)):
            value = value[0] if value else ""
        if isinstance(value, bytes):
            value = value.decode("utf-8", "replace")
        if str(value).strip():
            return str(value).strip()
    return default


def number(value, default=0):
    found = re.search(r"\d+", str(value))
    return int(found[0]) if found else default


def inspect_file(path, relative):
    audio = File(path)
    if audio is None or audio.info is None:
        raise ValueError("Unrecognised audio file")
    tags = audio.tags or {}
    artist = tag(tags, "TPE1", "artist", "\xa9ART", default="Unknown artist")
    codec = {
        "MP3": "mp3",
        "FLAC": "flac",
        "MP4": "aac",
        "OggVorbis": "vorbis",
        "OggOpus": "opus",
        "WAVE": "wav",
    }.get(type(audio).__name__, path.suffix.lstrip(".").lower())
    if (
        type(audio).__name__ == "MP4"
        and "alac" in str(getattr(audio.info, "codec", "")).casefold()
    ):
        codec = "alac"
    stat = path.stat()
    bitrate = round(getattr(audio.info, "bitrate", 0) / 1000)
    if codec in {"opus", "vorbis"} and not bitrate:
        bitrate = measured_bitrate(path, audio.info.length)
    track = {
        "id": hashlib.sha256(relative.encode()).hexdigest()[:24],
        "title": tag(tags, "TIT2", "title", "\xa9nam", default=path.stem),
        "artist": artist,
        "album_artist": tag(
            tags, "TPE2", "albumartist", "album artist", "aART", default=artist
        ),
        "album": tag(
            tags, "TALB", "album", "\xa9alb", default=path.parent.name or "Singles"
        ),
        "genre": tag(tags, "TCON", "genre", "\xa9gen"),
        "compilation": tag(tags, "TCMP", "compilation", "cpil").casefold()
        in {"1", "true"},
        "year": number(tag(tags, "TDRC", "date", "year", "\xa9day")),
        "track_number": number(tag(tags, "TRCK", "tracknumber", "trkn")),
        "disc_number": number(tag(tags, "TPOS", "discnumber", "disk"), 1) or 1,
        "duration": round(audio.info.length, 3),
        "codec": codec,
        "bitrate": bitrate,
        "relative_path": relative,
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "available": True,
    }
    for field, keys in {
        "musicbrainz_release_id": (
            "musicbrainz_albumid",
            "TXXX:MusicBrainz Album Id",
            "----:com.apple.iTunes:MusicBrainz Album Id",
        ),
        "musicbrainz_recording_id": (
            "musicbrainz_trackid",
            "TXXX:MusicBrainz Track Id",
            "----:com.apple.iTunes:MusicBrainz Track Id",
        ),
    }.items():
        value = tag(tags, *keys)
        if value:
            track[field] = value
    isrc = tag(tags, "TSRC", "isrc")
    if isrc:
        track["isrcs"] = [isrc]
    pictures = getattr(audio, "pictures", []) or (
        tags.getall("APIC") if hasattr(tags, "getall") else []
    )
    front = next((p for p in pictures if getattr(p, "type", 3) == 3), None) or (
        pictures[0] if pictures else None
    )
    cover = front.data if front else next(iter(tags.get("covr", [])), None)
    if cover and len(cover) <= 5_000_000:
        return track, bytes(cover)
    return track, None


class LibraryScanner:
    def __init__(self, store, root=None):
        self.store = store
        self.root = Path(root or os.environ.get("PIG_MUSIC_LIBRARY", "/library"))
        self.lock = threading.Lock()
        self.stopping = threading.Event()

    def status(self):
        return self.store.read_document("library-scan")

    def report(self, **details):
        with self.store.connect() as conn:
            conn.execute(
                "INSERT INTO documents VALUES ('library-scan',?) ON CONFLICT(name) DO UPDATE SET payload=excluded.payload",
                (json.dumps(details),),
            )

    @coordinated
    def run(self):
        if not self.lock.acquire(blocking=False):
            return self.status()
        self.store.covers.lock.acquire()
        try:
            root = self.root.resolve()
            if not root.is_dir():
                raise ValueError(
                    "The configured music folder is unavailable. The previous index is retained."
                )
            previous = (
                json.loads(
                    (self.store.directory / "library-snapshot.json").read_text(
                        encoding="utf-8-sig"
                    )
                )
                if (self.store.directory / "library-snapshot.json").exists()
                else {}
            )
            old = {
                t["relative_path"]: t
                for t in previous.get("tracks", [])
                if t.get("relative_path")
            }
            tracks = []
            cover_hashes = {}
            failures = []
            directory_errors = []
            self.report(status="scanning", files=0)
            for folder, dirs, files in os.walk(
                root, followlinks=False, onerror=directory_errors.append
            ):
                dirs[:] = sorted(
                    d
                    for d in dirs
                    if d.casefold() not in IGNORED and not Path(folder, d).is_symlink()
                )
                for name in sorted(files):
                    if self.stopping.is_set():
                        raise ValueError(
                            "Scan interrupted. The previous index is retained."
                        )
                    path = Path(folder, name)
                    if (
                        name.startswith(".pig-music-")
                        or path.suffix.lower() not in EXTENSIONS
                        or path.is_symlink()
                        or not path.resolve().is_relative_to(root)
                    ):
                        continue
                    relative = path.relative_to(root).as_posix()
                    try:
                        stat = path.stat()
                        cached = old.get(relative)
                        if (
                            cached
                            and cached.get("size_bytes") == stat.st_size
                            and cached.get("mtime_ns") == stat.st_mtime_ns
                            and not cached.get("cover_rescan")
                            and (
                                not cached.get("cover_sha")
                                or self.store.covers.path(cached["cover_sha"])
                            )
                        ):
                            track = {**cached, "available": True}
                            cover = None
                        else:
                            track, cover = inspect_file(path, relative)
                            if cached:
                                track["id"] = cached["id"]
                        try:
                            if cover:
                                raw_sha = hashlib.sha256(cover).hexdigest()
                                if raw_sha not in cover_hashes:
                                    cover_hashes[raw_sha] = self.store.covers.save(
                                        cover
                                    )
                                track["cover_sha"] = cover_hashes[raw_sha]
                                track["cover_source"] = "embedded"
                            elif track.get("cover_source") != "embedded":
                                # Folder artwork has its own fingerprint: replacing
                                # it must not require modifying the audio file.
                                image = next(
                                    (
                                        path.parent / name
                                        for name in (
                                            "cover.jpg",
                                            "folder.jpg",
                                            "cover.png",
                                            "folder.png",
                                        )
                                        if (path.parent / name).is_file()
                                        and not (path.parent / name).is_symlink()
                                        and (path.parent / name)
                                        .resolve()
                                        .is_relative_to(root)
                                        and (path.parent / name).stat().st_size
                                        <= 5_000_000
                                    ),
                                    None,
                                )
                                signature = None
                                if image:
                                    image_stat = image.stat()
                                    signature = [
                                        image.relative_to(root).as_posix(),
                                        image_stat.st_size,
                                        image_stat.st_mtime_ns,
                                    ]
                                if (
                                    signature != track.get("cover_signature")
                                    or track.get("cover_rescan")
                                    or (
                                        image
                                        and not self.store.covers.path(
                                            track.get("cover_sha")
                                        )
                                    )
                                ):
                                    track.pop("cover_sha", None)
                                    if image:
                                        raw = image.read_bytes()
                                        raw_sha = hashlib.sha256(raw).hexdigest()
                                        if raw_sha not in cover_hashes:
                                            cover_hashes[raw_sha] = (
                                                self.store.covers.save(raw)
                                            )
                                        track["cover_sha"] = cover_hashes[raw_sha]
                                track["cover_source"] = "folder"
                                track["cover_signature"] = signature
                            track.pop("cover_rescan", None)
                        except (ValueError, OSError) as exc:
                            # Invalid display art must not hide playable audio.
                            track.pop("cover_sha", None)
                            track["cover_rescan"] = True
                            failures.append(
                                {
                                    "path": relative,
                                    "error": "Artwork: " + str(exc)[:140],
                                }
                            )
                        tracks.append(track)
                    except Exception as exc:
                        failures.append(
                            {
                                "path": relative,
                                "error": (str(exc) or type(exc).__name__)[:160],
                            }
                        )
                        if relative in old:
                            tracks.append(old[relative])
                    if len(tracks) % 100 == 0:
                        self.report(status="scanning", files=len(tracks))
            if directory_errors or old and not tracks:
                raise ValueError(
                    "Couldn't read the whole music folder. The previous index is kept."
                )
            present = {t["relative_path"] for t in tracks}
            missing = [
                {**t, "available": False}
                for path, t in old.items()
                if path not in present
            ]
            payload = {
                "schema_version": 3,
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "source": previous.get("source") or str(root),
                "tracks": tracks + missing,
                "read_failures": failures,
            }
            self.store.covers.write_snapshot(
                self.store.directory / "library-snapshot.json", payload
            )
            self.store.refresh_library()
            self.report(
                status="completed",
                files=len(tracks),
                missing=len(missing),
                read_failures=len(failures),
                completed_at=payload["captured_at"],
            )
        except (OSError, ValueError) as exc:
            self.report(status="failed", error=str(exc)[:200])
        finally:
            self.store.covers.lock.release()
            self.lock.release()
        return self.status()
