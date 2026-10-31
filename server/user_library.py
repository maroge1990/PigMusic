"""Personal projections over shared immutable household metadata.

Membership and request beneficiaries are durable; projection caches only retain
references and small per-user lists. User data never changes household identity.
"""

from collections import OrderedDict, defaultdict
import hashlib
import json
import re
import time
from urllib.parse import urlsplit

from .library import identifier, normalise
from .media import public_settings, saved_track_ids
from .paths import versioned
from .quality import LABELS as QUALITY_LABELS
from .lifecycle import coordinated
from .playlists import MAX_PLAYLIST_ENTRIES

TRACK_FIELDS = frozenset(
    {
        "id",
        "title",
        "artist",
        "album",
        "album_artist",
        "year",
        "track_number",
        "disc_number",
        "duration",
        "codec",
        "bitrate",
        "album_id",
        "cover_sha",
        "cover_url",
        "available",
        "musicbrainz_release_id",
        "musicbrainz_recording_id",
        "isrcs",
        "genre",
    }
)


def _text(value):
    """SQLite's UTC "2026-10-19 07:00:00" as ISO 8601 with its zone."""
    text = str(value or "")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", text):
        return text.replace(" ", "T") + "Z"
    return text


def _stamp(item):
    """Sortable creation order for a queue item (SQLite timestamp text)."""
    digits = re.sub(r"\D", "", _text(item.get("created_at")))
    return int(digits or 0)


def _quality(item):
    """What the file is (or was asked to be), in words: "FLAC", "MP3 320 kbps"."""
    codec = (item.get("codec") or "").upper()
    bitrate = item.get("bitrate_kbps") or item.get("bitrate")
    if codec in {"FLAC", "ALAC"}:
        return codec
    if codec and bitrate:
        return f"{codec} {round(bitrate)} kbps"
    profile = item.get("effective_quality") or item.get("quality_profile")
    return QUALITY_LABELS.get(profile, codec or None)


_ACTIVE = ("waiting_for_sources", "searching", "downloading", "validating", "importing")
_ACTIVE_SQL = "(" + ",".join(f"'{s}'" for s in _ACTIVE) + ")"
_MINE = "EXISTS (SELECT 1 FROM json_each(queue.payload,'$.requested_by') WHERE value=?)"
_PRESENTED = TRACK_FIELDS | {
    "source",
    "effective_quality",
    "bitrate_kbps",
    "status",
    "created_at",
    "updated_at",
    "progress",
    "quality_profile",
    "received_bytes",
    "total_bytes",
    "error",
}


def _job(key, raw, status, created, updated):
    return {
        "id": key,
        **json.loads(raw),
        "status": status,
        "created_at": created,
        "updated_at": updated,
    }


def _present(job):
    """A queue job as its requester sees it: no paths, no provider errors."""
    job = {k: v for k, v in job.items() if k in _PRESENTED}
    active = job["status"] in _ACTIVE
    job["server_state"] = (
        "requested"
        if active
        else (
            "in_library"
            if job["status"] == "completed"
            else "failed" if job["status"] == "failed" else "not_in_library"
        )
    )
    total = job.get("total_bytes") or 0
    job["request_progress"] = (
        min(1, (job.get("received_bytes") or 0) / total) if active and total else None
    )
    if job.get("error"):
        job["error"] = "Acquisition failed. Retry or ask your administrator."
    return job


def request_scope(tracks):
    """What each track of one request belongs to: its album when the request
    asked for two or more songs of that release, otherwise the song alone.

    Recorded per requester on the queue job, so Downloads groups by what was
    asked for rather than by tags.
    """
    counts = defaultdict(int)
    keys = []
    for track in tracks:
        album = normalise(track.get("album") or "")
        key = (
            (
                normalise(track.get("album_artist") or track.get("artist") or ""),
                album,
                track.get("musicbrainz_release_id") or "",
            )
            if album
            else None
        )
        keys.append(key)
        if key:
            counts[key] += 1
    return [["album", *key] if key and counts[key] > 1 else ["song"] for key in keys]


def _phase(item):
    status = item["status"]
    if status in {"searching", "downloading", "validating", "importing"}:
        return "active"
    if status == "waiting_for_sources":
        return "waiting"
    return "done" if status in {"completed", "downloaded"} else "failed"


# Which of two jobs for the same song to show: the most advanced, then newest.
_RETRY_RANK = {"active": 0, "waiting": 1, "done": 2, "failed": 3}


def _group(jobs, user_id):
    """Jobs by request group: {key: {slot: job}}.

    Albums are keyed by artist, title and release. A job without a release
    joins the album's only known release; with several, it stays apart. A
    song requested on its own joins an album request only as a repeat of a
    song that album request already has.
    """
    albums, songs = [], []
    for job in jobs:
        scope = (job.get("request_scope") or {}).get(user_id)
        if scope is None:
            # Requests made before scopes were recorded: their tags decide.
            album = normalise(job.get("album") or "")
            scope = (
                [
                    "album",
                    normalise(job.get("album_artist") or job.get("artist") or ""),
                    album,
                    job.get("musicbrainz_release_id") or "",
                ]
                if album
                else ["song"]
            )
        (albums if scope[0] == "album" else songs).append((tuple(scope), job))
    releases = defaultdict(set)
    for scope, _ in albums:
        if scope[3]:
            releases[scope[:3]].add(scope[3])

    def album_key(base, release):
        if not release and len(releases[base]) == 1:
            release = next(iter(releases[base]))
        return (*base, release)

    groups = {}
    for scope, job in albums:
        _place(groups.setdefault(album_key(scope[:3], scope[3]), {}), job)
    for _, job in songs:
        album = normalise(job.get("album") or "")
        if album:
            base = (
                "album",
                normalise(job.get("album_artist") or job.get("artist") or ""),
                album,
            )
            key = album_key(base, job.get("musicbrainz_release_id") or "")
            if key in groups and _place(groups[key], job, join_only=True):
                continue
        _place(groups.setdefault(("song", job["id"]), {}), job)
    return groups


