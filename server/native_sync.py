"""Durable final-state deltas and user/revision-bound pagination.

A checkpoint is materialised at a sync read, not on every individual mutation.
It records the diff since that user's previous observed state, allowing offline
clients to converge without storing a full snapshot for each edit.
"""

import base64
import functools
import hashlib
import json
import time
import uuid
from .api_models import ArtistDTO, AlbumDTO, TrackDTO, PlaylistDTO
from .user_library import stored_cover_sha

MODELS = {
    "artists": ArtistDTO,
    "albums": AlbumDTO,
    "tracks": TrackDTO,
    "playlists": PlaylistDTO,
}


# A first sync of a large library is bounded by round trips, not bytes: 50k
# songs took 250 sequential pages of 200. Pages now hold up to 1000 items
# but never much more than PAGE_BYTES of JSON, whatever the metadata.
PAGE_LIMIT = 1000
PAGE_BYTES = 2 * 1024 * 1024
# A collection's later pages come from the revision its first page captured,
# kept this long in memory (SYNC1): no capture and no lock per page, and a
# change meanwhile no longer restarts a phone's download.
CURSOR_SECONDS = 15 * 60


class ResyncRequired(ValueError):
    pass


def schema_digest():
    """The shape of every synced object. A capture's signature includes it,
    so a server whose DTOs gained a field (an artist's `cover_sha`) diffs
    everything again and sends the changed objects as ordinary updates."""
    return _digest(tuple(MODELS.items()))


@functools.cache
def _digest(models):
    shapes = {kind: model.model_json_schema() for kind, model in models}
    return hashlib.sha256(json.dumps(shapes, sort_keys=True).encode()).hexdigest()[:16]


