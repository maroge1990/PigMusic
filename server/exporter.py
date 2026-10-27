"""Publish validated managed downloads with a durable, restartable export record.

Unrelated NAS files are never overwritten. Managed quality replacements retain
guarded originals until validated publication. NAS mode requires explicit mount
identity/configuration; isolated previews remain the default.
"""

from .lifecycle import coordinated

import asyncio
import hashlib
import json
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

from .downloader import safe_name
from .library import load_json, normalise
from .storage import library_root


# "CD 01", "Disc 2", "Disk02": one disc of an album kept in its own folder.
DISC_FOLDER = re.compile(r"(?:cd|dis[ck])[ _-]*0*(\d+)", re.IGNORECASE)


def disc_folder(candidates, disc):
    """The one folder among an album's disc folders that holds `disc`, else
    None. Each folder's disc is its songs' disc number when they agree, else
    the number in its name; folders that are not siblings named as discs are
    different albums and never chosen between."""
    paths = {Path(folder): discs for folder, discs in candidates.items()}
    if len({path.parent for path in paths}) != 1:
        return None
    found = []
    for path, discs in paths.items():
        named = DISC_FOLDER.fullmatch(path.name)
        if not named:
            return None
        recorded = {d for d in discs if d}
        number = recorded.pop() if len(recorded) == 1 else int(named[1])
        if number == disc:
            found.append(path.as_posix())
    return found[0] if len(found) == 1 else None


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def contained(root, relative):
    value = Path(relative)
    if value.is_absolute() or ".." in value.parts or ":" in str(value):
        raise ValueError("Invalid export path.")
    target = (root / value).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError("Export escaped the configured library.")
    cursor = root.resolve()
    for part in value.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("Export paths cannot contain symlinks.")
    return target


def fingerprint(path):
    info = os.stat(path)
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def settled(identity, now=None):
    return identity[3] < (time.time_ns() if now is None else now) - 2_000_000_000