def _place(group, job, join_only=False):
    """Put `job` in its song slot, collapsing repeats. A repeat names the same
    song and position and no different recording. With `join_only`, only a
    repeat is placed. Returns whether the job was placed."""
    title = (
        normalise(job.get("title", "")),
        job.get("disc_number") or 1,
        job.get("track_number") or 0,
    )
    recording = job.get("musicbrainz_recording_id")
    for slot, current in group.items():
        other = current.get("musicbrainz_recording_id")
        if slot[0] != title or (recording and other and recording != other):
            continue
        if (_RETRY_RANK[_phase(job)], -_stamp(job)) < (
            _RETRY_RANK[_phase(current)],
            -_stamp(current),
        ):
            group[slot] = job
        return True
    if join_only:
        return False
    group[(title, job["id"])] = job
    return True


def _group_summary(key, slots):
    songs = sorted(
        (_present(job) for job in slots.values()),
        key=lambda i: (
            i.get("disc_number") or 1,
            i.get("track_number") or 0,
            i.get("title", ""),
        ),
    )
    phases = [_phase(i) for i in songs]
    first = songs[0]
    done = phases.count("done")
    partial = sum(
        i.get("request_progress") or 0 for i, p in zip(songs, phases) if p == "active"
    )
    album = key[0] == "album"
    # Unfinished work first; a group is done only when every song is.
    state = next((p for p in ("active", "waiting", "failed") if p in phases), "done")
    return {
        "id": identifier("request-group", *key),
        "kind": "album" if album else "song",
        "title": first.get("album") if album else first.get("title", ""),
        "artist": first.get("album_artist") or first.get("artist", ""),
        "release_id": (
            (key[3] or None) if album else first.get("musicbrainz_release_id")
        ),
        "state": state,
        "songs": len(songs),
        "done": done,
        "active": phases.count("active"),
        "waiting": phases.count("waiting"),
        "failed": phases.count("failed"),
        "progress": (done + partial) / len(songs),
        "requested_at": min(_text(i.get("created_at")) for i in songs),
        "updated_at": max(_text(i.get("updated_at")) for i in songs),
        "items": [
            {
                **i,
                "created_at": _text(i.get("created_at")),
                "updated_at": _text(i.get("updated_at")),
                "source": i.get("source"),
                "quality": _quality(i),
            }
            for i in songs
        ],
    }


class SearchChanged(ValueError):
    pass


def stored_cover_sha(item):
    """Embedded art hash, else the hash of server-fetched art in the cover store.

    Native clients address artwork only by hash; albums without embedded art
    (catalogue covers fetched by the server) would otherwise never show one.
    """
    if item.get("cover_sha"):
        return item["cover_sha"]
    match = re.fullmatch(
        r"/api/(?:v1/covers|artwork)/([0-9a-f]{64})\.jpg", item.get("cover_url") or ""
    )
    return match[1] if match else None


def add_pending(albums, household_albums, pending):
    """Show requested songs in the album they will join, with their state.

    A request joins the person's album for its release (or title and artist),
    else the household album, else a new album named by the request. Returns
    {artist_id: (name, [album ids])} for albums that exist only as requests.
    """

    def key(album_artist, title):
        return normalise(album_artist or ""), normalise(title or "")

    personal = {a["id"]: a for a in albums}
    releases, titles = {}, {}
    for album in list(albums) + list(household_albums):
        if album.get("musicbrainz_release_id"):
            releases.setdefault(album["musicbrainz_release_id"], album["id"])
        titles.setdefault(key(album.get("artist"), album.get("title")), album["id"])
    household = {a["id"]: a for a in household_albums}
    extra = {}
    for job in sorted(
        pending, key=lambda j: (j.get("disc_number", 1), j.get("track_number", 0))
    ):
        album_artist = job.get("album_artist") or job["artist"]
        title = job.get("album") or job["title"]
        album_id = releases.get(job.get("musicbrainz_release_id")) or titles.get(
            key(album_artist, title)
        )
        if album_id is None:
            seed = job.get("musicbrainz_release_id") or "|".join(
                key(album_artist, title)
            )
            album_id = "requested-" + hashlib.sha256(seed.encode()).hexdigest()[:24]
            titles[key(album_artist, title)] = album_id
            if job.get("musicbrainz_release_id"):
                releases[job["musicbrainz_release_id"]] = album_id
        album = personal.get(album_id)
        if album is None:
            source = household.get(album_id)
            artist_id = (source or {}).get("artist_id") or identifier(
                "artist", normalise(album_artist)
            )
            album = {
                **(source or {}),
                "id": album_id,
                "title": (source or {}).get("title") or title,
                "artist": (source or {}).get("artist") or album_artist,
                "artist_id": artist_id,
                "year": (source or {}).get("year") or job.get("year") or 0,
                "category": (source or {}).get("category") or "album",
                "musicbrainz_release_id": (source or {}).get("musicbrainz_release_id")
                or job.get("musicbrainz_release_id"),
                "cover_sha": stored_cover_sha(source or {}),
                "tracks": [],
            }
            albums.append(album)
            personal[album_id] = album
            extra.setdefault(artist_id, (album["artist"], []))[1].append(album_id)
        position = (job.get("disc_number") or 1, job.get("track_number") or 0)
        if any(
            (t.get("disc_number") or 1, t.get("track_number") or 0) == position
            and position[1]
            or (
                job.get("musicbrainz_recording_id")
                and t.get("musicbrainz_recording_id") == job["musicbrainz_recording_id"]
            )
            for t in album["tracks"]
        ):
            continue
        album["tracks"].append(
            {
                **{k: job[k] for k in TRACK_FIELDS if k in job and k != "id"},
                "id": "request-" + job["id"],
                "album_id": album_id,
                "cover_url": album.get("cover_url"),
                "cover_sha": album.get("cover_sha"),
                "available": False,
                "server_state": "failed" if job["status"] == "failed" else "requested",
            }
        )
    for album in personal.values():
        if any(
            t.get("server_state") in {"requested", "failed"} for t in album["tracks"]
        ):
            album["tracks"].sort(
                key=lambda t: (t.get("disc_number") or 1, t.get("track_number") or 0)
            )
            album["track_count"] = len(album["tracks"])
            album["duration"] = sum(t.get("duration", 0) for t in album["tracks"])
    return extra


