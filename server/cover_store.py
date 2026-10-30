"""Content-addressed local artwork and bounded-memory legacy migration.

Only appdata is written. Library audio and tag files remain read-only.
"""

import base64
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import threading
import time
from pathlib import Path

import ijson
from PIL import Image, ImageOps

HASH = re.compile(r"[0-9a-f]{64}\Z")
WEEK = 7 * 86400


def read_value(events):
    """Consume exactly one JSON value, including nested metadata containers."""
    builder = ijson.ObjectBuilder()
    depth = 0
    for event, value in events:
        builder.event(event, value)
        if event in {"start_map", "start_array"}:
            depth += 1
        elif event in {"end_map", "end_array"}:
            depth -= 1
        if depth == 0:
            return builder.value
    raise ValueError("Incomplete snapshot value")


class CoverStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        # Scanner writes and pruning share this lock for the entire scan.
        self.lock = threading.RLock()
        # Guards only the in-memory cache, so a presentation read never waits
        # for a scan, refresh or prune holding `lock` (LOOP1, 29 Oct).
        self.presentation_lock = threading.Lock()
        self.presentation_cache = {}

    def path(self, sha):
        if not isinstance(sha, str) or not HASH.fullmatch(sha):
            return None
        path = self.directory / (sha + ".jpg")
        return path if path.is_file() and not path.is_symlink() else None

    def save(self, content):
        if not content or len(content) > 5_000_000:
            raise ValueError("Artwork must be between 1 byte and 5 MB")
        with Image.open(io.BytesIO(content)) as image:
            if image.width * image.height > 30_000_000:
                raise ValueError("Artwork dimensions exceed the limit")
            image = ImageOps.exif_transpose(image).convert("RGB")
            image.thumbnail((1024, 1024))
            output = io.BytesIO()
            image.save(output, "JPEG", quality=84)
        data = output.getvalue()
        sha = hashlib.sha256(data).hexdigest()
        with self.lock:
            target = self.directory / (sha + ".jpg")
            if not self.path(sha):
                descriptor, temporary = tempfile.mkstemp(
                    prefix=".cover-", suffix=".tmp", dir=self.directory
                )
                try:
                    with os.fdopen(descriptor, "wb") as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, target)
                finally:
                    Path(temporary).unlink(missing_ok=True)
        self.metadata(sha)
        return sha

    def presentation(self, sha):
        """Read only cached metadata; projection refresh never decodes images."""
        if not self.path(sha):
            return None
        cached = self.presentation_cache.get(sha)
        if cached:
            return cached
        target = self.directory / "metadata" / (sha + "-v1.json")
        try:
            if (
                target.parent.is_symlink()
                or target.is_symlink()
                or target.stat().st_size > 1024
            ):
                return None
            value = json.loads(target.read_text())
            if (
                set(value) != {"version", "sha", "dominant_colour", "width", "height"}
                or value["sha"] != sha
                or value["version"] != 1
                or not re.fullmatch(r"#[0-9a-f]{6}", value["dominant_colour"])
                or not all(
                    type(value[k]) is int and 0 < value[k] <= 1024
                    for k in ("width", "height")
                )
            ):
                return None
        except (OSError, ValueError, TypeError):
            return None
        with self.presentation_lock:
            if len(self.presentation_cache) >= 8192:
                self.presentation_cache.pop(next(iter(self.presentation_cache)))
            self.presentation_cache[sha] = value
        return value

    def metadata(self, sha):
        """Deterministic dominant swatch from the immutable local JPEG only."""
        with self.lock:
            cached = self.presentation(sha)
            if cached:
                return cached
            target = self.path(sha)
            if not target:
                return None
            with Image.open(target) as image:
                width, height = image.size
                image = image.convert("RGB")
                image.thumbnail((32, 32))
                reduced = image.quantize(colors=8, method=Image.Quantize.MEDIANCUT)
                count, index = max(
                    reduced.getcolors(), key=lambda item: (item[0], -item[1])
                )
                rgb = reduced.getpalette()[index * 3 : index * 3 + 3]
            value = {
                "version": 1,
                "sha": sha,
                "dominant_colour": "#" + "".join(f"{v:02x}" for v in rgb),
                "width": width,
                "height": height,
            }
            directory = self.directory / "metadata"
            if directory.is_symlink():
                return None
            directory.mkdir(exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(prefix=".metadata-", dir=directory)
            try:
                with os.fdopen(descriptor, "w") as stream:
                    json.dump(value, stream, sort_keys=True)
                os.replace(temporary, directory / (sha + "-v1.json"))
            finally:
                Path(temporary).unlink(missing_ok=True)
            return self.presentation(sha)

    def migrate(self, path):
        """Read legacy covers one at a time; retain the original for rollback.

        Track metadata is held in memory, but the image map is never built.
        Normalised files are durable before the metadata replacement is made.
        A malformed image or JSON leaves the source snapshot untouched.
        """
        path = Path(path)
        if not path.exists():
            return
        with self.lock, path.open("rb") as stream:
            # Version 3 writers always emit this first. Avoid reparsing metadata
            # on every refresh while keeping legacy key order unrestricted.
            prefix = stream.read(64)
            if prefix.startswith(b'{"schema_version": 3,'):
                return
            stream.seek(3 if prefix.startswith(b"\xef\xbb\xbf") else 0)
            events = iter(ijson.basic_parse(stream, use_float=True))
            if next(events, None) != ("start_map", None):
                raise ValueError("Snapshot must be a JSON object")
            snapshot, covers = {}, {}
            seen = set()
            for event, key in events:
                if event == "end_map":
                    break
                if event != "map_key" or key in seen:
                    raise ValueError("Invalid or duplicate snapshot field")
                seen.add(key)
                if key != "covers":
                    snapshot[key] = read_value(events)
                    continue
                if next(events, None) != ("start_map", None):
                    raise ValueError("Legacy covers must be a JSON object")
                for event, cover_key in events:
                    if event == "end_map":
                        break
                    if event != "map_key" or cover_key in covers:
                        raise ValueError("Invalid or duplicate legacy cover")
                    cover = read_value(events)
                    data = cover.get("data", "")
                    if len(data) > 6_666_668:
                        raise ValueError("Legacy artwork exceeds the limit")
                    covers[cover_key] = self.save(base64.b64decode(data, validate=True))
            if next(events, None) is not None:
                raise ValueError("Unexpected trailing snapshot data")
            if snapshot.get("schema_version", 1) > 3:
                raise ValueError("Unsupported snapshot schema")
            if "covers" not in seen:
                return
            for track in snapshot.get("tracks", []):
                key = (
                    track.get("album_artist", track.get("artist", "Unknown artist"))
                    + "\x1f"
                    + track.get("album", "Singles")
                )
                if key in covers:
                    track.setdefault("cover_sha", covers[key])
                    # The old map cannot tell which edition supplied the image.
                    # Force a read-only tag inspection on the next scan.
                    track["cover_rescan"] = True
            backup = path.with_name("library-snapshot.pre-v3.json")
            if not backup.exists():
                descriptor, temporary = tempfile.mkstemp(
                    prefix=".snapshot-backup-", dir=path.parent
                )
                try:
                    with path.open("rb") as source, os.fdopen(descriptor, "wb") as dest:
                        shutil.copyfileobj(source, dest, length=64 * 1024)
                        dest.flush()
                        os.fsync(dest.fileno())
                    os.replace(temporary, backup)
                finally:
                    Path(temporary).unlink(missing_ok=True)
            self.write_snapshot(path, snapshot)

    @staticmethod
    def write_snapshot(path, snapshot):
        descriptor, temporary = tempfile.mkstemp(prefix=".snapshot-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(
                    {
                        "schema_version": 3,
                        **{
                            k: v
                            for k, v in snapshot.items()
                            if k not in {"schema_version", "covers"}
                        },
                    },
                    stream,
                    ensure_ascii=False,
                )
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def prune(self, references, now=None):
        """Remove only our unreferenced hash files after a seven-day grace."""
        now = time.time() if now is None else now
        removed = 0
        with self.lock:
            for path in self.directory.glob("*.jpg"):
                if (
                    HASH.fullmatch(path.stem)
                    and path.stem not in references
                    and not path.is_symlink()
                    and path.stat().st_mtime < now - WEEK
                ):
                    path.unlink()
                    removed += 1
            variants = self.directory / "variants"
            if variants.is_dir():
                for path in variants.glob("*.webp"):
                    sha = path.name.split("-", 1)[0]
                    if (
                        re.fullmatch(
                            r"[0-9a-f]{64}-v1-(128|256|512|1024|1600)(-soft)?\.webp",
                            path.name,
                        )
                        and HASH.fullmatch(sha)
                        and sha not in references
                        and not self.path(sha)
                        and not path.is_symlink()
                        and path.stat().st_mtime < now - WEEK
                    ):
                        path.unlink()
            metadata = self.directory / "metadata"
            if metadata.is_dir():
                for path in metadata.glob("*-v1.json"):
                    sha = path.name.removesuffix("-v1.json")
                    if (
                        HASH.fullmatch(sha)
                        and sha not in references
                        and not self.path(sha)
                        and not path.is_symlink()
                        and path.stat().st_mtime < now - WEEK
                    ):
                        path.unlink()
                        self.presentation_cache.pop(sha, None)
        return removed
