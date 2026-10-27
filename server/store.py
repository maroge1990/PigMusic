from .lifecycle import TimedRLock, coordinated
import json
import hashlib
import os
import time
import threading
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

from .migrations import migrate
from .cover_store import CoverStore

from .library import (
    build_library,
    build_migration,
    identifier,
    load_json,
    normalise,
    parse_apple,
)
from .media import settings as media_settings, public_settings
from .quality import policy, actual_profile, rank, PROFILES, satisfies
from .providers import REGISTRY, supports
from .storage import file_sha256, library_root
from .matching import (
    LibraryIndex,
    compatible,
    exclusion_key,
    request_intent,
    same_song,
    song_key,
)

# A held upload is decided only while its request has stopped (SC1).
REVIEWABLE = {"failed", "waiting_for_sources"}


class Store:
    """Durable application state and revisioned projection of the music library."""

    def __init__(self, directory: Path):
        self.directory = directory
        # Holds over a second are logged with their holder (LOCK1).
        self.lifecycle_lock = TimedRLock("lifecycle_lock")
        # Mix playback leases against mix cache cleanup only. Playback must not
        # wait for library work (syncs, scans, exports) under lifecycle_lock;
        # cleanup takes this after lifecycle_lock, never the other way round.
        self.mix_lock = threading.RLock()
        self.library_lock = TimedRLock("library_lock")
        directory.mkdir(parents=True, exist_ok=True)
        self.cover_dir = directory / "covers"
        self.cover_dir.mkdir(exist_ok=True)
        self.covers = CoverStore(self.cover_dir)
        self.covers.migrate(self.directory / "library-snapshot.json")
        self.database = directory / "pig-music.sqlite3"
        with self.connect() as conn:
            migrate(conn)
        self.refresh_library()
        self.source = self.read_document("migration")
        from .user_library import UserLibrary, initialise_owner

        self.user_libraries = UserLibrary(self)
        initialise_owner(self)

    def refresh_library(self):
        with self.covers.lock, self.library_lock:
            self._refresh_library()

    def _refresh_library(self):
        snapshot = load_json(self.directory / "library-snapshot.json")
        with self.connect() as conn:
            extra = [
                json.loads(r[0]) for r in conn.execute("SELECT payload FROM acquired")
            ]
            categories = dict(conn.execute("SELECT id,category FROM album_categories"))
        replaced = {t["id"] for t in extra}
        exported = {
            t["export_relative_path"] for t in extra if t.get("export_relative_path")
        }
        with self.connect() as conn:
            removed = {
                json.loads(v)["track_id"]
                for (v,) in conn.execute(
                    "SELECT payload FROM documents WHERE name LIKE 'removed:%'"
                )
                if json.loads(v).get("status") in {"removing", "removed"}
            }
        extra = [t for t in extra if t["id"] not in removed]
        anchors = self.read_document("album-edition-anchors")
        self.edition_anchors = anchors
        self.library = build_library(
            {
                **snapshot,
                "tracks": [
                    *[
                        t
                        for t in snapshot.get("tracks", [])
                        if t["id"] not in removed
                        and t["id"] not in replaced
                        and t.get("relative_path") not in exported
                    ],
                    *extra,
                ],
            },
            categories,
            anchors,
        )
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO documents VALUES (?,?) ON CONFLICT(name) DO UPDATE SET payload=excluded.payload",
                ("album-edition-anchors", json.dumps(anchors)),
            )
        self._cache_covers(snapshot.get("tracks", []))
        self.albums_by_id = {a["id"]: a for a in self.library["albums"]}
        self.artists_by_id = {a["id"]: a for a in self.library["artists"]}
        self.index = LibraryIndex(self.library)
        self.snapshot_tracks = {t["id"]: t for t in snapshot.get("tracks", [])}
        fingerprint = hashlib.sha256(
            json.dumps(self.library, sort_keys=True).encode()
        ).hexdigest()
        # A content revision survives restart: a reset counter could leave an
        # already-open browser displaying stale content after a new scan/start.
        self.library_revision = fingerprint
        from .household_policy import HouseholdPolicy

        with self.connect() as conn:
            HouseholdPolicy(self).refresh_inbox(conn)
        self.library_payload = None

    def categorise(self, album_id, category):
        if category not in {"auto", "album", "compilation", "soundtrack", "musical"}:
            raise ValueError("Choose a valid library category")
        if album_id not in self.albums_by_id:
            raise ValueError("Album not found")
        with self.connect() as conn:
            if category == "auto":
                conn.execute("DELETE FROM album_categories WHERE id=?", (album_id,))
            else:
                conn.execute(
                    "INSERT INTO album_categories VALUES (?,?) ON CONFLICT(id) DO UPDATE SET category=excluded.category",
                    (album_id, category),
                )
        self.refresh_library()
        return self.summary()

    def download_settings(self):
        return {
            "enabled": False,
            "profile": "mp3_320",
            "sources": ["freemp3cloud", "zvu4no"],
            "concurrent_downloads": 3,
            "fallback_profile": "mp3_320",
            "fallback_minutes": 10,
            "recheck_hours": 24,
            **self.read_document("downloads"),
        }

    def save_download_settings(self, settings):
        previous = self.download_settings()
        settings = {**self.download_settings(), **settings}
        if settings.get("fallback_profile") not in {
            "none",
            "mp3_320",
            "mp3_128",
            "native_standard",
        }:
            raise ValueError("Choose a valid fallback quality")
        for key, minimum, maximum in (
            ("concurrent_downloads", 1, 6),
            ("fallback_minutes", 1, 60),
            ("recheck_hours", 1, 168),
        ):
            if (
                type(settings.get(key)) is not int
                or not minimum <= settings[key] <= maximum
            ):
                raise ValueError(f"Choose {key} between {minimum} and {maximum}")
        if settings.get("profile") not in PROFILES or not isinstance(
            settings.get("enabled"), bool
        ):
            raise ValueError("Choose a valid quality profile and enabled state")
        if not isinstance(settings.get("sources"), list) or not set(
            settings["sources"]
        ).issubset(REGISTRY):
            raise ValueError("Choose supported download sources")
        settings["sources"] = list(dict.fromkeys(settings["sources"]))
        if settings["enabled"] and not settings["sources"]:
            raise ValueError("Enable at least one source")
        soulseek = (
            "soulseek" in settings["sources"]
            and (self.directory / "slskd-connection.json").exists()
        )
        if settings["enabled"] and "soulseek" in settings["sources"] and not soulseek:
            raise ValueError(
                "Configure the Soulseek engine connection before enabling this source"
            )
        if settings["enabled"] and not any(
            supports(source, settings["profile"]) for source in settings["sources"]
        ):
            message = {
                "lossless": "A lossless-capable source must be connected before enabling this profile",
                "aac_256": "Enable an AAC-capable source for this profile",
            }.get(settings["profile"], "Enable an MP3 source for the selected profile")
            raise ValueError(message)
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO documents VALUES ('downloads',?) ON CONFLICT(name) DO UPDATE SET payload=excluded.payload",
                (json.dumps(settings),),
            )
        if previous["profile"] != settings["profile"]:
            with self.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                for row in conn.execute("SELECT payload FROM acquired").fetchall():
                    track = json.loads(row[0])
                    desired = self.quality_policy(track, conn=conn)
                    self._enqueue_quality(conn, track, desired)
                    self._save_quality_policy(
                        conn, {**track, "quality_policy": desired}
                    )
        return self.download_settings()

    def request_active(self, request_id, claim_token=None):
        with self.connect() as conn:
            row = conn.execute(
                "SELECT status,payload FROM queue WHERE id=?", (request_id,)
            ).fetchone()
        return bool(
            row
            and (
                claim_token is None
                or json.loads(row[1]).get("claim_token") == claim_token
            )
            and row[0] in {"searching", "downloading", "validating", "importing"}
        )

    def claim_request(self):
        if not self.download_settings()["enabled"]:
            return None
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT id,payload FROM queue WHERE status='waiting_for_sources' "
                "AND COALESCE(json_extract(payload,'$.retry_after'),0)<=? "
                "ORDER BY created_at,id LIMIT 1",
                (time.time(),),
            ).fetchone()
            if row:
                payload = {**json.loads(row[1]), "claim_token": uuid.uuid4().hex}
                for key in (
                    "error",
                    "source",
                    "received_bytes",
                    "total_bytes",
                    "source_review",  # Held again if the upload is still found.
                ):
                    payload.pop(key, None)
                conn.execute(
                    "UPDATE queue SET status='searching',payload=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (json.dumps(payload), row[0]),
                )
                return {"id": row[0], **payload}

    def request_state(self, request_id, status, _claim_token=None, **details):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT payload,status FROM queue WHERE id=?", (request_id,)
            ).fetchone()
            if not row or row[1] not in {
                "searching",
                "downloading",
                "validating",
                "importing",
            }:
                return
            if (
                _claim_token is not None
                and json.loads(row[0]).get("claim_token") != _claim_token
            ):
                return
            payload = {**json.loads(row[0]), **details}
            conn.execute(
                "UPDATE queue SET status=?,payload=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (status, json.dumps(payload), request_id),
            )

    def progress(self, request_id, received, total, claim_token=None):
        # Avoid a database write for every network chunk.
        if (
            received // 524288 != max(0, received - 65536) // 524288
            or received == total
        ):
            self.request_state(
                request_id,
                "downloading",
                received_bytes=received,
                total_bytes=total,
                _claim_token=claim_token,
            )

    @coordinated
    def publish_request(self, job, source, target, probe, sha):
        target = target.resolve()
        root = library_root(self).resolve()
        if not target.resolve().is_relative_to(root):
            raise ValueError("Import target is outside the isolated library")
        metadata = {
            k: job[k]
            for k in (
                "title",
                "artist",
                "album",
                "album_artist",
                "year",
                "disc_number",
                "track_number",
            )
        }
        metadata.update(
            {
                k: job[k]
                for k in ("musicbrainz_release_id", "musicbrainz_recording_id", "isrcs")
                if k in job
            }
        )
        metadata.update(
            id=job.get("library_track_id", job["id"]),
            codec=probe["codec"],
            bitrate=probe["bitrate_kbps"],
            duration=probe["duration"],
            sha256=sha,
            relative_path=target.relative_to(root).as_posix(),
        )
        if probe.get("acquisition"):
            metadata["acquisition"] = probe["acquisition"]
        if self.covers.path(job.get("cover_sha")):
            metadata["cover_sha"] = job["cover_sha"]
        if job.get("explicit_edition"):
            metadata["explicit_edition"] = True
        if job.get("recording_kind"):
            metadata["recording_kind"] = job["recording_kind"]
        metadata["_request_id"] = job["id"]
        metadata["_claim_token"] = job.get("claim_token")
        metadata["quality_policy"] = job.get(
            "quality_policy",
            policy(self.download_settings(), job.get("quality_profile")),
        )
        legacy = None
        if job.get("original_relative_path") and job.get("library_track_id"):
            from .exporter import contained, digest

            nas = Path(os.environ.get("PIG_MUSIC_LIBRARY", "/library"))
            original = contained(nas, job["original_relative_path"])
            if original.is_file():
                stat = original.stat()
                if stat.st_size == job.get(
                    "original_size_bytes"
                ) and stat.st_mtime_ns == job.get("original_mtime_ns"):
                    legacy = {
                        "id": metadata["id"],
                        "nas_relative_path": job["original_relative_path"],
                        "sha256": digest(original),
                        "_replacement_requested": True,
                    }
        journal = (
            self.directory
            / "downloads"
            / "staging"
            / (job["id"] + "-" + job.get("claim_token", "legacy") + ".json")
        )
        journal.write_text(
            json.dumps(
                {**metadata, **({"_legacy_replacement": legacy} if legacy else {})}
            ),
            encoding="utf-8",
        )
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status,payload FROM queue WHERE id=?", (job["id"],)
            ).fetchone()
            if (
                not row
                or row[0] != "validating"
                or (
                    job.get("claim_token")
                    and json.loads(row[1]).get("claim_token") != job["claim_token"]
                )
            ):
                return
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if file_sha256(target) != sha:
                    raise ValueError(
                        "Import collision: an existing file has different content"
                    )
            else:
                os.replace(source, target)
            old = conn.execute(
                "SELECT payload FROM acquired WHERE id=?", (metadata["id"],)
            ).fetchone()
            if old:
                conn.execute(
                    "INSERT OR IGNORE INTO quality_versions VALUES (?,?,?)",
                    (job["id"], metadata["id"], old[0]),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO quality_cleanup VALUES (?,?,?)",
                    (job["id"], metadata["id"], old[0]),
                )
            elif legacy:
                conn.execute(
                    "INSERT OR IGNORE INTO quality_versions VALUES (?,?,?)",
                    (job["id"], metadata["id"], json.dumps(legacy)),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO quality_cleanup VALUES (?,?,?)",
                    (job["id"], metadata["id"], json.dumps(legacy)),
                )
            conn.execute(
                "INSERT INTO acquired VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                (metadata["id"], json.dumps(metadata)),
            )
            self._grant_request_membership(conn, row[1], metadata["id"])
            self._save_quality_policy(conn, metadata)
            conn.execute(
                "UPDATE queue SET status='completed',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (job["id"],),
            )
        journal.unlink(missing_ok=True)
        self.refresh_library()
        self.cleanup_replacements()

    def defer_request(self, job, seconds, attempts):
        """Put a claimed request back in the queue, not to be claimed again
        for `seconds` (the catalogue it needs is unavailable for now)."""
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status,payload FROM queue WHERE id=?", (job["id"],)
            ).fetchone()
            if (
                not row
                or row[0] != "searching"
                or json.loads(row[1]).get("claim_token") != job.get("claim_token")
            ):
                return False
            payload = {
                **json.loads(row[1]),
                "retry_after": time.time() + seconds,
                "resolution_attempts": attempts,
            }
            conn.execute(
                "UPDATE queue SET status='waiting_for_sources',payload=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (json.dumps(payload), job["id"]),
            )
        return True

    def fulfil_request(self, job, track_id):
        """Complete a claimed request with a song the household already has
        (another edition): its requesters gain that song, nothing downloads."""
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status,payload FROM queue WHERE id=?", (job["id"],)
            ).fetchone()
            if (
                not row
                or row[0] != "searching"
                or json.loads(row[1]).get("claim_token") != job.get("claim_token")
            ):
                return False
            self._grant_request_membership(conn, row[1], track_id)
            conn.execute(
                "UPDATE queue SET status='completed',payload=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (
                    json.dumps({**json.loads(row[1]), "fulfilled_by": track_id}),
                    job["id"],
                ),
            )
        return True

    def _grant_request_membership(self, conn, payload, track_id):
        from .user_library import bump

        for beneficiary in json.loads(payload).get("requested_by", []):
            if conn.execute(
                "SELECT 1 FROM users WHERE id=? AND disabled=0", (beneficiary,)
            ).fetchone():
                added = conn.execute(
                    "INSERT OR IGNORE INTO library_members VALUES (?,?,?, 'request')",
                    (beneficiary, track_id, int(time.time())),
                ).rowcount
                if added:
                    bump(conn, beneficiary)

    @coordinated
    def recover_requests(self):
        root = library_root(self).resolve()
        stage = self.directory / "downloads" / "staging"
        if stage.exists():
            for journal in stage.glob("*.json"):
                try:
                    metadata = json.loads(journal.read_text(encoding="utf-8"))
                    legacy = metadata.pop("_legacy_replacement", None)
                    target = (root / metadata["relative_path"]).resolve()
                    if (
                        not target.is_relative_to(root)
                        or not target.is_file()
                        or file_sha256(target) != metadata["sha256"]
                    ):
                        continue
                    with self.connect() as conn:
                        conn.execute("BEGIN IMMEDIATE")
                        request_id = metadata.get("_request_id", metadata["id"])
                        row = conn.execute(
                            "SELECT status,payload FROM queue WHERE id=?", (request_id,)
                        ).fetchone()
                        if (
                            row
                            and row[0] not in {"cancelled", "failed"}
                            and (
                                not metadata.get("_claim_token")
                                or json.loads(row[1]).get("claim_token")
                                == metadata["_claim_token"]
                            )
                        ):
                            old = conn.execute(
                                "SELECT payload FROM acquired WHERE id=?",
                                (metadata["id"],),
                            ).fetchone()
                            if (
                                old
                                and json.loads(old[0]).get("sha256")
                                != metadata["sha256"]
                            ):
                                conn.execute(
                                    "INSERT OR IGNORE INTO quality_versions VALUES (?,?,?)",
                                    (request_id, metadata["id"], old[0]),
                                )
                                conn.execute(
                                    "INSERT OR IGNORE INTO quality_cleanup VALUES (?,?,?)",
                                    (request_id, metadata["id"], old[0]),
                                )
                            elif not old and legacy:
                                conn.execute(
                                    "INSERT OR IGNORE INTO quality_versions VALUES (?,?,?)",
                                    (request_id, metadata["id"], json.dumps(legacy)),
                                )
                                conn.execute(
                                    "INSERT OR IGNORE INTO quality_cleanup VALUES (?,?,?)",
                                    (request_id, metadata["id"], json.dumps(legacy)),
                                )
                            conn.execute(
                                "INSERT INTO acquired VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                                (metadata["id"], json.dumps(metadata)),
                            )
                            self._grant_request_membership(conn, row[1], metadata["id"])
                            self._save_quality_policy(conn, metadata)
                            conn.execute(
                                "UPDATE queue SET status='completed' WHERE id=?",
                                (request_id,),
                            )
                    journal.unlink(missing_ok=True)
                except (ValueError, KeyError, OSError):
                    continue
        with self.connect() as conn:
            conn.execute(
                "UPDATE queue SET status='waiting_for_sources' WHERE status IN ('searching','downloading','validating','importing')"
            )
        self.refresh_library()
        self.cleanup_replacements()

    @coordinated
    def cleanup_replacements(self):
        root = library_root(self).resolve()
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT request_id,track_id,payload FROM quality_cleanup"
            ).fetchall()
        for request_id, track_id, raw in rows:
            try:
                old = json.loads(raw)
                with self.connect() as conn:
                    row = conn.execute(
                        "SELECT payload FROM acquired WHERE id=?", (track_id,)
                    ).fetchone()
                if not row:
                    continue
                current = json.loads(row[0])
                if old.get("export_relative_path") or old.get("nas_relative_path"):
                    if not current.get("export_relative_path"):
                        continue  # Keep the old NAS file until its replacement is published.
                    from .exporter import contained, digest

                    nas = Path(
                        os.environ.get("PIG_MUSIC_LIBRARY", "/library")
                    ).resolve()
                    old_path = (
                        old.get("export_relative_path") or old["nas_relative_path"]
                    )
                    if not old.get("_replacement_requested") and not old.get(
                        "export_relative_path"
                    ):
                        continue
                    original = contained(nas, old_path)
                    replacement = contained(nas, current["export_relative_path"])
                    if (
                        original == replacement
                        or not replacement.is_file()
                        or digest(replacement) != current["sha256"]
                    ):
                        continue
                    trash = contained(
                        nas, ".pig-music-trash/" + request_id + "/" + old_path
                    )
                    if original.exists():
                        if digest(original) != old["sha256"]:
                            continue
                        trash.parent.mkdir(parents=True, exist_ok=True)
                        ignore = contained(nas, ".pig-music-trash/.plexignore")
                        try:
                            with ignore.open("x", encoding="utf-8") as stream:
                                stream.write("*\n")
                        except FileExistsError:
                            if ignore.read_text(encoding="utf-8").strip() != "*":
                                continue
                        try:
                            os.link(original, trash)
                        except FileExistsError:
                            if digest(trash) != old["sha256"]:
                                continue
                        if (
                            digest(original) != old["sha256"]
                            or digest(trash) != old["sha256"]
                        ):
                            continue
                        original.unlink()
                    elif not trash.is_file() or digest(trash) != old["sha256"]:
                        continue
                    with self.connect() as conn:
                        conn.execute(
                            "DELETE FROM quality_cleanup WHERE request_id=?",
                            (request_id,),
                        )
                    continue
                original = (root / old["relative_path"]).resolve()
                replacement = (root / current["relative_path"]).resolve()
                if (
                    not original.is_relative_to(root)
                    or not replacement.is_relative_to(root)
                    or original == replacement
                ):
                    continue
                if not replacement.is_file():
                    continue
                with replacement.open("rb") as stream:
                    if (
                        hashlib.file_digest(stream, "sha256").hexdigest()
                        != current["sha256"]
                    ):
                        continue
                if original.exists():
                    with original.open("rb") as stream:
                        if (
                            hashlib.file_digest(stream, "sha256").hexdigest()
                            != old["sha256"]
                        ):
                            continue
                    original.unlink()
                with self.connect() as conn:
                    conn.execute(
                        "DELETE FROM quality_cleanup WHERE request_id=?", (request_id,)
                    )
            except (OSError, KeyError, ValueError):
                continue  # Retry at startup or the next scheduler scan.

    def retry_request(self, request_id):
        with self.connect() as conn:
            result = conn.execute(
                "UPDATE queue SET status='waiting_for_sources',updated_at=CURRENT_TIMESTAMP WHERE id=? AND status IN ('failed','cancelled')",
                (request_id,),
            )
            if not result.rowcount:
                raise ValueError("Only failed or cancelled requests can be retried")
        return self.queue()

    def review_source(self, request_id, approve):
        """Approve or reject the upload a request is holding for review (SC1).

        Only a request that has stopped (failed or waiting) can be decided,
        so a running search never races the owner. Approving requeues a
        failed request with that upload allowed; rejecting rules it out."""
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status,payload FROM queue WHERE id=?", (request_id,)
            ).fetchone()
            if not row:
                raise LookupError("Request not found")
            status, payload = row[0], json.loads(row[1])
            review = payload.get("source_review")
            if not review or status not in REVIEWABLE:
                raise ValueError("This request has no upload waiting for review")
            if approve:
                payload["source_review_approved"] = review["provider_track_id"]
                # Approval is for this uploader; another one found at
                # transfer goes back to review.
                payload["source_review_approved_uploader"] = review["uploader"]
                payload.pop("retry_after", None)
                status = "waiting_for_sources"
            else:
                payload["source_review_rejected"] = [
                    *payload.get("source_review_rejected", []),
                    review["provider_track_id"],
                ]
            payload["source_review"] = None
            conn.execute(
                "UPDATE queue SET status=?,payload=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (status, json.dumps(payload), request_id),
            )
        return self.queue()

    def album_key(self, track):
        saved = self.index.album_by_track.get(track.get("id"))
        if saved and same_song(self.index.by_id[track["id"]], track):
            return saved
        legacy = identifier(
            "album",
            normalise(track.get("album_artist") or track.get("artist", "")),
            normalise(track.get("album", "")),
        )
        anchor = self.edition_anchors.get(legacy)
        release = track.get("musicbrainz_release_id")
        if anchor and release and release != anchor:
            return identifier("album-edition", legacy, release)
        return legacy

    def quality_policy(self, track, preferred=None, conn=None, context=None):
        result = policy(
            context["settings"] if context is not None else self.download_settings(),
            preferred,
        )
        if preferred is None:
            artist_key = identifier(
                "artist",
                normalise(track.get("album_artist") or track.get("artist", "")),
            )
            album_key = self.album_key(track)
            for name in (
                "quality-override:artist:" + artist_key,
                "quality-override:album:" + album_key,
            ):
                value = (
                    context["overrides"].get(name)
                    if context is not None
                    else self.read_document(name).get("profile")
                )
                if value:
                    result["preferred"] = value
        from .household_policy import HouseholdPolicy

        if conn is None:
            with self.connect() as connection:
                result["preferred"] = HouseholdPolicy(self).effective(
                    connection, track, result["preferred"]
                )
        else:
            result["preferred"] = HouseholdPolicy(self).effective(
                conn, track, result["preferred"], context
            )
        return result

    def _save_quality_policy(self, conn, metadata):
        value = {
            **metadata.get("quality_policy", policy(self.download_settings())),
            "next_check": time.time()
            + metadata.get("quality_policy", {}).get("recheck_hours", 24) * 3600,
        }
        conn.execute(
            "INSERT INTO documents VALUES (?,?) ON CONFLICT(name) DO UPDATE SET payload=excluded.payload",
            ("quality-policy:" + metadata["id"], json.dumps(value)),
        )

    def _enqueue_quality(self, conn, track, desired, automatic=False, active=None):
        active = (
            active
            if active is not None
            else conn.execute(
                "SELECT id,payload FROM queue WHERE status IN ('waiting_for_sources','searching','downloading','validating','importing')"
            ).fetchall()
        )
        for request_id, raw in active:
            payload = json.loads(raw)
            if payload.get("library_track_id") == track["id"]:
                if automatic or payload["quality_profile"] == desired["preferred"]:
                    return False
                conn.execute(
                    "UPDATE queue SET status='cancelled',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (request_id,),
                )
        preferred = desired["preferred"]
        current = actual_profile(track)
        if (
            current == preferred
            or preferred == "native_standard"
            and satisfies(preferred, track)
            or automatic
            and (current == "unknown" or rank(current) >= rank(preferred))
        ):
            return False
        payload = {
            k: track[k]
            for k in (
                "title",
                "artist",
                "album",
                "album_artist",
                "year",
                "disc_number",
                "track_number",
                "duration",
            )
        }
        payload.update(
            {
                k: track[k]
                for k in ("musicbrainz_release_id", "musicbrainz_recording_id", "isrcs")
                if track.get(k)
            }
        )
        payload.update(
            library_track_id=track["id"],
            quality_profile=preferred,
            quality_policy=desired,
            upgrade_only=automatic or rank(preferred) > rank(current),
            exact_quality=True,
            requested_by=[
                r[0]
                for r in conn.execute(
                    "SELECT user_id FROM library_members WHERE track_id=?",
                    (track["id"],),
                )
            ],
        )
        managed = conn.execute(
            "SELECT 1 FROM acquired WHERE id=?", (track["id"],)
        ).fetchone()
        if (
            not managed
            and track.get("relative_path")
            and track.get("size_bytes") is not None
            and track.get("mtime_ns") is not None
        ):
            payload.update(
                original_relative_path=track["relative_path"],
                original_size_bytes=track["size_bytes"],
                original_mtime_ns=track["mtime_ns"],
            )
        key = identifier("quality", track["id"], preferred, time.time_ns())
        conn.execute(
            "INSERT INTO queue(id,payload,status) VALUES (?,?,'waiting_for_sources')",
            (key, json.dumps(payload)),
        )
        return True

    @coordinated
    def quality_override(self, kind, item_id, preferred):
        if preferred is not None and preferred not in PROFILES:
            raise ValueError("Choose a supported quality")
        item = (self.albums_by_id if kind == "album" else self.artists_by_id).get(
            item_id
        )
        if item is None:
            raise ValueError("Library item not found")
        albums = (
            [item]
            if kind == "album"
            else [a for a in self.library["albums"] if a["artist_id"] == item_id]
        )
        name = "quality-override:" + kind + ":" + item_id
        with self.connect() as conn:
            if preferred is None:
                conn.execute("DELETE FROM documents WHERE name=?", (name,))
            else:
                conn.execute(
                    "INSERT INTO documents VALUES (?,?) ON CONFLICT(name) DO UPDATE SET payload=excluded.payload",
                    (name, json.dumps({"profile": preferred})),
                )
        created = 0
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for album in albums:
                for track in album["tracks"]:
                    desired = self.quality_policy(track, conn=conn)
                    created += self._enqueue_quality(conn, track, desired)
                    conn.execute(
                        "INSERT INTO documents VALUES (?,?) ON CONFLICT(name) DO UPDATE SET payload=excluded.payload",
                        (
                            "quality-policy:" + track["id"],
                            json.dumps(
                                {
                                    **desired,
                                    "next_check": time.time()
                                    + desired["recheck_hours"] * 3600,
                                }
                            ),
                        ),
                    )
        return {
            "created": created,
            "preferred": preferred,
            "originals_retained_on_failure": True,
            "downloads_enabled": self.download_settings()["enabled"],
        }

    def quality_status(self, kind, item_id):
        item = (self.albums_by_id if kind == "album" else self.artists_by_id).get(
            item_id
        )
        if item is None:
            raise ValueError("Library item not found")
        track = (
            {**item["tracks"][0], "album_id": item_id}
            if kind == "album"
            else {"artist": item["name"], "album_artist": item["name"]}
        )
        return {
            "profile": self.read_document(
                "quality-override:" + kind + ":" + item_id
            ).get("profile"),
            "effective_profile": self.quality_policy(track)["preferred"],
        }

    @coordinated
    def schedule_upgrades(self):
        if not self.download_settings()["enabled"]:
            return
        self.cleanup_replacements()
        tracks = {t["id"]: t for a in self.library["albums"] for t in a["tracks"]}
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT name,payload FROM documents WHERE name LIKE 'quality-policy:%'"
            ).fetchall()
            for name, raw in rows:
                desired = json.loads(raw)
                if desired.get("next_check", 0) > time.time():
                    continue
                track = tracks.get(name.split(":", 1)[1])
                if track:
                    desired = {**desired, **self.quality_policy(track)}
                    self._enqueue_quality(conn, track, desired, automatic=True)
                desired["next_check"] = time.time() + desired["recheck_hours"] * 3600
                conn.execute(
                    "UPDATE documents SET payload=? WHERE name=?",
                    (json.dumps(desired), name),
                )

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.database, timeout=10)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            with conn:
                yield conn
        finally:
            conn.close()

    def read_document(self, name):
        with self.connect() as conn:
            row = conn.execute(
                "SELECT payload FROM documents WHERE name=?", (name,)
            ).fetchone()
        return json.loads(row[0]) if row else {}

    def read_documents(self, names):
        """Several documents in one query: {name: payload} for those present."""
        names = list(dict.fromkeys(names))
        if not names:
            return {}
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT documents.name,documents.payload FROM documents "
                "JOIN json_each(?) wanted ON documents.name=wanted.value",
                (json.dumps(names),),
            ).fetchall()
        return {name: json.loads(payload) for name, payload in rows}

    def _cache_covers(self, snapshot_tracks):
        from .library_artwork import cache_covers

        return cache_covers(self, snapshot_tracks)

    def prune_artwork(self, now=None):
        from .library_artwork import prune_artwork

        return prune_artwork(self, now)

    def write_document(self, key, value):
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO documents VALUES (?,?) ON CONFLICT(name) DO UPDATE SET payload=excluded.payload",
                (key, json.dumps(value)),
            )

    def library_for(self, user_id):
        return self.user_libraries.summary(user_id)

    def summary(self):
        with self.connect() as conn:
            playlist_count = conn.execute("SELECT COUNT(*) FROM playlists").fetchone()[
                0
            ]
        return {
            **self.library,
            "revision": self.library_revision,
            "media": public_settings(self),
            "playlist_count": playlist_count,
            "albums": [
                {k: v for k, v in a.items() if k not in {"tracks", "cover_key"}}
                for a in self.library["albums"]
            ],
        }

    def selections(self):
        with self.connect() as conn:
            return {r[0] for r in conn.execute("SELECT track_id FROM selections")}

    def migration(self):
        result = build_migration(
            self.source, self.library, self.selections(), self.index
        )
        with self.connect() as conn:
            decisions = dict(
                conn.execute("SELECT track_id, decision FROM migration_decisions")
            )
        for album in result["albums"]:
            for track in album["tracks"]:
                track["decision"] = decisions.get(track["id"])
        albums = {a["id"]: a for a in self.library["albums"]}
        for album in result["albums"]:
            album["cover_url"] = albums.get(album["library_album_id"], {}).get(
                "cover_url"
            )
        return result

    def import_apple(self, raw: bytes):
        source = parse_apple(raw)
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO documents(name,payload) VALUES('migration',?) "
                "ON CONFLICT(name) DO UPDATE SET payload=excluded.payload",
                (json.dumps(source),),
            )
            conn.execute("DELETE FROM selections")
            conn.execute("DELETE FROM migration_decisions")
            track_by_id = {t["id"]: t for t in source["tracks"]}
            for playlist in source.get("playlists", []):
                payload = {
                    **playlist,
                    "tracks": [
                        track_by_id[track_id] for track_id in playlist["track_ids"]
                    ],
                    "imported_at": source["imported_at"],
                }
                conn.execute(
                    "INSERT INTO playlists(id,payload) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                    (playlist["id"], json.dumps(payload)),
                )
        self.source = source
        return self.migration()

    def decide(self, track_id: str, decision: str):
        if decision not in {"requested", "skip"}:
            raise ValueError("Choose the requested release or skip this song.")
        tracks = [t for a in self.migration()["albums"] for t in a["tracks"]]
        if not any(t["id"] == track_id and t["status"] == "review" for t in tracks):
            raise ValueError("This track does not need a version decision.")
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO migration_decisions VALUES (?,?) ON CONFLICT(track_id) DO UPDATE SET decision=excluded.decision",
                (track_id, decision),
            )
        return self.migration()

    def submit_migration(self):
        # Read persisted inventory, selection and decisions in one transaction so
        # concurrent submissions/import replacement cannot produce partial batches.
        settings = self.download_settings()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT payload FROM documents WHERE name='migration'"
            ).fetchone()
            source = json.loads(row[0]) if row else {}
            selected = {r[0] for r in conn.execute("SELECT track_id FROM selections")}
            decisions = dict(
                conn.execute("SELECT track_id,decision FROM migration_decisions")
            )
            migration = build_migration(source, self.library, selected, self.index)
            tracks = [
                t for a in migration["albums"] for t in a["tracks"] if t["selected"]
            ]
            if not tracks:
                raise ValueError("Select music before submitting an import.")
            conflicts = [
                t
                for t in tracks
                if t["status"] == "review" and t["id"] not in decisions
            ]
            if conflicts:
                raise ValueError(
                    f"Resolve {len(conflicts)} version conflicts before importing."
                )
            created = existing = skipped = 0
            for track in tracks:
                if decisions.get(track["id"]) == "skip":
                    skipped += 1
                    continue
                payload = {
                    k: track[k]
                    for k in (
                        "title",
                        "artist",
                        "album",
                        "album_artist",
                        "year",
                        "disc_number",
                        "track_number",
                        "duration",
                    )
                }
                payload.update(
                    {
                        k: track[k]
                        for k in (
                            "musicbrainz_release_id",
                            "musicbrainz_recording_id",
                            "isrcs",
                        )
                        if track.get(k)
                    }
                )
                payload["quality_profile"] = settings["profile"]
                payload["quality_policy"] = self.quality_policy(payload)
                payload["quality_profile"] = payload["quality_policy"]["preferred"]
                key = identifier(
                    "request",
                    *(
                        normalise(payload[k])
                        for k in ("artist", "title", "album", "album_artist")
                    ),
                    payload["year"],
                    payload["disc_number"],
                    payload["track_number"],
                    payload["duration"],
                )
                prior = conn.execute(
                    "SELECT status FROM queue WHERE id=?", (key,)
                ).fetchone()
                if prior and prior[0] not in {"cancelled", "failed"}:
                    from .household_policy import HouseholdPolicy

                    HouseholdPolicy(self).reconcile_pending(conn)
                    existing += 1
                elif prior:
                    conn.execute(
                        "UPDATE queue SET status='waiting_for_sources', payload=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (json.dumps(payload), key),
                    )
                    created += 1
                else:
                    conn.execute(
                        "INSERT INTO queue(id,payload,status) VALUES (?,?,'waiting_for_sources')",
                        (key, json.dumps(payload)),
                    )
                    created += 1
            return {
                "created": created,
                "existing": existing,
                "skipped": skipped,
                "downloads_enabled": settings["enabled"],
            }

    @coordinated
    def submit_catalogue(self, tracks, preferred=None, user_id=None):
        settings = self.download_settings()
        if preferred and tracks:
            album_id = self.album_key(tracks[0])
            with self.connect() as conn:
                conn.execute(
                    "INSERT INTO documents VALUES (?,?) ON CONFLICT(name) DO UPDATE SET payload=excluded.payload",
                    (
                        "quality-override:album:" + album_id,
                        json.dumps({"profile": preferred}),
                    ),
                )
        created = existing = skipped = 0
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            # Read both legacy and edition-aware exclusions once per batch.
            exclusions = [
                json.loads(r[0])["track"]
                for r in conn.execute(
                    "SELECT payload FROM documents WHERE name LIKE 'excluded:%'"
                )
            ]
            from .user_library import request_scope

            scopes = request_scope(tracks)
            for track, scope in zip(tracks, scopes):
                if any(same_song(old, track) for old in exclusions):
                    skipped += 1
                    continue
                if track["status"] == "downloaded":
                    if user_id:
                        matched = self.index.match(track).track
                        if matched:
                            from .user_library import bump

                            added = conn.execute(
                                "INSERT OR IGNORE INTO library_members VALUES (?,?,?, 'household-add')",
                                (user_id, matched["id"], int(time.time())),
                            ).rowcount
                            if added:
                                bump(conn, user_id)
                                from .household_policy import HouseholdPolicy

                                HouseholdPolicy(self).membership_changed(
                                    conn, [matched["id"]]
                                )
                    skipped += 1
                    continue
                payload = {
                    k: v
                    for k, v in track.items()
                    if k not in {"id", "status", "presence_note", "explicit_edition"}
                }
                if track.get("explicit_edition"):
                    payload["explicit_edition"] = True
                payload["song_key"] = list(song_key(track))
                if user_id:
                    payload["requested_by"] = [user_id]
                    payload["request_scope"] = {user_id: scope}
                payload["quality_profile"] = preferred or settings["profile"]
                payload["quality_policy"] = self.quality_policy(
                    payload, preferred, conn=conn
                )
                payload["quality_profile"] = payload["quality_policy"]["preferred"]
                key = identifier(
                    "request",
                    *(
                        normalise(payload[k])
                        for k in ("artist", "title", "album", "album_artist")
                    ),
                    payload["year"],
                    payload["disc_number"],
                    payload["track_number"],
                    payload["duration"],
                )
                same = None
                if payload.get("explicit_edition"):
                    # Only this edition will do: never one ordinary job's twin.
                    key = identifier(
                        key, "explicit", payload.get("musicbrainz_release_id") or ""
                    )
                else:
                    # Any edition will do: join a queued request for the same
                    # song (an explicit one satisfies it too).
                    same = next(
                        (
                            row[0]
                            for row in conn.execute(
                                "SELECT id,payload FROM queue WHERE status NOT IN "
                                "('cancelled','failed') AND "
                                "json_extract(payload,'$.song_key')=json(?) "
                                "ORDER BY created_at,id",
                                (json.dumps(payload["song_key"]),),
                            )
                            if compatible(request_intent(json.loads(row[1])), payload)
                        ),
                        None,
                    )
                    if same:
                        key = same
                prior = conn.execute(
                    "SELECT status,payload FROM queue WHERE id=?", (key,)
                ).fetchone()
                if (
                    not same
                    and prior
                    and json.loads(prior[1]).get("musicbrainz_release_id")
                    not in {None, payload.get("musicbrainz_release_id")}
                ):
                    key = identifier(key, payload["musicbrainz_release_id"])
                    prior = conn.execute(
                        "SELECT status,payload FROM queue WHERE id=?", (key,)
                    ).fetchone()
                if prior and prior[0] not in {"cancelled", "failed"}:
                    if user_id:
                        previous = json.loads(prior[1])
                        beneficiaries = previous.get("requested_by", [])
                        scopes_by_user = previous.get("request_scope") or {}
                        if (
                            user_id not in beneficiaries
                            or user_id not in scopes_by_user
                        ):
                            previous["requested_by"] = [
                                *beneficiaries,
                                *([] if user_id in beneficiaries else [user_id]),
                            ]
                            previous["request_scope"] = {
                                **scopes_by_user,
                                user_id: scopes_by_user.get(user_id, scope),
                            }
                            conn.execute(
                                "UPDATE queue SET payload=? WHERE id=?",
                                (json.dumps(previous), key),
                            )
                    from .household_policy import HouseholdPolicy

                    HouseholdPolicy(self).reconcile_pending(conn)
                    existing += 1
                elif prior:
                    conn.execute(
                        "UPDATE queue SET status='waiting_for_sources',payload=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (json.dumps(payload), key),
                    )
                    created += 1
                else:
                    conn.execute(
                        "INSERT INTO queue(id,payload,status) VALUES (?,?,'waiting_for_sources')",
                        (key, json.dumps(payload)),
                    )
                    created += 1
        return {
            "created": created,
            "existing": existing,
            "skipped": skipped,
            "downloads_enabled": settings["enabled"],
        }

    def queue(self):
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id,payload,status,created_at,updated_at FROM queue ORDER BY created_at DESC,id"
            ).fetchall()
        return {
            "library_revision": self.library_revision,
            "downloads_enabled": self.download_settings()["enabled"],
            "items": [
                {
                    "id": r[0],
                    **json.loads(r[1]),
                    "status": r[2],
                    "created_at": r[3],
                    "updated_at": r[4],
                }
                for r in rows
            ],
        }

    def clear_queue(self, finished_only=False):
        """Clear finished entries (the one Clear everyone has), or with
        `finished_only=False` cancel and remove everything.

        Removing an active claim invalidates its token; it cannot publish
        into a newly submitted request with the same deterministic ID.
        """
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if finished_only:
                conn.execute(
                    "DELETE FROM queue WHERE status IN ('completed','downloaded','failed','cancelled')"
                )
            else:
                conn.execute("DELETE FROM queue")
        return self.queue()

    def cancel_request(self, request_id):
        with self.connect() as conn:
            result = conn.execute(
                "UPDATE queue SET status='cancelled',updated_at=CURRENT_TIMESTAMP WHERE id=? AND status IN ('waiting_for_sources','searching','downloading','validating')",
                (request_id,),
            )
            if not result.rowcount:
                raise ValueError("Only waiting requests can be cancelled.")
        return self.queue()

    def update_selection(self, ids: list[str], selected: bool):
        migration = self.migration()
        eligible = {
            t["id"]
            for a in migration["albums"]
            for t in a["tracks"]
            if t["status"] != "downloaded"
        }
        if not set(ids).issubset(eligible):
            raise ValueError("Selection contains unknown or already downloaded tracks.")
        with self.connect() as conn:
            if selected:
                conn.executemany(
                    "INSERT OR IGNORE INTO selections(track_id) VALUES(?)",
                    [(i,) for i in ids],
                )
            else:
                conn.executemany(
                    "DELETE FROM selections WHERE track_id=?", [(i,) for i in ids]
                )
        return self.migration()

    def plan(self):
        migration = self.migration()
        tracks = [
            {k: v for k, v in t.items() if k not in {"favourited", "play_count"}}
            for a in migration["albums"]
            for t in a["tracks"]
            if t["selected"]
        ]
        return {
            "schema_version": 1,
            "mode": "review_only",
            "downloads_enabled": False,
            "source_name": migration["source_name"],
            "track_count": len(tracks),
            "needs_version_review": sum(t["status"] == "review" for t in tracks),
            "tracks": tracks,
        }