def playable(album):
    """The album with only its playable songs; None when it has none."""
    tracks = [t for t in album.get("tracks", []) if t.get("available", True)]
    if not tracks:
        return None
    return {**album, "tracks": tracks, "track_count": len(tracks)}


def household_track(track):
    return {
        **track_dto(track),
        "household_available": True,
        "server_state": "household_available",
    }


def track_dto(track):
    source_revision = hashlib.sha256(
        json.dumps(
            [
                track.get("id"),
                track.get("sha256"),
                track.get("size_bytes"),
                track.get("mtime_ns"),
            ]
        ).encode()
    ).hexdigest()
    return {
        **{key: value for key, value in track.items() if key in TRACK_FIELDS},
        # Stored rows may predate /api/v1 addresses.
        "cover_url": versioned(track.get("cover_url")),
        "source_sha": track.get("sha256"),
        "source_revision": source_revision,
        "server_state": track.get("server_state")
        or ("in_library" if track.get("available", True) else "failed"),
    }


def bump(conn, user_id):
    conn.execute(
        "INSERT INTO membership_versions(user_id,revision) VALUES (?,1) ON CONFLICT(user_id) DO UPDATE SET revision=revision+1",
        (user_id,),
    )


def initialise_owner(store):
    # Once done, the migration stays done: skip its lock and write transaction
    # on every later call (LOOP1; it ran on each media request).
    if getattr(store, "owner_initialised", False):
        return
    with store.library_lock, store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        owner = conn.execute(
            "SELECT id FROM users WHERE role='owner' AND disabled=0 ORDER BY created_at LIMIT 1"
        ).fetchone()
        if not owner:
            return
        if conn.execute(
            "SELECT 1 FROM documents WHERE name='household-membership-initialised'"
        ).fetchone():
            store.owner_initialised = True
            return
        identity = owner[0]
        now = int(time.time())
        for album in store.library["albums"]:
            conn.executemany(
                "INSERT OR IGNORE INTO library_members VALUES (?,?,?,'migration')",
                [(identity, t["id"], now) for t in album["tracks"]],
            )
        conn.execute(
            "UPDATE playlists SET owner_id=? WHERE owner_id IS NULL", (identity,)
        )
        favourites = conn.execute(
            "SELECT payload FROM playlists WHERE id='pig-favourites'"
        ).fetchone()
        if favourites:
            renamed = "pig-favourites:" + identity
            payload = {**json.loads(favourites[0]), "id": renamed}
            conn.execute(
                "UPDATE playlists SET id=?,payload=? WHERE id='pig-favourites'",
                (renamed, json.dumps(payload)),
            )
        for key, payload in conn.execute("SELECT id,payload FROM queue").fetchall():
            value = json.loads(payload)
            if "requested_by" not in value:
                value["requested_by"] = [identity]
                conn.execute(
                    "UPDATE queue SET payload=? WHERE id=?", (json.dumps(value), key)
                )
        for key, raw in conn.execute(
            "SELECT name,payload FROM documents WHERE name LIKE 'artist-job:%'"
        ).fetchall():
            if key.count(":") == 1:
                artist_id = key.split(":")[1]
                value = {**json.loads(raw), "id": artist_id, "user_id": identity}
                conn.execute(
                    "INSERT OR IGNORE INTO documents VALUES (?,?)",
                    ("artist-job:" + identity + ":" + artist_id, json.dumps(value)),
                )
        bump(conn, identity)
        conn.execute(
            "INSERT INTO documents VALUES ('household-membership-initialised',?)",
            (json.dumps({"owner_id": identity}),),
        )
    # Only after the transaction committed.
    store.owner_initialised = True


