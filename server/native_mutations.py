"""Commit personal edits and their idempotency receipts in one SQLite transaction."""

import hashlib
import json
import time
from datetime import datetime, timezone
from uuid import UUID
from .lifecycle import coordinated
from .playlists import Playlists, PlaylistConflict, ensure_room
from .user_library import bump


class NativeMutations:
    def __init__(self, store):
        self.store = store

    @coordinated
    def apply(self, user_id, operation):
        key = str(UUID(operation["operation_id"]))
        fingerprint = hashlib.sha256(
            json.dumps(operation, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        playlists = Playlists(self.store, user_id)
        action = operation["action"]
        result = {"operation_id": key, "applied": True}
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute(
                "SELECT fingerprint,result FROM native_mutations WHERE user_id=? AND operation_id=?",
                (user_id, key),
            ).fetchone()
            if previous:
                if previous[0] != fingerprint:
                    raise PlaylistConflict(
                        "This edit identifier was reused for another edit."
                    )
                return json.loads(previous[1])
            if (
                conn.execute(
                    "SELECT count(*) FROM native_mutations WHERE user_id=?", (user_id,)
                ).fetchone()[0]
                >= 100000
            ):
                raise ValueError(
                    "Native edit receipt storage requires administrator review."
                )
            if action == "preferences":
                preferences = operation.get("preferences")
                if preferences is None:
                    raise ValueError("Preferences are required")
                from .routes.personal import merged_preferences

                # An app that doesn't send a setting keeps what was saved (#202).
                saved = conn.execute(
                    "SELECT payload FROM user_preferences WHERE user_id=?", (user_id,)
                ).fetchone()
                conn.execute(
                    "INSERT INTO user_preferences VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET payload=excluded.payload",
                    (
                        user_id,
                        json.dumps(
                            merged_preferences(
                                json.loads(saved[0]) if saved else {}, preferences
                            )
                        ),
                    ),
                )
            else:
                playlist_id = operation.get("playlist_id")
                if action == "create":
                    playlist_id = playlist_id or key
                    try:
                        playlist_id = str(UUID(playlist_id))
                    except ValueError:
                        raise ValueError("A new playlist identifier must be a UUID")
                    if conn.execute(
                        "SELECT 1 FROM playlists WHERE id=?", (playlist_id,)
                    ).fetchone():
                        raise PlaylistConflict("Playlist identifier already exists")
                    payload = {
                        "id": playlist_id,
                        "name": self.name(operation.get("name")),
                        "tracks": [],
                        "source": "pig-music",
                        "imported_at": datetime.now(timezone.utc).isoformat(),
                    }
                elif action == "favourite":
                    playlist_id = "pig-favourites:" + user_id
                    row = conn.execute(
                        "SELECT payload FROM playlists WHERE id=? AND owner_id=?",
                        (playlist_id, user_id),
                    ).fetchone()
                    payload = (
                        json.loads(row[0])
                        if row
                        else {
                            "id": playlist_id,
                            "name": "Favourites",
                            "tracks": [],
                            "source": "pig-music",
                            "favourites": True,
                            "imported_at": datetime.now(timezone.utc).isoformat(),
                        }
                    )
                    track = operation.get("track")
                    value = operation.get("favourite")
                    if track is None or value is None:
                        raise ValueError("Song and favourite choice are required")
                    payload["tracks"] = [
                        t
                        for t in payload["tracks"]
                        if not playlists.same_song(t, track)
                    ]
                    if value:
                        ensure_room(payload["tracks"])
                        payload["tracks"].append(track)
                    result["favourited"] = value
                else:
                    row = conn.execute(
                        "SELECT payload FROM playlists WHERE id=? AND owner_id=?",
                        (playlist_id, user_id),
                    ).fetchone()
                    if not row:
                        raise LookupError("Playlist not found")
                    payload = json.loads(row[0])
                    if payload.get("favourites"):
                        raise ValueError("Use the favourite action")
                    if action != "append" and payload.get(
                        "revision", 0
                    ) != operation.get("revision"):
                        raise PlaylistConflict(
                            "Playlist changed. Review this edit before reapplying it."
                        )
                    if action == "append":
                        if operation.get("track") is None:
                            raise ValueError("Song is required")
                        ensure_room(payload["tracks"])
                        payload["tracks"].append(operation["track"])
                    elif action == "rename":
                        payload["name"] = self.name(operation.get("name"))
                    elif action == "reorder":
                        order = operation.get("order")
                        if order is None or sorted(order) != list(
                            range(len(payload["tracks"]))
                        ):
                            raise ValueError(
                                "Order must contain every current position exactly once"
                            )
                        payload["tracks"] = [
                            payload["tracks"][index] for index in order
                        ]
                    elif action == "remove":
                        position = operation.get("position")
                        if position is None or not 0 <= position < len(
                            payload["tracks"]
                        ):
                            raise ValueError("Song position is outside this playlist")
                        payload["tracks"].pop(position)
                    elif action != "delete":
                        raise ValueError("Unsupported native edit")
                payload["revision"] = payload.get("revision", 0) + 1
                if action == "delete":
                    conn.execute(
                        "DELETE FROM playlists WHERE id=? AND owner_id=?",
                        (playlist_id, user_id),
                    )
                else:
                    conn.execute(
                        "INSERT INTO playlists(id,payload,owner_id) VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                        (playlist_id, json.dumps(payload), user_id),
                    )
                result.update(playlist_id=playlist_id, revision=payload["revision"])
                bump(conn, user_id)
            conn.execute(
                "INSERT INTO native_mutations VALUES (?,?,?,?,?)",
                (user_id, key, fingerprint, json.dumps(result), int(time.time())),
            )
        return result

    @staticmethod
    def name(value):
        value = (value or "").strip()
        if not value or len(value) > 100:
            raise ValueError("Choose a playlist name up to 100 characters")
        return value
