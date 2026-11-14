"""Membership-aware removal with durable intents and caller-scoped undo."""

import hashlib
import json
import time
import uuid
from pathlib import Path

from .queue_states import ACTIVE
from .lifecycle import coordinated
from .matching import same_song
from .media import track_file
from .user_library import bump, initialise_owner


class RemovalChanged(ValueError):
    pass


class PersonalRemoval:
    def __init__(self, store, recovery, clock=time.time):
        self.store, self.recovery, self.clock = store, recovery, clock

    @coordinated
    def preview(self, user_id, track_ids):
        initialise_owner(self.store)
        ids = sorted(set(track_ids))
        if not ids or len(ids) > 500:
            raise ValueError("Choose between 1 and 500 songs.")
        tracks = {t["id"]: t for a in self.store.library["albums"] for t in a["tracks"]}
        with self.store.connect() as conn:
            memberships = [
                list(r)
                for r in conn.execute("SELECT * FROM library_members")
                if r[1] in ids
            ]
            names = dict(conn.execute("SELECT id,display_name FROM users"))
            jobs = [
                (key, status, json.loads(raw))
                for key, status, raw in conn.execute(
                    "SELECT id,status,payload FROM queue"
                )
                if status in ACTIVE
            ]
        owned = {r[1] for r in memberships if r[0] == user_id}
        if set(ids) != owned or not set(ids).issubset(tracks):
            raise LookupError("One or more songs are outside your library.")
        items, signature = [], []
        for identity in ids:
            track = tracks[identity]
            holders = {
                r[0] for r in memberships if r[1] == identity and r[0] != user_id
            }
            pending = []
            for key, status, job in jobs:
                if same_song(job, track):
                    others = set(job.get("requested_by", [])) - {user_id}
                    if others:
                        holders.update(others)
                        pending.append((key, status, sorted(others)))
            path = track_file(self.store, identity)
            stat = path.stat()
            items.append(
                {
                    "track_id": identity,
                    "delete_file": not holders,
                    "kept_for": sorted(names[u] for u in holders if u in names),
                }
            )
            signature.append(
                (
                    identity,
                    sorted(r for r in memberships if r[1] == identity),
                    sorted(pending),
                    str(path),
                    stat.st_size,
                    stat.st_mtime_ns,
                )
            )
        token = hashlib.sha256(
            json.dumps(signature, sort_keys=True).encode()
        ).hexdigest()
        return {
            "confirmation": token,
            "items": items,
            "delete_count": sum(i["delete_file"] for i in items),
            "keep_count": sum(not i["delete_file"] for i in items),
        }

    @coordinated
    def remove(self, user_id, track_ids, confirmation):
        preview = self.preview(user_id, track_ids)
        if confirmation != preview["confirmation"]:
            raise RemovalChanged("Sharing or files changed. Review the removal again.")
        ids = {i["track_id"] for i in preview["items"]}
        deleting = {i["track_id"] for i in preview["items"] if i["delete_file"]}
        prepared = [self.recovery.prepare(identity) for identity in sorted(deleting)]
        # Multiple metadata IDs can refer to the same physical file.
        for record, root in prepared:
            record["shared"] = any(
                t["id"] not in deleting
                and track_file(self.store, t["id"]) == root / record["original"]
                for a in self.store.library["albums"]
                for t in a["tracks"]
            )
        physical_paths = [str(root / record["original"]) for record, root in prepared]
        if len(set(physical_paths)) != len(physical_paths) or any(
            record["shared"] for record, _ in prepared
        ):
            raise ValueError(
                "Multiple song identities share a file. Ask the administrator to review them before removal."
            )
        operation = {
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "created_at": self.clock(),
            "status": "removing",
            "track_ids": sorted(ids),
            "recovery_ids": [r["id"] for r, _ in prepared],
        }
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            operation["memberships"] = [
                list(r)
                for r in conn.execute(
                    "SELECT * FROM library_members WHERE user_id=?", (user_id,)
                )
                if r[1] in ids
            ]
            for identity in ids:
                conn.execute(
                    "DELETE FROM library_members WHERE user_id=? AND track_id=?",
                    (user_id, identity),
                )
            bump(conn, user_id)
            selected = {
                t["id"]: t
                for a in self.store.library["albums"]
                for t in a["tracks"]
                if t["id"] in ids
            }
            for key, status, raw in conn.execute(
                "SELECT id,status,payload FROM queue"
            ).fetchall():
                job = json.loads(raw)
                if (
                    status in ACTIVE
                    and user_id in job.get("requested_by", [])
                    and any(same_song(job, t) for t in selected.values())
                ):
                    job["requested_by"] = [
                        u for u in job["requested_by"] if u != user_id
                    ]
                    conn.execute(
                        "UPDATE queue SET payload=?,status=? WHERE id=?",
                        (
                            json.dumps(job),
                            status if job["requested_by"] else "cancelled",
                            key,
                        ),
                    )
            for record, _ in prepared:
                record["operation_id"] = operation["id"]
                self.recovery.begin(conn, record)
            conn.execute(
                "INSERT INTO documents VALUES (?,?)",
                ("library-removal:" + operation["id"], json.dumps(operation)),
            )
        try:
            for record, root in prepared:
                self.recovery.finish(record, root)
            operation["status"] = "removed"
            self.recovery.save("library-removal:" + operation["id"], operation)
        finally:
            self.store.refresh_library()
        return {
            "id": operation["id"],
            "removed": len(ids),
            "deleted": len(prepared),
            "kept": len(ids) - len(prepared),
            "undo_until": operation["created_at"] + 30,
        }

    @coordinated
    def undo(self, user_id, operation_id):
        operation = self.store.read_document("library-removal:" + operation_id)
        if operation.get("user_id") != user_id:
            raise LookupError("Removal not found.")
        if operation.get("status") == "undone":
            return {"restored": 0}
        if self.clock() > operation["created_at"] + 30:
            raise ValueError(
                "Undo expired. Ask the administrator to restore from Recovery."
            )
        for recovery_id in operation["recovery_ids"]:
            record = self.store.read_document("removed:" + recovery_id)
            if record.get("status") == "removing":
                self.recovery.check_mount(Path(record["root"]))
                self.recovery.finish(record, Path(record["root"]))
            if record.get("status") != "restored":
                self.recovery.restore(recovery_id)
        for identity in operation["track_ids"]:
            if not track_file(self.store, identity).is_file():
                raise ValueError("A file is unavailable; restore stopped.")
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            added = sum(
                conn.execute(
                    "INSERT OR IGNORE INTO library_members VALUES (?,?,?,?)", row
                ).rowcount
                for row in operation["memberships"]
            )
            if added:
                bump(conn, user_id)
            operation["status"] = "undone"
            conn.execute(
                "UPDATE documents SET payload=? WHERE name=?",
                (json.dumps(operation), "library-removal:" + operation_id),
            )
        return {"restored": len(operation["track_ids"])}
