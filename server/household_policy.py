"""Shared quality targets and explicit assignment of externally scanned music."""

import json
import time
import uuid
from .lifecycle import coordinated
from .library import identifier, normalise
from .quality import PROFILES, rank, actual_profile, options
from .user_library import bump, track_dto

ACTIVE = {"waiting_for_sources", "searching", "downloading", "validating", "importing"}


class HouseholdPolicy:
    def __init__(self, store):
        self.store = store

    def context(self, conn):
        members = {}
        for user, track in conn.execute("SELECT user_id,track_id FROM library_members"):
            members.setdefault(track, set()).add(user)
        active = {}
        for key, raw in conn.execute(
            "SELECT id,payload FROM queue WHERE status IN ('waiting_for_sources','searching','downloading','validating','importing')"
        ):
            job = json.loads(raw)
            active.setdefault(job.get("library_track_id"), []).append((key, raw))
        return {
            "members": members,
            "active": active,
            "preferences": {
                (u, s, k): p
                for u, s, k, p in conn.execute(
                    "SELECT user_id,scope,item_id,preferred FROM quality_preferences"
                )
            },
            "overrides": {
                key: json.loads(raw).get("profile")
                for key, raw in conn.execute(
                    "SELECT name,payload FROM documents WHERE name >= 'quality-override:' AND name < 'quality-override;'"
                )
            },
            "settings": self.store.download_settings(),
        }

    def effective(self, conn, track, fallback, context=None):
        identity = track.get("library_track_id") or track.get("id")
        users = set(track.get("requested_by", []))
        if context is not None:
            users.update(context["members"].get(identity, ()))
        elif identity:
            users.update(
                r[0]
                for r in conn.execute(
                    "SELECT user_id FROM library_members WHERE track_id=?", (identity,)
                )
            )
        artist = identifier(
            "artist", normalise(track.get("album_artist") or track.get("artist", ""))
        )
        album = self.store.album_key(track)
        targets = []
        for user in users:
            preferred = fallback
            for scope, key in (("system", "*"), ("artist", artist), ("album", album)):
                row = (
                    (context["preferences"].get((user, scope, key)),)
                    if context is not None
                    else conn.execute(
                        "SELECT preferred FROM quality_preferences WHERE user_id=? AND scope=? AND item_id=?",
                        (user, scope, key),
                    ).fetchone()
                )
                if row and row[0]:
                    preferred = row[0]
            targets.append(preferred)
        if not targets:
            return fallback
        highest = max(rank(t) for t in targets)
        choices = {t for t in targets if rank(t) == highest}
        if "mp3_128" in choices:
            choices.discard("native_standard")
        current = actual_profile(track)
        return current if current in choices else max(choices)

    def reconcile_pending(self, conn):
        context = self.context(conn)
        for key, status, raw in conn.execute(
            "SELECT id,status,payload FROM queue"
        ).fetchall():
            if status not in ACTIVE:
                continue
            job = json.loads(raw)
            desired = self.store.quality_policy(job, conn=conn, context=context)
            if job.get("quality_profile") != desired["preferred"]:
                job.update(
                    quality_profile=desired["preferred"],
                    quality_policy=desired,
                    claim_token=uuid.uuid4().hex,
                )
                for field in ("source", "received_bytes", "total_bytes", "error"):
                    job.pop(field, None)
                conn.execute(
                    "UPDATE queue SET payload=?,status='waiting_for_sources',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (json.dumps(job), key),
                )

    def membership_changed(self, conn, track_ids):
        ids = set(track_ids)
        context = self.context(conn)
        for album in self.store.library["albums"]:
            for track in album["tracks"]:
                if track["id"] in ids:
                    desired = self.store.quality_policy(
                        track, conn=conn, context=context
                    )
                    self.store._enqueue_quality(
                        conn,
                        track,
                        desired,
                        automatic=True,
                        active=context["active"].get(track["id"], []),
                    )
        self.reconcile_pending(conn)
        for identity in ids:
            conn.execute("DELETE FROM unassigned_tracks WHERE track_id=?", (identity,))

    @coordinated
    def set_quality(self, user_id, scope, item_id, preferred):
        if (
            scope not in {"system", "artist", "album"}
            or preferred is not None
            and preferred not in PROFILES
        ):
            raise ValueError("Choose a supported quality preference.")
        projection = self.store.user_libraries.projection(user_id)
        if scope == "system":
            item_id = "*"
            tracks = projection["tracks"]
        elif scope == "album":
            album = next((a for a in projection["albums"] if a["id"] == item_id), None)
            if not album:
                raise LookupError("Album not found.")
            tracks = album["tracks"]
        else:
            artist = next(
                (a for a in projection["artists"] if a["id"] == item_id), None
            )
            if not artist:
                raise LookupError("Artist not found.")
            tracks = [
                t
                for a in projection["albums"]
                if a["id"] in artist["album_ids"]
                for t in a["tracks"]
            ]
        created = 0
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if preferred is None:
                conn.execute(
                    "DELETE FROM quality_preferences WHERE user_id=? AND scope=? AND item_id=?",
                    (user_id, scope, item_id),
                )
            else:
                conn.execute(
                    "INSERT INTO quality_preferences VALUES (?,?,?,?) ON CONFLICT(user_id,scope,item_id) DO UPDATE SET preferred=excluded.preferred",
                    (user_id, scope, item_id, preferred),
                )
            context = self.context(conn)
            for track in tracks:
                count = conn.execute(
                    "SELECT count(*) FROM library_members WHERE track_id=?",
                    (track["id"],),
                ).fetchone()[0]
                desired = self.store.quality_policy(track, conn=conn, context=context)
                created += self.store._enqueue_quality(
                    conn,
                    track,
                    desired,
                    automatic=count > 1,
                    active=context["active"].get(track["id"], []),
                )
            self.reconcile_pending(conn)
        return {
            "scope": scope,
            "item_id": item_id,
            "preferred": preferred,
            "created": created,
        }

    def quality(self, user_id):
        with self.store.connect() as conn:
            return {
                "preferences": [
                    {"scope": scope, "item_id": key, "preferred": value}
                    for scope, key, value in conn.execute(
                        "SELECT scope,item_id,preferred FROM quality_preferences WHERE user_id=? ORDER BY scope,item_id",
                        (user_id,),
                    )
                ],
                "options": options(),
                "household_default": self.store.download_settings()["profile"],
            }

    def refresh_inbox(self, conn):
        if not conn.execute(
            "SELECT 1 FROM documents WHERE name='household-membership-initialised'"
        ).fetchone():
            return
        owned = {
            r[0] for r in conn.execute("SELECT DISTINCT track_id FROM library_members")
        }
        managed = {r[0] for r in conn.execute("SELECT id FROM acquired")}
        already_assigned = owned | managed
        unassigned = {
            t["id"]
            for a in self.store.library["albums"]
            for t in a["tracks"]
            if t.get("available", True) and t["id"] not in already_assigned
        }
        for identity in unassigned:
            conn.execute(
                "INSERT OR IGNORE INTO unassigned_tracks VALUES (?,?)",
                (identity, int(time.time())),
            )
        for (identity,) in conn.execute(
            "SELECT track_id FROM unassigned_tracks"
        ).fetchall():
            if identity not in unassigned:
                conn.execute(
                    "DELETE FROM unassigned_tracks WHERE track_id=?", (identity,)
                )

    @coordinated
    def inbox(self, offset=0, limit=100):
        with self.store.connect() as conn:
            self.refresh_inbox(conn)
            ids = {r[0] for r in conn.execute("SELECT track_id FROM unassigned_tracks")}
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("Invalid inbox page bounds.")
        albums = [
            a
            for a in self.store.library["albums"]
            if any(t["id"] in ids for t in a["tracks"])
        ]
        return {
            "albums": [
                {
                    "id": a["id"],
                    "title": a["title"],
                    "artist": a["artist"],
                    "tracks": [track_dto(t) for t in a["tracks"] if t["id"] in ids],
                }
                for a in albums[offset : offset + limit]
            ],
            "total": len(albums),
            "next_offset": offset + limit if offset + limit < len(albums) else None,
        }

    @coordinated
    def assign(self, track_ids, user_ids):
        users = set(user_ids)
        ids = set(track_ids)
        if not users or not ids:
            raise ValueError("Choose music and at least one person.")
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self.refresh_inbox(conn)
            allowed = {
                r[0]
                for r in conn.execute(
                    "SELECT id FROM users WHERE disabled=0 AND password_hash IS NOT NULL"
                )
            }
            pending = {
                r[0] for r in conn.execute("SELECT track_id FROM unassigned_tracks")
            }
            if not users <= allowed or not ids <= pending:
                raise ValueError(
                    "People or inbox music changed. Refresh before assigning."
                )
            for user in users:
                for identity in ids:
                    conn.execute(
                        "INSERT OR IGNORE INTO library_members VALUES (?,?,?,'scan-assign')",
                        (user, identity, int(time.time())),
                    )
                bump(conn, user)
            for identity in ids:
                conn.execute(
                    "DELETE FROM unassigned_tracks WHERE track_id=?", (identity,)
                )
            self.membership_changed(conn, ids)
        return {"assigned": len(ids), "people": len(users)}