class UserLibrary:
    def __init__(self, store):
        self.store = store
        self.cache = OrderedDict()
        self.artwork_cache = {}
        self.index_cache = {}
        self.lookup_cache = {}

    def artwork_access(self, user_id):
        """Artwork a person may load, indexed once per projection revision."""
        data = self.projection(user_id)
        cached = self.artwork_cache.get(user_id)
        if cached and cached[0] == data["music_revision"]:
            return cached[1]
        access = {
            "items": {
                kind: {item["id"]: item for item in data[kind]}
                for kind in ("artists", "albums")
            },
            "paths": {
                urlsplit(item.get(field) or "").path
                for kind in ("artists", "albums", "tracks")
                for item in data[kind]
                for field in ("cover_url", "fallback_cover_url")
                if item.get(field)
            },
        }
        self.artwork_cache[user_id] = (data["music_revision"], access)
        return access

    def match_index(self, user_id):
        """The person's song-matching index, built once per projection revision
        (building it reads every saved song; playlist pages need it each time)."""
        from .matching import LibraryIndex

        data = self.projection(user_id)
        cached = self.index_cache.get(user_id)
        if cached and cached[0] == data["music_revision"]:
            return cached[1]
        index = LibraryIndex(data)
        self.index_cache[user_id] = (data["music_revision"], index)
        return index

    def projection(self, user_id):
        initialise_owner(self.store)
        with self.store.library_lock, self.store.connect() as conn:
            membership_revision = conn.execute(
                "SELECT revision FROM membership_versions WHERE user_id=?", (user_id,)
            ).fetchone()
            # The person is part of the validator: equal membership counters
            # for two people must never let a shared browser reuse a 304.
            signature = (
                user_id,
                self.store.library_revision,
                membership_revision[0] if membership_revision else 0,
                json.dumps(public_settings(self.store), sort_keys=True),
            )
            pending = self.pending_requests(conn, user_id)
            signature += (sorted((job["id"], job["status"]) for job in pending),)
            previous = self.cache.get(user_id)
            if previous and previous[0] == signature:
                self.cache.move_to_end(user_id)
                return previous[1]
            members = {
                r[0]
                for r in conn.execute(
                    "SELECT track_id FROM library_members WHERE user_id=?", (user_id,)
                )
            }
            # Playlist edits bump the membership counter (other devices must
            # notice them) without changing any music. Then keep the very same
            # music objects, so native capture can skip re-serialising them.
            content = (signature[1], signature[3], signature[4], frozenset(members))
            if previous and previous[2] == content:
                value = {
                    **previous[1],
                    "revision": hashlib.sha256(
                        json.dumps(signature).encode()
                    ).hexdigest(),
                }
                self.cache[user_id] = (signature, value, content)
                self.cache.move_to_end(user_id)
                return value
            albums = []
            for album in self.store.library["albums"]:
                cover_sha = stored_cover_sha(album)
                # Copies: the shared household library is never mutated.
                tracks = [
                    {
                        **track,
                        "album_id": album["id"],
                        "cover_url": album.get("cover_url"),
                        "cover_sha": track.get("cover_sha") or cover_sha,
                    }
                    for track in album["tracks"]
                    if track["id"] in members
                ]
                if tracks:
                    albums.append(
                        {
                            **album,
                            "cover_sha": cover_sha,
                            "tracks": tracks,
                            "track_count": len(tracks),
                            "duration": sum(t.get("duration", 0) for t in tracks),
                            "codecs": sorted(
                                {t.get("codec", "") for t in tracks if t.get("codec")}
                            ),
                        }
                    )
            extra_albums = add_pending(albums, self.store.library["albums"], pending)
            extra_ids = {i for _, ids in extra_albums.values() for i in ids}
            by_album = {a["id"]: a for a in albums}
            album_by_track = {t["id"]: a["id"] for a in albums for t in a["tracks"]}
            all_tracks = {t["id"]: t for a in albums for t in a["tracks"]}
            credited_by_artist = defaultdict(list)
            artist_ids = {}
            for track in all_tracks.values():
                name = track.get("artist", "")
                if name not in artist_ids:
                    artist_ids[name] = identifier("artist", normalise(name))
                credited_by_artist[artist_ids[name]].append(track)
            artists = []
            saved_artists = set()
            household_artists = list(self.store.library["artists"])
            known = {artist["id"] for artist in household_artists}
            # Artists whose only music here is still requested.
            for artist_id, (name, _) in extra_albums.items():
                if artist_id not in known:
                    household_artists.append(
                        {"id": artist_id, "name": name, "album_ids": []}
                    )
            for artist in household_artists:
                owned = [i for i in artist["album_ids"] if i in by_album]
                owned += [
                    i
                    for i in extra_albums.get(artist["id"], ("", []))[1]
                    if i not in owned
                ]
                credited = credited_by_artist[artist["id"]]
                associated = {t["id"] for t in credited}
                associated.update(
                    t["id"] for key in owned for t in by_album[key]["tracks"]
                )
                if associated & members:
                    saved_artists.add(artist["id"])
                if associated:
                    representative = (
                        owned[0] if owned else album_by_track[credited[0]["id"]]
                    )
                    # A household portrait (or its lazy alias) is not personal
                    # data; keep it rather than replacing it with album art.
                    portrait = (
                        artist.get("cover_url")
                        if artist.get("cover_url") != artist.get("fallback_cover_url")
                        else None
                    )
                    artists.append(
                        {
                            **artist,
                            "album_ids": owned,
                            "album_count": len(owned),
                            "track_count": len(associated),
                            "compilation_only": not bool(owned),
                            "cover_album_id": representative,
                            "cover_url": portrait
                            or by_album[representative].get("cover_url"),
                            "fallback_cover_url": by_album[representative].get(
                                "cover_url"
                            ),
                        }
                    )
            revision = hashlib.sha256(json.dumps(signature).encode()).hexdigest()
            value = {
                # Changes only with the music itself, not with playlist edits.
                "music_revision": revision,
                "artists": artists,
                "albums": albums,
                "tracks": list(all_tracks.values()),
                # Counts describe saved music; requested songs are not counted.
                "stats": {
                    "tracks": sum(t["id"] in members for t in all_tracks.values()),
                    "artists": len(saved_artists),
                    **{
                        kind
                        + "s": sum(
                            a["category"] == kind and a["id"] not in extra_ids
                            for a in albums
                        )
                        for kind in ("album", "compilation", "soundtrack", "musical")
                    },
                },
                "revision": revision,
                "media": public_settings(self.store),
                "snapshot": {
                    "captured_at": self.store.library.get("snapshot", {}).get(
                        "captured_at"
                    )
                },
            }
            self.cache[user_id] = (signature, value, content)
            self.cache.move_to_end(user_id)
            while len(self.cache) > 8:
                self.cache.popitem(last=False)
            return value

    def pending_requests(self, conn, user_id):
        """This person's requested songs that are not yet saved, newest state.

        Quality replacements of saved songs are not shown; cancelled and
        completed requests leave the projection.
        """
        jobs = []
        for request_id, status, raw in conn.execute(
            "SELECT id,status,payload FROM queue WHERE status IN "
            "('waiting_for_sources','searching','downloading','validating','importing','failed') "
            "AND EXISTS (SELECT 1 FROM json_each(queue.payload,'$.requested_by') WHERE value=?)",
            (user_id,),
        ):
            payload = json.loads(raw)
            if (
                payload.get("library_track_id")
                or not payload.get("title")
                or not payload.get("artist")
            ):
                continue
            jobs.append({**payload, "id": request_id, "status": status})
        return jobs

    def search(self, user_id, query, offset=0, limit=100, revision=None):
        if offset < 0 or offset > 1000000 or not 1 <= limit <= 200:
            raise ValueError("Invalid search page")
        query = normalise(query[:150])
        with self.store.library_lock:
            data = self.projection(user_id)
            if revision is not None and revision != data["revision"]:
                raise SearchChanged("Search changed. Refresh before loading more.")
            personal_ids = {t["id"] for t in data["tracks"]}

            def matches(track):
                return query and query in normalise(
                    track["artist"] + " " + track["title"] + " " + track["album"]
                )

            tracks = [t for t in data["tracks"] if matches(t)]
            household = [
                t
                for album in self.store.library["albums"]
                for t in album["tracks"]
                if t["id"] not in personal_ids
                and t.get("available", True)
                and matches(t)
            ]
            artists = [
                a for a in data["artists"] if query and query in normalise(a["name"])
            ]
            albums = [
                a
                for a in data["albums"]
                if query and query in normalise(a["artist"] + " " + a["title"])
            ]
            end = offset + limit
            return {
                "tracks": [track_dto(t) for t in tracks[offset:end]],
                "household_tracks": [
                    {
                        **track_dto(t),
                        "household_available": True,
                        "server_state": "household_available",
                    }
                    for t in household[offset:end]
                ],
                "artists": artists[offset:end],
                "albums": [
                    {k: v for k, v in a.items() if k not in {"tracks", "cover_key"}}
                    for a in albums[offset:end]
                ],
                "total_tracks": len(tracks),
                "total_household_tracks": len(household),
                "total_artists": len(artists),
                "total_albums": len(albums),
                "offset": offset,
                "next_offset": (
                    end
                    if end < max(len(tracks), len(household), len(artists), len(albums))
                    else None
                ),
                "revision": data["revision"],
            }

    def find(self, user_id, query, limit=50):
        """One ranked search over the person's library and the rest of the
        server (D-61): songs, albums, artists and playlists, each marked in
        the library or not, never split into lists by where they live."""
        from . import search_rank
        from .playlists import Playlists

        # Top results point into these lists, so each holds at least its best.
        if not 10 <= limit <= 200:
            raise ValueError("Invalid search page")
        query = query[:150]
        if not normalise(query):
            return {
                "top": [],
                **{k: [] for k in ("songs", "albums", "artists", "playlists")},
            }
        with self.store.library_lock:
            data = self.projection(user_id)
            mine = {
                "song": {t["id"] for t in data["tracks"]},
                "album": {a["id"] for a in data["albums"]},
                "artist": {a["id"] for a in data["artists"]},
            }
            songs = list(data["tracks"]) + [
                t
                for album in self.store.library["albums"]
                for t in album["tracks"]
                if t["id"] not in mine["song"] and t.get("available", True)
            ]
            server_albums = {
                a["id"]: a
                for a in map(playable, self.store.library["albums"])
                if a and a["id"] not in mine["album"]
            }
            albums = list(data["albums"]) + list(server_albums.values())
            # Only artists with music someone can play here (Codex, stage 2).
            playable_albums = [
                a for a in map(playable, self.store.library["albums"]) if a
            ]
            playable_ids = {a["id"] for a in playable_albums}
            # Compilation-only performers own no album; their credits count (#102).
            credited = {
                identifier("artist", normalise(t.get("artist", "")))
                for a in playable_albums
                for t in a["tracks"]
            }
            artists = list(data["artists"]) + [
                a
                for a in self.store.library["artists"]
                if a["id"] not in mine["artist"]
                and (
                    playable_ids & set(a.get("album_ids") or []) or a["id"] in credited
                )
            ]
        lists = Playlists(self.store, user_id).list()

        def result(kind, item, name, value):
            return {
                "kind": kind,
                "id": item["id"],
                "name": name,
                "score": value,
                "in_library": kind == "playlist" or item["id"] in mine[kind],
                "item": item,
            }

        ranked = {
            "song": [
                result(
                    "song",
                    t,
                    t["title"],
                    search_rank.named(query, t["title"], t.get("artist", "")),
                )
                for t in songs
            ],
            "album": [
                result(
                    "album",
                    a,
                    a["title"],
                    search_rank.named(query, a["title"], a.get("artist", "")),
                )
                for a in albums
            ],
            "artist": [
                result("artist", a, a["name"], search_rank.score(query, a["name"]))
                for a in artists
            ],
            "playlist": [
                result("playlist", p, p["name"], search_rank.score(query, p["name"]))
                for p in lists
            ],
        }
        ranked = {
            kind: search_rank.order([r for r in found if r["score"]])
            for kind, found in ranked.items()
        }

        def song(r):
            return (
                track_dto(r["item"]) if r["in_library"] else household_track(r["item"])
            )

        def album(r):
            value = {
                k: v for k, v in r["item"].items() if k not in {"tracks", "cover_key"}
            }
            if not r["in_library"]:
                value["server_state"] = "household_available"
            return {**value, "in_library": r["in_library"]}

        return {
            "top": search_rank.top(ranked),
            "songs": [song(r) for r in ranked["song"][:limit]],
            "albums": [album(r) for r in ranked["album"][:limit]],
            "artists": [
                {**r["item"], "in_library": r["in_library"]}
                for r in ranked["artist"][:limit]
            ],
            "playlists": [r["item"] for r in ranked["playlist"][:limit]],
        }

    def server_album(self, album_id):
        """An album on the server but not in the person's library, as search
        found it: only its playable songs, marked household (D-61)."""
        with self.store.library_lock:
            found = next(
                (a for a in self.store.library["albums"] if a["id"] == album_id), None
            )
            album = playable(found) if found else None
        if not album:
            return None
        return {
            **{k: v for k, v in album.items() if k not in {"tracks", "cover_key"}},
            "server_state": "household_available",
            "tracks": [household_track(t) for t in album["tracks"]],
        }

    def server_artist(self, artist_id):
        """An artist on the server but not in the person's library: the
        albums with playable songs, marked household (D-61)."""
        with self.store.library_lock:
            found = next(
                (a for a in self.store.library["artists"] if a["id"] == artist_id),
                None,
            )
            if not found:
                return None
            ids = set(found.get("album_ids") or [])
            every = [a for a in map(playable, self.store.library["albums"]) if a]
            albums = [a for a in every if a["id"] in ids]
            # Songs credited to them on other albums (compilations, #102).
            credits = [
                t
                for a in every
                if a["id"] not in ids
                for t in a["tracks"]
                if identifier("artist", normalise(t.get("artist", ""))) == artist_id
            ]
        if not albums and not credits:
            return None
        return {
            **found,
            "album_ids": [a["id"] for a in albums],
            "albums": [
                {
                    **{k: v for k, v in a.items() if k not in {"tracks", "cover_key"}},
                    "server_state": "household_available",
                }
                for a in albums
            ],
            "tracks": [household_track(t) for a in albums for t in a["tracks"]],
            "compilation_tracks": [household_track(t) for t in credits],
        }

    def lookup(self, user_id, songs):
        """Which of these songs are already in a person's library.

        A recording ID is decisive; otherwise title and artist must both match.
        """
        from .matching import compatible

        data = self.projection(user_id)
        cached = self.lookup_cache.get(user_id)
        if not cached or cached[0] != data["music_revision"]:
            albums = {a["id"]: a.get("title") for a in data["albums"]}
            recordings, names = {}, defaultdict(list)
            for track in data["tracks"]:
                if track.get("available", True) is False:
                    continue
                if track.get("musicbrainz_recording_id"):
                    recordings.setdefault(track["musicbrainz_recording_id"], track)
                # Compared as filed: a "Live at ..." album is a live recording.
                filed = {**track, "album": albums.get(track.get("album_id"))}
                names[(normalise(track["title"]), normalise(track["artist"]))].append(
                    (filed, track)
                )
            cached = (data["music_revision"], recordings, names)
            self.lookup_cache[user_id] = cached
        _, recordings, names = cached
        found = []
        for song in songs:
            # The shared policy: a live (remix, demo) song is not the studio one.
            track = recordings.get(song.get("musicbrainz_recording_id") or "") or next(
                (
                    track
                    for filed, track in names.get(
                        (normalise(song["title"]), normalise(song["artist"])), ()
                    )
                    if compatible(
                        {**song, "musicbrainz_recording_id": None},
                        {**filed, "musicbrainz_recording_id": None},
                    )
                ),
                None,
            )
            found.append(track_dto(track) if track else None)
        return found

    def recently_added(self, user_id, limit=12):
        """Albums most recently added to a person's library.

        One-off bootstrap attribution ('migration') is not "recent" music.
        """
        projection = self.projection(user_id)
        album_for = {
            track["id"]: album
            for album in projection["albums"]
            for track in album["tracks"]
        }
        with self.store.connect() as conn:
            rows = conn.execute(
                "SELECT track_id FROM library_members WHERE user_id=? "
                "AND source != 'migration' ORDER BY added_at DESC LIMIT 2000",
                (user_id,),
            ).fetchall()
        albums, seen = [], set()
        for (track_id,) in rows:
            album = album_for.get(track_id)
            if album and album["id"] not in seen:
                seen.add(album["id"])
                albums.append(
                    {k: v for k, v in album.items() if k not in {"tracks", "cover_key"}}
                )
                if len(albums) == limit:
                    break
        return {"albums": albums, "revision": projection["revision"]}

    def summary(self, user_id):
        projection = self.projection(user_id)
        from .playlists import Playlists

        return {
            **{key: value for key, value in projection.items() if key != "tracks"},
            "albums": [
                {k: v for k, v in a.items() if k not in {"tracks", "cover_key"}}
                for a in projection["albums"]
            ],
            "playlist_count": len(Playlists(self.store, user_id).list()),
        }

    def contains(self, user_id, track_id):
        initialise_owner(self.store)
        with self.store.connect() as conn:
            return (
                conn.execute(
                    "SELECT 1 FROM library_members WHERE user_id=? AND track_id=?",
                    (user_id, track_id),
                ).fetchone()
                is not None
            )

    @coordinated
    def add(self, user_id, track_ids, source="household-add"):
        initialise_owner(self.store)
        with self.store.library_lock, self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            available = {
                t["id"]
                for a in self.store.library["albums"]
                for t in a["tracks"]
                if t.get("available", True)
            }
            removing = {
                json.loads(raw)["track_id"]
                for (raw,) in conn.execute(
                    "SELECT payload FROM documents WHERE name LIKE 'removed:%'"
                )
                if json.loads(raw).get("status") in {"removing", "removed"}
            }
            available -= removing
            if not set(track_ids).issubset(available):
                raise ValueError("One or more files are unavailable")
            changed = 0
            for identity in set(track_ids):
                changed += conn.execute(
                    "INSERT OR IGNORE INTO library_members VALUES (?,?,?,?)",
                    (user_id, identity, int(time.time()), source),
                ).rowcount
            if changed:
                bump(conn, user_id)
                from .household_policy import HouseholdPolicy

                HouseholdPolicy(self.store).membership_changed(conn, track_ids)
        return {"added": changed, "revision": self.projection(user_id)["revision"]}

    def clear_requests(self, user_id):
        """Dismiss this person's finished history, retaining files and shared jobs."""
        cleared = 0
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT id,payload FROM queue WHERE status IN ('completed','downloaded','failed','cancelled') "
                "AND EXISTS (SELECT 1 FROM json_each(queue.payload,'$.requested_by') WHERE value=?)",
                (user_id,),
            ).fetchall()
            for key, raw in rows:
                payload = json.loads(raw)
                payload["requested_by"] = [
                    value
                    for value in payload.get("requested_by", [])
                    if value != user_id
                ]
                conn.execute(
                    "UPDATE queue SET payload=? WHERE id=?", (json.dumps(payload), key)
                )
                cleared += 1
        return {"cleared": cleared}

    @coordinated
    def cancel_request(self, user_id, request_id):
        """Withdraw one beneficiary; shared downloads continue for remaining people."""
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status,payload FROM queue WHERE id=?", (request_id,)
            ).fetchone()
            if not row or user_id not in json.loads(row[1]).get("requested_by", []):
                raise LookupError("Request not found")
            if row[0] not in {
                "waiting_for_sources",
                "searching",
                "downloading",
                "validating",
            }:
                raise ValueError("Only active requests can be cancelled")
            payload = json.loads(row[1])
            payload["requested_by"] = [
                identity for identity in payload["requested_by"] if identity != user_id
            ]
            remaining = bool(payload["requested_by"])
            conn.execute(
                "UPDATE queue SET payload=?,status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (json.dumps(payload), row[0] if remaining else "cancelled", request_id),
            )
        return {"cancelled": True, "continues_for_others": remaining}

    def retry_request(self, user_id, request_id):
        """Requeue one of this person's failed or cancelled requests."""
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            result = conn.execute(
                "UPDATE queue SET status='waiting_for_sources',updated_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND status IN ('failed','cancelled') AND EXISTS "
                "(SELECT 1 FROM json_each(queue.payload,'$.requested_by') WHERE value=?)",
                (request_id, user_id),
            )
            if result.rowcount:
                return {"retried": True}
            mine = conn.execute(
                "SELECT 1 FROM queue WHERE id=? AND EXISTS "
                "(SELECT 1 FROM json_each(queue.payload,'$.requested_by') WHERE value=?)",
                (request_id, user_id),
            ).fetchone()
        if not mine:
            raise LookupError("Request not found")
        raise ValueError("Only failed or cancelled requests can be retried")

    def queue(self, user_id, offset=0, limit=200):
        if offset < 0 or not 1 <= limit <= 200:
            raise ValueError("Invalid request page bounds")
        with self.store.connect() as conn:
            total = conn.execute(
                "SELECT count(*) FROM queue WHERE " + _MINE, (user_id,)
            ).fetchone()[0]
            in_progress = conn.execute(
                "SELECT count(*) FROM queue WHERE status IN "
                + _ACTIVE_SQL
                + " AND "
                + _MINE,
                (user_id,),
            ).fetchone()[0]
            records = conn.execute(
                "SELECT id,payload,status,created_at,updated_at FROM queue WHERE "
                + _MINE
                + " ORDER BY CASE WHEN status IN "
                + _ACTIVE_SQL
                + " THEN 0 ELSE 1 END,created_at DESC,id LIMIT ? OFFSET ?",
                (user_id, limit, offset),
            ).fetchall()
        return {
            "library_revision": self.store.library_revision,
            "downloads_enabled": self.store.download_settings()["enabled"],
            "total": total,
            "active": in_progress,
            "offset": offset,
            "next_offset": offset + limit if offset + limit < total else None,
            "items": [_present(_job(*record)) for record in records],
        }

    def request_groups(self, user_id, offset=0, limit=50):
        """This person's requests, one entry per album request (a single song
        stands alone), newest first with unfinished ones on top.

        What was requested comes from the request itself (`request_scope`,
        recorded when it was submitted); album metadata alone does not make a
        song an album request. Distinct releases stay distinct groups. A song
        asked for again after a failure appears once (the most advanced, then
        newest, of its jobs) unless the jobs name different recordings.
        """
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("Invalid request page bounds")
        # One read: counts, pages and groups all come from the same snapshot.
        with self.store.connect() as conn:
            records = conn.execute(
                "SELECT id,payload,status,created_at,updated_at FROM queue WHERE "
                + _MINE,
                (user_id,),
            ).fetchall()
        downloads_enabled = self.store.download_settings()["enabled"]
        jobs = [_job(*record) for record in records]
        result = [
            _group_summary(key, songs) for key, songs in _group(jobs, user_id).items()
        ]
        result.sort(key=lambda g: g["updated_at"], reverse=True)
        result.sort(key=lambda g: g["state"] not in {"active", "waiting"})
        return {
            "total": len(result),
            "active": sum(job["status"] in _ACTIVE for job in jobs),
            "offset": offset,
            "next_offset": offset + limit if offset + limit < len(result) else None,
            "downloads_enabled": downloads_enabled,
            "groups": result[offset : offset + limit],
        }

    @coordinated
    def import_playlists(self, user_id, raw):
        from .library import parse_apple

        source = parse_apple(raw)
        by_track = {t["id"]: track_dto(t) for t in source["tracks"]}
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for playlist in source.get("playlists", []):
                if len(playlist["track_ids"]) > MAX_PLAYLIST_ENTRIES:
                    raise ValueError(
                        f"“{playlist.get('name', 'A playlist')}” has more than "
                        "10,000 songs. Split it in Music, then import again."
                    )
                identity = identifier("user-playlist", user_id, playlist["id"])
                previous = conn.execute(
                    "SELECT payload FROM playlists WHERE id=? AND owner_id=?",
                    (identity, user_id),
                ).fetchone()
                revision = (
                    json.loads(previous[0]).get("revision", 0) + 1 if previous else 0
                )
                payload = {
                    **playlist,
                    "revision": revision,
                    "id": identity,
                    "tracks": [by_track[i] for i in playlist["track_ids"]],
                    "imported_at": source["imported_at"],
                }
                conn.execute(
                    "INSERT INTO playlists(id,payload,owner_id) VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                    (identity, json.dumps(payload), user_id),
                )
            conn.execute(
                "INSERT INTO documents VALUES (?,?) ON CONFLICT(name) DO UPDATE SET payload=excluded.payload",
                ("migration:" + user_id, json.dumps(source)),
            )
            bump(conn, user_id)
        return {
            "playlists": len(source.get("playlists", [])),
            "tracks": len(source["tracks"]),
        }

    def request(self, user_id, tracks):
        from .catalogue import cached_release_kind

        prepared = []
        for track in tracks:
            if not track.get("recording_kind") and track.get("musicbrainz_release_id"):
                # Older clients do not send the release type: the release
                # page this server served has it.
                kinds = cached_release_kind(self.store, track["musicbrainz_release_id"])
                if kinds:
                    track = {**track, "recording_kind": kinds}
            if not track.get("recording_kind"):
                track = {k: v for k, v in track.items() if k != "recording_kind"}
            result = self.store.index.match(track)
            prepared.append({**track, "status": result.status})
        return self.store.submit_catalogue(prepared, user_id=user_id)

    def history(self, user_id, events, session_id):
        """Record listens of the person's songs, household songs (they play for
        every member without being added: D-48) and their mix entries. A listen
        of anything else, or outside the time window, is skipped on its own:
        rejecting the batch made the web client resend it forever (#66)."""
        now = int(time.time())
        mix_tracks = {}
        for event in events:
            if event["track_id"].startswith("mix:"):
                try:
                    mix_tracks[event["track_id"]] = self.store.mixes.entry(
                        user_id, event["track_id"]
                    )
                except (LookupError, AttributeError):
                    pass
        household = saved_track_ids(
            self.store,
            [e["track_id"] for e in events if not e["track_id"].startswith("mix:")],
        )
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            added = 0
            for event in events:
                if (
                    event["track_id"] not in mix_tracks
                    and event["track_id"] not in household
                    and not conn.execute(
                        "SELECT 1 FROM library_members WHERE user_id=? AND track_id=?",
                        (user_id, event["track_id"]),
                    ).fetchone()
                ):
                    continue
                if (
                    event["played_at"] > now + 300
                    or event["played_at"] < now - 30 * 86400
                ):
                    continue
                added += conn.execute(
                    "INSERT OR IGNORE INTO play_events(user_id,track_id,played_at,seconds,device_session_id,event_id) VALUES (?,?,?,?,?,?)",
                    (
                        user_id,
                        event["track_id"],
                        event["played_at"],
                        event["seconds"],
                        session_id,
                        event["event_id"],
                    ),
                ).rowcount
                if event["track_id"] in mix_tracks:
                    from .mix_cache import song_key

                    conn.execute(
                        "INSERT OR IGNORE INTO mix_listens VALUES (?,?,?,?)",
                        (
                            user_id,
                            song_key(mix_tracks[event["track_id"]]),
                            event["played_at"],
                            event["event_id"],
                        ),
                    )
        return {"recorded": added}
