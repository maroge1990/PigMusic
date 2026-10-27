"""Validated source downloads retained by mix editions, separate from NAS publication."""

import hashlib
import json
import shutil
import time
import threading
from contextlib import contextmanager
from pathlib import Path

from .downloader import Downloader, Cancelled, QualityDeadline
from .library import normalise
from .media import track_file
from .quality import satisfies


def song_key(track):
    # Release identity matters even when recordings share a title or an MBID.
    identity = [
        track.get("musicbrainz_release_id"),
        track.get("musicbrainz_recording_id"),
        *[normalise(track.get(k) or "") for k in ("artist", "title", "album")],
    ]
    if all(identity[:2]):
        identity = identity[:2]
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


class MixCache:
    def __init__(self, store):
        self.store = store
        self.download_lock = threading.Lock()
        self.directory = store.directory / "mix-cache"
        self.directory.mkdir(exist_ok=True)
        self.staging = self.directory / "staging"
        self.staging.mkdir(exist_ok=True)

    def settings(self):
        return {
            "budget_bytes": 50_000_000_000,
            "reserve_bytes": 2_000_000_000,
            "profile": "mp3_320",
            "timezone": "Australia/Melbourne",
            **self.store.read_document("mix-settings"),
        }

    def status(self):
        with self.store.connect() as conn:
            now = time.time()
            count, size = conn.execute(
                "SELECT COUNT(*),COALESCE(SUM(bytes),0) FROM mix_audio"
            ).fetchone()
            ready = conn.execute(
                "SELECT COUNT(*) FROM mix_editions WHERE active=1"
            ).fetchone()[0]
            pending = conn.execute(
                "SELECT COUNT(*) FROM mix_editions WHERE retired_at IS NULL AND json_extract(payload,'$.state')='preparing'"
            ).fetchone()[0]
            retrying = conn.execute(
                "SELECT COUNT(*) FROM mix_editions WHERE retired_at IS NULL AND json_extract(payload,'$.state')='preparing' AND json_extract(payload,'$.attempts')>0"
            ).fetchone()[0]
            retained = self.retained(conn, now)
            reclaimable = sum(
                size
                for key, size in conn.execute(
                    "SELECT key,bytes FROM mix_audio WHERE lease_until<? AND created_at<?",
                    (now, now - 3600),
                )
                if key not in retained
            )
            issues, failed = [], set()
            for user, raw, person in conn.execute(
                "SELECT e.user_id,e.payload,u.display_name FROM mix_editions e JOIN users u ON u.id=e.user_id WHERE e.retired_at IS NULL OR e.lease_until>?",
                (now,),
            ):
                payload = json.loads(raw)
                failures = payload.get("failures", {})
                failed.update((user, key) for key in failures)
                message = payload.get("blocked") or next(iter(failures.values()), None)
                if message and len(issues) < 10:
                    issues.append(
                        {"mix": payload["title"], "person": person, "message": message}
                    )
            people = [
                {"id": r[0], "name": r[1], "ready": r[2], "preparing": r[3]}
                for r in conn.execute(
                    "SELECT u.id,u.display_name,COUNT(CASE WHEN e.active=1 THEN 1 END),COUNT(CASE WHEN e.retired_at IS NULL AND json_extract(e.payload,'$.state')='preparing' THEN 1 END) FROM users u LEFT JOIN mix_editions e ON e.user_id=u.id WHERE u.disabled=0 GROUP BY u.id ORDER BY u.display_name COLLATE NOCASE"
                )
            ]
        return {
            **self.settings(),
            "files": count,
            "used_bytes": size,
            "ready": ready,
            "preparing": pending,
            "retrying": retrying,
            "failed": len(failed),
            "reclaimable_bytes": reclaimable,
            "issues": issues,
            "people": people,
        }

    def room(self, size=160_000_000):
        settings = self.settings()
        with self.store.connect() as conn:
            used = conn.execute(
                "SELECT COALESCE(SUM(bytes),0) FROM mix_audio"
            ).fetchone()[0]
        return (
            used + size <= settings["budget_bytes"]
            and shutil.disk_usage(self.directory).free - size
            >= settings["reserve_bytes"]
        )

    def file(self, key):
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT filename FROM mix_audio WHERE key=?", (key,)
            ).fetchone()
        if not row:
            raise FileNotFoundError()
        path = self.directory / row[0]
        if path.is_symlink() or path.parent != self.directory or not path.is_file():
            raise FileNotFoundError()
        return path

    def available(self, track):
        match = self.store.index.match(track).track
        if match:
            try:
                return track_file(self.store, match["id"])
            except (FileNotFoundError, ValueError, OSError):
                pass
        return self.file(song_key(track))

    @contextmanager
    def lease(self, key, seconds=6 * 3600):
        # Acquisition/promotion and cleanup share the lifecycle lock. A durable
        # lease protects playback after the URL response has released the lock.
        with self.store.lifecycle_lock:
            with self.store.connect() as conn:
                conn.execute(
                    "UPDATE mix_audio SET lease_until=MAX(lease_until,?) WHERE key=?",
                    (time.time() + seconds, key),
                )
            yield

    def retained(self, conn, now):
        retained = set()
        for (payload,) in conn.execute(
            "SELECT payload FROM mix_editions WHERE active=1 OR retired_at IS NULL OR retired_at>? OR lease_until>?",
            (now - 3600, now),
        ):
            retained.update(song_key(t) for t in json.loads(payload)["tracks"])
        # Explicit library intent protects audio even while paused or retrying.
        retained.update(
            song_key(json.loads(payload))
            for (payload,) in conn.execute(
                "SELECT payload FROM queue WHERE status NOT IN ('completed','cancelled')"
            )
        )
        return retained

    def cleanup(self, now=None):
        now = time.time() if now is None else now
        with (
            self.store.lifecycle_lock,
            self.store.mix_lock,
            self.store.connect() as conn,
        ):
            retained = self.retained(conn, now)
            for key, filename in conn.execute(
                "SELECT key,filename FROM mix_audio WHERE lease_until<? AND created_at<?",
                (now, now - 3600),
            ).fetchall():
                if key in retained:
                    continue
                path = self.directory / filename
                if path.parent == self.directory:
                    path.unlink(missing_ok=True)
                conn.execute("DELETE FROM mix_audio WHERE key=?", (key,))
            # Edition metadata is retained for delayed listen delivery, not audio.
            conn.execute(
                "DELETE FROM mix_editions WHERE active=0 AND retired_at<?",
                (now - 31 * 86400,),
            )
            known = {r[0] for r in conn.execute("SELECT filename FROM mix_audio")}
            for path in self.directory.iterdir():
                if (
                    path.is_file()
                    and path.name not in known
                    and path.stat().st_mtime < now - 3600
                ):
                    path.unlink(missing_ok=True)
            for path in self.staging.iterdir():
                if path.is_file() and path.stat().st_mtime < now - 86400:
                    path.unlink(missing_ok=True)

    def promote(self, downloader, job, profile):
        """Copy, never move, a suitable cache file into normal guarded publication."""
        key = song_key(job)
        with self.lease(key), self.store.connect() as conn:
            row = conn.execute(
                "SELECT probe FROM mix_audio WHERE key=?", (key,)
            ).fetchone()
            if not row or not satisfies(profile, json.loads(row[0])):
                return False
            try:
                source = self.file(key)
            except FileNotFoundError:
                return False
        # The durable lease keeps cleanup away; copy without lifecycle_lock
        # (LOCK1). The copy is validated again before publication.
        target = downloader.staging / (job["id"] + "-mix" + source.suffix)
        try:
            shutil.copyfile(source, target)
        except FileNotFoundError:
            target.unlink(missing_ok=True)
            return False
        try:
            downloader.complete(job, target, target.suffix, "mix-cache", profile)
            return True
        finally:
            target.unlink(missing_ok=True)


