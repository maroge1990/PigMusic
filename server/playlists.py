"""Ordered, persistent playlist membership, independent of the migration inventory."""

import json, uuid
from collections import defaultdict
from datetime import datetime, timezone
from .library import identifier
from .library import normalise
from .lifecycle import coordinated
from .matching import context


class PlaylistConflict(ValueError):
    pass


# One size for every playlist, however it is edited: web, iPhone, favourites and
# imports all stay within what native reorder payloads can represent.
MAX_PLAYLIST_ENTRIES = 10000


class PlaylistFull(ValueError):
    def __init__(self):
        super().__init__("Playlist has reached 10,000 songs")


def ensure_room(tracks):
    if len(tracks) >= MAX_PLAYLIST_ENTRIES:
        raise PlaylistFull()


class Playlists:
    def __init__(self, store, user_id=None):
        self.store = store
        self.user_id = user_id

    def owner(self):
        if self.user_id:
            return self.user_id
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT id FROM users WHERE role='owner' ORDER BY created_at LIMIT 1"
            ).fetchone()
        return row[0] if row else None

    def clause(self):
        identity = self.owner()
        if self.user_id:
            return " AND owner_id=?", (identity,)
        if identity:
            return " AND (owner_id=? OR owner_id IS NULL)", (identity,)
        return " AND owner_id IS NULL", ()

    def raw(self, playlist_id):
        clause, params = self.clause()
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT payload FROM playlists WHERE id=?" + clause,
                (playlist_id, *params),
            ).fetchone()
        if not row:
            raise ValueError("Playlist not found.")
        return json.loads(row[0])

    def detail(self, playlist_id):
        playlist = self.raw(playlist_id)
        entries = []
        index = self.store.index
        if self.user_id:
            index = self.store.user_libraries.match_index(self.user_id)
        for position, track in enumerate(playlist["tracks"]):
            result = index.match(track)
            match = result.track
            # A household file this person has not added: playable from the
            # server without being in their library.
            household = (
                self.store.index.match(track).track
                if self.user_id and not match
                else None
            )
            entries.append(
                {
                    **track,
                    "entry_id": playlist_id + ":" + str(position),
                    "position": position,
                    "status": result.status,
                    "match_kind": result.kind,
                    "saved_id": match["id"] if match else None,
                    "saved_track": match,
                    "household_available": bool(household),
                    "household_track": household,
                }
            )
        return {
            **playlist,
            "revision": playlist.get("revision", 0),
            "entries": entries,
            "track_count": len(entries),
            "available_count": sum(e["status"] == "downloaded" for e in entries),
            "review_count": sum(e["status"] == "review" for e in entries),
        }

    def list(self):
        clause, params = self.clause()
        with self.store.connect() as conn:
            ids = [
                r[0]
                for r in conn.execute(
                    "SELECT id FROM playlists WHERE 1=1" + clause, params
                )
            ]
        return [
            {
                k: v
                for k, v in self.detail(i).items()
                if k not in {"tracks", "track_ids", "entries"}
            }
            for i in ids
        ]

    def review(self, playlist_id):
        playlist = self.raw(playlist_id)
        source = {
            "tracks": playlist["tracks"],
            "source_name": playlist["name"],
            "imported_at": playlist["imported_at"],
        }
        with self.store.connect() as conn:
            conn.execute(
                "INSERT INTO documents VALUES ('migration',?) ON CONFLICT(name) DO UPDATE SET payload=excluded.payload",
                (json.dumps(source),),
            )
            conn.execute("DELETE FROM selections")
            conn.execute("DELETE FROM migration_decisions")
        self.store.source = source
        migration = self.store.migration()
        self.store.update_selection(
            [
                t["id"]
                for a in migration["albums"]
                for t in a["tracks"]
                if t["status"] != "downloaded"
            ],
            True,
        )
        return self.store.migration()

    @coordinated
    def create(self, name):
        name = name.strip()
        if not name or len(name) > 100:
            raise ValueError("Choose a playlist name up to 100 characters.")
        identity = str(uuid.uuid4())
        payload = {
            "id": identity,
            "name": name,
            "tracks": [],
            "imported_at": datetime.now(timezone.utc).isoformat(),
            "source": "pig-music",
        }
        with self.store.connect() as conn:
            conn.execute(
                "INSERT INTO playlists(id,payload,owner_id) VALUES (?,?,?)",
                (identity, json.dumps(payload), self.owner()),
            )
            if self.owner():
                from .user_library import bump

                bump(conn, self.owner())
        return self.detail(identity)

    def membership(self, track):
        key = identifier(
            "song-membership",
            track.get("musicbrainz_release_id") or "",
            track.get("musicbrainz_recording_id") or "",
            *[
                normalise(track.get(k, ""))
                for k in ("artist", "title", "album", "album_artist")
            ],
        )
        return key

    def same_song(self, left, right):
        from .matching import same_song

        return same_song(left, right)

    def add(self, playlist_id, track, *, allow_repeat=False):
        return self.add_many(playlist_id, [track], allow_repeat=allow_repeat)

    @coordinated
    def add_many(self, playlist_id, tracks, *, allow_repeat=False):
        """Appends songs in order in one transaction: all of them or none, so
        trying again after a failure never doubles the ones already in."""
        clause, params = self.clause()
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT payload FROM playlists WHERE id=?" + clause,
                (playlist_id, *params),
            ).fetchone()
            if not row:
                raise ValueError("Playlist not found.")
            payload = json.loads(row[0])
            repeat = allow_repeat and not payload.get("favourites")
            entries = list(payload["tracks"])
            # Same-song needs the same context, so only songs that share one
            # are compared; a bulk append near the limit stays quick.
            by_context = defaultdict(list)
            if not repeat:
                for entry in entries:
                    by_context[context(entry)].append(entry)
            for track in tracks:
                bucket = by_context[context(track)] if not repeat else []
                if repeat or not any(self.same_song(t, track) for t in bucket):
                    # Raises before anything is written: the whole batch fails.
                    ensure_room(entries)
                    entries.append(track)
                    bucket.append(track)
            payload["tracks"] = entries
            payload["revision"] = payload.get("revision", 0) + 1
            conn.execute(
                "UPDATE playlists SET payload=? WHERE id=?",
                (json.dumps(payload), playlist_id),
            )
            if self.owner():
                from .user_library import bump

                bump(conn, self.owner())
        return self.detail(playlist_id)

    @coordinated
    def edit(
        self, playlist_id, revision, *, name=None, order=None, remove=None, delete=False
    ):
        """Apply an ownership-checked edit atomically, rejecting stale positions."""
        clause, params = self.clause()
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT payload FROM playlists WHERE id=?" + clause,
                (playlist_id, *params),
            ).fetchone()
            if not row:
                raise LookupError("Playlist not found.")
            payload = json.loads(row[0])
            if payload.get("revision", 0) != revision:
                raise PlaylistConflict("Playlist changed. Refresh before editing.")
            if payload.get("favourites"):
                raise ValueError("Use the favourites control to change favourites.")
            if name is not None:
                name = name.strip()
                if not name or len(name) > 100:
                    raise ValueError("Choose a playlist name up to 100 characters.")
                payload["name"] = name
            if order is not None:
                if sorted(order) != list(range(len(payload["tracks"]))):
                    raise ValueError(
                        "Order must contain every current position exactly once."
                    )
                payload["tracks"] = [payload["tracks"][i] for i in order]
            if remove is not None:
                if not 0 <= remove < len(payload["tracks"]):
                    raise ValueError("Song position is outside this playlist.")
                payload["tracks"].pop(remove)
            payload["revision"] = revision + 1
            if delete:
                conn.execute("DELETE FROM playlists WHERE id=?", (playlist_id,))
            else:
                conn.execute(
                    "UPDATE playlists SET payload=? WHERE id=?",
                    (json.dumps(payload), playlist_id),
                )
            from .user_library import bump

            if self.owner():
                bump(conn, self.owner())
        return {"deleted": True} if delete else self.detail(playlist_id)

    @coordinated
    def favourite(self, track, value=None):
        owner_id = self.owner()
        identity = "pig-favourites:" + owner_id if owner_id else "pig-favourites"
        key = self.membership(track)
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT payload FROM playlists WHERE id=?", (identity,)
            ).fetchone()
            payload = (
                json.loads(row[0])
                if row
                else {
                    "id": identity,
                    "name": "Favourites",
                    "tracks": [],
                    "imported_at": datetime.now(timezone.utc).isoformat(),
                    "source": "pig-music",
                    "favourites": True,
                }
            )
            exists = any(self.same_song(t, track) for t in payload["tracks"])
            if value is None:
                return {"favourited": exists}
            if value and not exists:
                ensure_room(payload["tracks"])
            payload["tracks"] = [
                t for t in payload["tracks"] if not self.same_song(t, track)
            ]
            if value:
                payload["tracks"].append(track)
            payload["revision"] = payload.get("revision", 0) + 1
            conn.execute(
                "INSERT INTO playlists(id,payload,owner_id) VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                (identity, json.dumps(payload), owner_id),
            )
            if owner_id:
                from .user_library import bump

                bump(conn, owner_id)
        return {"favourited": value}