class NativeSync:
    def __init__(self, store):
        self.store = store
        # Per person and collection: the projection objects whose stored
        # payload was verified at the last committed capture. The projection
        # keeps the same objects across playlist-only edits, so those skip
        # validation and serialisation entirely.
        self.clean = {}
        # {user: {revision: (captured data, monotonic time)}}, a few each.
        self.captured = {}

    def dto(self, kind, raw):
        if kind == "tracks":
            from .user_library import track_dto

            # Requested songs are not in the household index yet; they carry
            # the album they will join.
            album_id = self.store.index.album_by_track.get(raw["id"]) or raw.get(
                "album_id"
            )
            album = self.store.albums_by_id.get(album_id, {})
            raw = {
                **track_dto(raw),
                "album_id": album_id,
                "cover_url": album.get("cover_url") or raw.get("cover_url"),
                "cover_sha": stored_cover_sha(album) or raw.get("cover_sha"),
            }
        elif kind in ("albums", "artists"):
            raw = {**raw, "cover_sha": stored_cover_sha(raw)}
        return MODELS[kind].model_validate(raw).model_dump(mode="json")

    def capture(self, user_id):
        with self.store.lifecycle_lock, self.store.library_lock:
            projection = self.store.user_libraries.projection(user_id)
            signature = f"{projection['revision']}:{schema_digest()}"
            revision = uuid.uuid4().hex
            with self.store.connect() as conn:
                previous = conn.execute(
                    "SELECT revision,signature,captured_at FROM sync_revisions WHERE user_id=? ORDER BY sequence DESC LIMIT 1",
                    (user_id,),
                ).fetchone()
                if (
                    previous
                    and previous[1] == signature
                    and previous[2] >= int(time.time()) - 30 * 86400
                ):
                    return self.remember(
                        user_id, {**projection, "revision": previous[0]}
                    )
                from .playlists import Playlists
                from .routes.personal import playlist_dto

                playlists = Playlists(self.store, user_id)
                projection = {
                    **projection,
                    "playlists": [
                        playlist_dto(playlists.detail(p["id"]))
                        for p in playlists.list()
                    ],
                }
                conn.execute("BEGIN IMMEDIATE")
                upserts = {kind: [] for kind in MODELS}
                deletes = {kind: [] for kind in MODELS}
                known = self.clean.get(user_id, {})
                verified = {kind: {} for kind in MODELS}
                for kind, model in MODELS.items():
                    before = dict(
                        conn.execute(
                            "SELECT entity_id,payload FROM sync_entities WHERE user_id=? AND entity=?",
                            (user_id, kind),
                        )
                    )
                    unchanged = known.get(kind, {})
                    for raw in projection[kind]:
                        verified[kind][id(raw)] = raw
                        if unchanged.get(id(raw)) is raw and raw["id"] in before:
                            del before[raw["id"]]
                            continue
                        item = self.dto(kind, raw)
                        payload = json.dumps(
                            item, sort_keys=True, separators=(",", ":")
                        )
                        if before.pop(item["id"], None) != payload:
                            upserts[kind].append(item)
                            conn.execute(
                                "INSERT INTO sync_entities VALUES (?,?,?,?) ON CONFLICT(user_id,entity,entity_id) DO UPDATE SET payload=excluded.payload",
                                (user_id, kind, item["id"], payload),
                            )
                    deletes[kind] = list(before)
                    conn.executemany(
                        "DELETE FROM sync_entities WHERE user_id=? AND entity=? AND entity_id=?",
                        [(user_id, kind, key) for key in before],
                    )
                conn.execute(
                    "INSERT INTO sync_revisions(user_id,revision,signature,captured_at,payload) VALUES (?,?,?,?,?)",
                    (
                        user_id,
                        revision,
                        signature,
                        int(time.time()),
                        json.dumps({"upserts": upserts, "deletes": deletes}),
                    ),
                )
                conn.execute(
                    "DELETE FROM sync_revisions WHERE captured_at<?",
                    (int(time.time()) - 30 * 86400,),
                )
            # Only after the commit: these objects now match their rows.
            self.clean[user_id] = verified
            return self.remember(user_id, {**projection, "revision": revision})

    def remember(self, user_id, data):
        kept = self.captured.setdefault(user_id, {})
        kept.pop(data["revision"], None)
        kept[data["revision"]] = (data, time.monotonic())
        while len(kept) > 3:
            kept.pop(next(iter(kept)))
        return data

    def recalled(self, user_id, revision):
        found = self.captured.get(user_id, {}).get(revision)
        if found and time.monotonic() - found[1] < CURSOR_SECONDS:
            return found[0]
        return None

    def page(self, user_id, kind, cursor=None, limit=200):
        if not 1 <= limit <= PAGE_LIMIT:
            raise ValueError(f"Page limit must be between 1 and {PAGE_LIMIT}")
        offset = 0
        if not cursor:
            data = self.capture(user_id)
        else:
            try:
                encoded = json.loads(
                    base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
                )
                identity, collection, revision, offset = encoded
                if not isinstance(offset, int) or offset < 0:
                    raise ValueError()
            except (ValueError, TypeError):
                raise ValueError("Invalid cursor")
            if identity != user_id or collection != kind:
                raise ResyncRequired("Pagination changed. Restart this collection.")
            # The cursor's own revision when still kept; otherwise only an
            # unchanged library continues it.
            data = self.recalled(user_id, revision) or self.capture(user_id)
            if revision != data["revision"]:
                raise ResyncRequired("Pagination changed. Restart this collection.")
        values = data[kind]
        items, size = [], 0
        for value in values[offset : offset + limit]:
            item = self.dto(kind, value)
            size += len(json.dumps(item, separators=(",", ":")))
            if items and size > PAGE_BYTES:
                break
            items.append(item)
        following = offset + len(items)
        next_cursor = (
            base64.urlsafe_b64encode(
                json.dumps([user_id, kind, data["revision"], following]).encode()
            )
            .decode()
            .rstrip("=")
            if following < len(values)
            else None
        )
        return {
            "items": items,
            "total": len(values),
            "revision": data["revision"],
            "next_cursor": next_cursor,
        }

    def changes(self, user_id, since):
        current = self.capture(user_id)
        with self.store.connect() as conn:
            start = conn.execute(
                "SELECT sequence FROM sync_revisions WHERE user_id=? AND revision=? AND captured_at>=?",
                (user_id, since, int(time.time()) - 30 * 86400),
            ).fetchone()
            if not start:
                raise ResyncRequired(
                    "Sync history expired. Download the library again."
                )
            upserts, deletes = {k: {} for k in MODELS}, {k: set() for k in MODELS}
            for (raw,) in conn.execute(
                "SELECT payload FROM sync_revisions WHERE user_id=? AND sequence>? ORDER BY sequence",
                (user_id, start[0]),
            ):
                batch = json.loads(raw)
                for kind in MODELS:
                    for value in batch["upserts"][kind]:
                        upserts[kind][value["id"]] = value
                        deletes[kind].discard(value["id"])
                    for key in batch["deletes"][kind]:
                        upserts[kind].pop(key, None)
                        deletes[kind].add(key)
            return {
                "revision": current["revision"],
                "upserts": {k: list(v.values()) for k, v in upserts.items()},
                "deletes": {k: sorted(v) for k, v in deletes.items()},
            }