class MixDownloader(Downloader):
    """Use acquisition's search, transfer and validation without its NAS side effects."""

    def __init__(self, cache, runtime, stopping, providers=None):
        self.cache = cache
        self.stop_check = stopping
        super().__init__(cache.store, providers=providers, runtime=runtime)
        self.staging = cache.staging

    def prepare_storage(self):
        pass

    def check(self, job_id):
        if self.stop_check() or not self.store.download_settings()["enabled"]:
            raise Cancelled()
        if self.deadlines.get(job_id, float("inf")) <= time.monotonic():
            raise QualityDeadline()
        if not self.cache.room():
            raise Cancelled()
        with self.store.connect() as conn:
            if conn.execute(
                "SELECT 1 FROM queue WHERE status IN ('queued','waiting_for_sources','searching','downloading','validating') LIMIT 1"
            ).fetchone():
                raise Cancelled()

    def state(self, request_id, status, **details):
        self.result = status
        if details.get("error"):
            self.error = details["error"]

    def hold(self, job, source, candidate):
        pass  # Mix preparation never asks the owner; it skips the upload.

    def progress(self, job_id, received, total):
        self.check(job_id)

    def complete(self, job, path, extension, source, profile, provenance=None):
        self.check(job["id"])
        probe = self.validate(path, job, profile, check=lambda: self.check(job["id"]))
        key = song_key(job)
        filename = key + extension
        with self.store.lifecycle_lock:
            self.check(job["id"])
            path.replace(self.cache.directory / filename)
            with self.store.connect() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO mix_audio VALUES (?,?,?,?,?,?)",
                    (
                        key,
                        filename,
                        json.dumps(probe),
                        (self.cache.directory / filename).stat().st_size,
                        time.time() + 3600,
                        time.time(),
                    ),
                )
        self.result = "ready"

    def fetch(self, track):
        # The actual worker thread owns this lock, even if an HTTP caller goes
        # away. Automatic preparation and saved-edition playback share staging.
        if not self.cache.download_lock.acquire(timeout=1):
            self.result, self.error = "deferred", "Another mix download is in progress"
            return False
        try:
            try:
                self.cache.available(track)
                self.result, self.error = "ready", None
                return True
            except FileNotFoundError:
                pass
            return self._fetch(track)
        finally:
            self.cache.download_lock.release()

    def _fetch(self, track):
        self.result = "preparing"
        self.error = None
        profile = self.cache.settings()["profile"]
        job = {
            "album_artist": track["artist"],
            "year": "",
            "disc_number": 1,
            "track_number": 1,
            **track,
            "id": song_key(track),
            "quality_policy": {
                "preferred": profile,
                "fallback": "mp3_320",
                "fallback_minutes": 1,
                "aac_fallback": True,
            },
        }
        self.process(job)
        return self.result == "ready"