class Exporter:
    def __init__(self, store, root=None):
        self.store = store
        self.root = Path(root or os.environ.get("PIG_MUSIC_LIBRARY", "/library"))
        self.lock = threading.Lock()
        self.stopping = asyncio.Event()
        # SHA-256 by path and file identity (device, inode, size, modification
        # time), so an unchanged pass reads no audio and hashing happens before
        # lifecycle_lock is taken (LOCK1). A changed file is hashed again.
        # Metadata cannot see an edit that restores the modification time, so
        # only the read-only check of a song already published trusts an
        # earlier pass; publishing trusts only a read made in this pass, and
        # deleting the source needs a hard link or a read made just now.
        self.digests = {}
        self.pass_id = 0

    def digest(self, path, this_pass=False):
        key = os.fspath(path)
        before = fingerprint(path)
        cached = self.digests.get(key)
        if (
            cached
            and cached[0] == before
            and (not this_pass or cached[2] == self.pass_id)
        ):
            return cached[1]
        started = time.time_ns()
        value = digest(path)
        # A file written moments ago could change again within one timestamp
        # tick without its size changing: remember only settled files.
        if fingerprint(path) == before and settled(before, started):
            if len(self.digests) > 20000:
                self.digests.clear()
            self.digests[key] = (before, value, self.pass_id)
        return value

    def linked(self, path, source, expected):
        """Whether `path` holds the verified source's bytes: a hard link of it,
        or read again now. This decides whether the source may be deleted."""
        try:
            return os.path.samefile(path, source) or digest(path) == expected
        except OSError:
            return False

    def paths(self, track):
        """The download's source and its NAS destination."""
        source_root = library_root(self.store)
        if not contained(source_root, track["relative_path"]).is_file():
            legacy = self.store.directory / "downloads" / "library"
            if contained(legacy, track["relative_path"]).is_file():
                source_root = legacy
        source = contained(source_root, track["relative_path"])
        relative = track.get("export_relative_path") or self.relative(track)
        return source, relative, contained(self.root, relative)

    def warm(self, track):
        """Hash what publication will check, outside lifecycle_lock. False
        when a file was written moments ago: it waits for the next pass, so
        publication never hashes it under the lock."""
        try:
            source, _, target = self.paths(track)
            # A song already published whose download is gone only needs its
            # copy checked: an earlier pass's read will do. Anything else
            # publishes, so it is read in this pass.
            published = track.get("export_relative_path") and not source.is_file()
            for path in (source, target):
                if path.is_file():
                    if not settled(fingerprint(path)):
                        return False
                    self.digest(path, this_pass=not published)
        except (OSError, ValueError, KeyError):
            pass  # publish() reports it.
        return True

    def current(self, conn, track):
        """The download as it stands now: still the same file, not in Trash."""
        row = conn.execute(
            "SELECT payload FROM acquired WHERE id=?", (track["id"],)
        ).fetchone()
        if not row or json.loads(row[0]).get("sha256") != track["sha256"]:
            return False
        for (raw,) in conn.execute(
            "SELECT payload FROM documents WHERE name LIKE 'removed:%'"
        ):
            value = json.loads(raw)
            if value.get("track_id") == track["id"] and value.get("status") in {
                "removing",
                "removed",
            }:
                return False
        return True

    def config(self):
        return load_json(self.store.directory / "library-export.json")

    def records(self):
        """Downloads to publish; one in Trash waits until it is restored,
        so its missing file never holds up the rest."""
        with self.store.connect() as conn:
            removed = {
                json.loads(v)["track_id"]
                for (v,) in conn.execute(
                    "SELECT payload FROM documents WHERE name LIKE 'removed:%'"
                )
                if json.loads(v).get("status") in {"removing", "removed"}
            }
            return [
                track
                for track in (
                    json.loads(row[0])
                    for row in conn.execute("SELECT payload FROM acquired")
                )
                if track["id"] not in removed
            ]

    def relative(self, track):
        component = (
            lambda value: safe_name(value)
            .encode("utf-8")[:220]
            .decode("utf-8", "ignore")
        )
        album = component(track["album"]) + (
            f" ({track['year']})" if track.get("year") else ""
        )
        folder = Path(component(track["album_artist"])) / album
        # Preserve the existing folder spelling and edition context when tags
        # identify exactly one matching album folder.
        candidates = {}
        snapshot = load_json(self.store.directory / "library-snapshot.json")
        for saved in snapshot.get("tracks", []):
            if normalise(saved.get("album_artist", "")) != normalise(
                track["album_artist"]
            ):
                continue
            if normalise(saved.get("album", "")) != normalise(track["album"]):
                continue
            if (
                track.get("year")
                and saved.get("year")
                and str(track["year"])[:4] != str(saved["year"])[:4]
            ):
                continue
            if (
                track.get("musicbrainz_release_id")
                and saved.get("musicbrainz_release_id")
                and track["musicbrainz_release_id"] != saved["musicbrainz_release_id"]
            ):
                continue
            parent = Path(saved.get("relative_path", "")).parent
            if len(parent.parts) >= 2:
                candidates.setdefault(parent.as_posix(), set()).add(
                    saved.get("disc_number")
                )
        if len(candidates) > 1:
            chosen = disc_folder(candidates, track.get("disc_number") or 1)
            if not chosen:
                raise ValueError(
                    "Multiple existing album folders need a destination decision."
                )
            candidates = {chosen: set()}
        if len(candidates) == 1:
            folder = Path(next(iter(candidates)))
        number = (
            f"{track['disc_number']:02}-{track['track_number']:02}"
            if track["disc_number"] > 1
            else f"{track['track_number']:02}"
        )
        label = f"{component(track['artist'])} - {component(track['album'])} - {number} - {component(track['title'])}"
        filename = (
            label.encode("utf-8")[:200].decode("utf-8", "ignore")
            + Path(track["relative_path"]).suffix
        )
        relative = (folder / filename).as_posix()
        # A same-extension quality replacement must coexist with the original
        # until publication succeeds. Unknown collisions remain review errors.
        target = contained(self.root, relative)
        if target.is_file() and self.digest(target) != track["sha256"]:
            with self.store.connect() as conn:
                old = [
                    json.loads(r[0])
                    for r in conn.execute(
                        "SELECT payload FROM quality_cleanup WHERE track_id=?",
                        (track["id"],),
                    )
                ]
            if any(
                (v.get("export_relative_path") or v.get("nas_relative_path"))
                == relative
                and v.get("sha256") == self.digest(target)
                for v in old
            ):
                relative = (
                    folder
                    / (
                        Path(filename).stem
                        + f" [{track['sha256'][:8]}]"
                        + Path(filename).suffix
                    )
                ).as_posix()
        return relative

    def plan(self):
        """Every download's destination. A song that needs a decision is
        listed with its reason, as `process()` leaves it waiting, so one
        ambiguous album never hides the rest of the plan."""
        items = []
        needs_review = []
        for track in self.records():
            item = {
                "id": track.get("id"),
                "artist": track.get("artist"),
                "album": track.get("album"),
                "title": track.get("title"),
                "exported": bool(track.get("export_relative_path")),
            }
            try:
                relative = track.get("export_relative_path") or self.relative(track)
                target = contained(self.root, relative)
            except (OSError, ValueError, KeyError) as exc:
                item.update(
                    destination=None,
                    collision=False,
                    error=str(exc)[:180],
                )
                needs_review.append(item)
            else:
                item.update(
                    destination=relative,
                    collision=target.exists() and not item["exported"],
                )
            items.append(item)
        return {
            "enabled": self.config().get("enabled") is True,
            "destination": "Existing artist/album folders",
            "existing_files_unchanged": True,
            "items": items,
            "needs_review": needs_review,
            "status": self.store.read_document("library-export-status"),
        }

    def report(self, **value):
        with self.store.connect() as conn:
            conn.execute(
                "INSERT INTO documents VALUES ('library-export-status',?) ON CONFLICT(name) DO UPDATE SET payload=excluded.payload",
                (json.dumps(value),),
            )

    @coordinated
    def publish(self, track):
        with self.store.connect() as conn:
            if not self.current(conn, track):
                return False  # Replaced or removed since this pass began.
        source, relative, target = self.paths(track)
        expected = track["sha256"]
        if not source.is_file():
            if (
                track.get("export_relative_path")
                and target.is_file()
                and self.digest(target) == expected
            ):
                return
            raise ValueError("Validated download is unavailable; export deferred.")
        if self.digest(source, this_pass=True) != expected:
            raise ValueError("Validated download changed; export refused.")
        if target.exists():
            if not target.is_file() or self.digest(target, this_pass=True) != expected:
                raise ValueError(
                    "Destination contains different content; export refused."
                )
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            stage = contained(
                self.root,
                str(
                    Path(relative).parent
                    / (".pig-music-" + uuid.uuid4().hex + ".pig-part")
                ),
            )
            try:
                if source.stat().st_dev == target.parent.stat().st_dev:
                    os.link(
                        source, stage
                    )  # Same NAS: publish without a second audio copy.
                else:
                    with source.open("rb") as incoming, stage.open("wb") as outgoing:
                        shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
                        outgoing.flush()
                        os.fsync(outgoing.fileno())
                if not self.linked(stage, source, expected):
                    raise ValueError("Export verification failed.")
                # Hard-link publication is atomic and cannot replace a competing
                # file. Both files are on the same NAS filesystem.
                try:
                    os.link(stage, target)
                except FileExistsError:
                    if not self.linked(target, source, expected):
                        raise ValueError("Destination changed during export.")
            finally:
                stage.unlink(missing_ok=True)
        if not self.linked(target, source, expected):
            raise ValueError("Published file verification failed.")
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT payload FROM acquired WHERE id=?", (track["id"],)
            ).fetchone()
            if not row or json.loads(row[0]).get("sha256") != expected:
                return
            value = {**json.loads(row[0]), "export_relative_path": relative}
            conn.execute(
                "UPDATE acquired SET payload=? WHERE id=?",
                (json.dumps(value), track["id"]),
            )
        # The NAS copy is verified and its location durably committed before
        # deleting the staging-library copy. Restart can finish this cleanup.
        if source.exists() and self.digest(source, this_pass=True) == expected:
            source.unlink()

    def process(self):
        # Not one lifecycle_lock hold for the whole pass: each song's
        # publication takes it (LOCK1), after its files are hashed.
        if self.config().get("enabled") is not True or not self.lock.acquire(
            blocking=False
        ):
            return
        self.pass_id += 1
        try:
            if not self.root.is_dir():
                raise ValueError("Library mount unavailable; export deferred.")
            config = self.config()
            marker = load_json(
                contained(
                    self.root,
                    config.get("marker_path", "Pig Music/.pig-music-library.json"),
                )
            )
            if (
                not config.get("library_id")
                or marker.get("library_id") != config["library_id"]
            ):
                raise ValueError(
                    "The expected library marker is unavailable; export deferred."
                )
            completed = 0
            changed = False
            waiting = []
            for track in self.records():
                # One song that needs a decision (or whose file is refused)
                # waits on its own; the rest still publish.
                if not self.warm(track):
                    continue
                try:
                    if self.publish(track) is False:
                        continue
                except (OSError, ValueError, KeyError) as exc:
                    waiting.append(
                        {
                            "id": track.get("id"),
                            "artist": track.get("album_artist"),
                            "album": track.get("album"),
                            "title": track.get("title"),
                            "error": str(exc)[:180],
                        }
                    )
                    continue
                completed += 1
                changed = changed or not track.get("export_relative_path")
            if changed:
                self.store.refresh_library()
            self.store.cleanup_replacements()
            if waiting:
                count = len(waiting)
                self.report(
                    status="partial",
                    files=completed,
                    error=f"{count} song{'s' if count > 1 else ''} need review: {waiting[0]['error']}",
                    needs_review=waiting[:50],
                )
            else:
                self.report(status="completed", files=completed)
        except (OSError, ValueError, KeyError) as exc:
            self.report(status="deferred", error=str(exc)[:180])
        finally:
            self.lock.release()

    async def run(self):
        while not self.stopping.is_set():
            await asyncio.to_thread(self.process)
            try:
                await asyncio.wait_for(self.stopping.wait(), timeout=15)
            except TimeoutError:
                pass
