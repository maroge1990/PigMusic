"""Albums the household has on two editions, tidied to the fullest one.

Editions are albums by one album artist whose titles agree once edition
qualifiers go ("Hot Fuss" and "Hot Fuss (Deluxe Edition)"), sharing at least
half their songs. Singles and EPs, live albums, greatest hits and
compilations are albums of their own. The fullest edition is kept. Each song
on another edition that the kept album also has either goes to Trash (the
kept copy is as good), or replaces the kept copy (it is better: the file
moves into the kept album's folder, retagged to it, and the kept copy goes to
Trash). Songs only another edition has stay where they are, listed for
review. An album split across editions, each song on one of them, is joined
instead: an edition none of whose songs the kept album has, each filling a
free place on the kept album's track list, moves into the kept album's folder,
retagged to it with its own track numbers. Nothing changes until the owner applies an album from a plan whose
files are unchanged since it was made, and every step can be undone.
"""

import hashlib
import json
import os
import re
import time
import uuid
from collections import defaultdict
from pathlib import Path

from .catalogue import cached_release
from .library import normalise, year_order
from .lifecycle import coordinated
from .matching import compatible, primary_artist, strip_edition
from .organisation import ALBUM_FIELDS, TAG_SUFFIXES, file_name
from .quality import canonical_codec
from .storage import library_root

CATEGORIES = {"album", "soundtrack", "musical"}
LOSSLESS = {"flac", "alac", "wav", "aiff"}
# Share of an edition's songs the kept album must also have.
OVERLAP = 0.5
# A lossy copy replaces another only when clearly better, not by noise.
BITRATE_MARGIN = 1.15


def edition_title(album):
    """ "The Killers - Hot Fuss (Deluxe Edition)" is "hot fuss"; " - EP" and
    " - Single" stay, so a single is never an edition of its album."""
    text = album.get("title") or ""
    prefix = re.match(r"\s*(.+?)\s+-\s+(.+)$", text)
    if prefix and normalise(prefix[1]) == normalise(album.get("artist")):
        text = prefix[2]
    return normalise(strip_edition(text))


def quality(track):
    codec = canonical_codec(track.get("codec"))
    bitrate = track.get("bitrate") or 0
    if codec in LOSSLESS:
        return 2, bitrate
    return (1 if codec and codec != "unknown" else 0), bitrate


def better(new, old):
    """Whether `new` is clearly the better file; unknown quality never is,
    and never loses its file to a guess."""
    a, b = quality(new), quality(old)
    if not a[0] or not b[0]:
        return False
    if a[0] != b[0]:
        return a[0] > b[0]
    return a[0] == 1 and b[1] > 0 and a[1] >= b[1] * BITRATE_MARGIN


def as_good(kept, copy):
    """Whether the evidence shows `kept` is at least as good as `copy`, so
    `copy` can go to Trash. Unknown quality, or lossy copies without a
    bitrate to compare, are never evidence."""
    a, b = quality(kept), quality(copy)
    if not a[0] or not b[0]:
        return False
    if a[0] != b[0]:
        return a[0] > b[0]
    if a[0] == 2:
        return True
    return a[1] > 0 and b[1] > 0 and not better(copy, kept)


def album_key(title, artist):
    """Albums that are editions of each other share this key."""
    return primary_artist(artist or ""), edition_title(
        {"title": title, "artist": artist}
    )


def label(track):
    codec = canonical_codec(track.get("codec")).upper() or "Unknown"
    bitrate = track.get("bitrate")
    return codec if codec in {"FLAC", "ALAC"} or not bitrate else f"{codec} {bitrate}"


def position(track):
    """(disc, track) of a song with a track number, else None."""
    number = track.get("track_number")
    if not isinstance(number, int) or number < 1:
        return None
    return track.get("disc_number") or 1, number


def summary(track, album):
    return {
        "id": track["id"],
        "title": track["title"],
        "artist": track["artist"],
        "album": album["title"],
        "quality": label(track),
    }


class Tidy:
    def __init__(self, store, recovery, organisation):
        self.store = store
        self.recovery = recovery
        self.organisation = organisation

    def files(self):
        """Library-relative path, size and modification time of every song
        file as PigMusic last saw it."""
        with self.store.connect() as conn:
            acquired = {
                identity: json.loads(payload)
                for identity, payload in conn.execute("SELECT id,payload FROM acquired")
            }
        found = {}
        for album in self.store.library["albums"]:
            for track in album["tracks"]:
                raw = acquired.get(track["id"])
                staged = None
                if raw is not None:
                    path = raw.get("export_relative_path")
                    # Not yet published: in the download workspace.
                    staged = None if path else raw.get("relative_path")
                    raw = {**raw, "nas": bool(path)}
                else:
                    raw = self.store.snapshot_tracks.get(track["id"]) or track
                    path = raw.get("relative_path")
                    raw = {**raw, "nas": bool(path)}
                found[track["id"]] = {
                    "path": path,
                    "nas": raw["nas"],
                    "staged": staged,
                    "size": raw.get("size_bytes"),
                    "mtime": raw.get("mtime_ns"),
                    "explicit": bool(raw.get("explicit_edition")),
                }
        return found

    def plan(self):
        files = self.files()
        groups = defaultdict(list)
        for album in self.store.library["albums"]:
            if album.get("category") not in CATEGORIES or album.get("compilation"):
                continue
            tracks = [t for t in album["tracks"] if t.get("available") is not False]
            # An edition chosen from Other versions is never tidied away.
            if not tracks or any(files[t["id"]]["explicit"] for t in tracks):
                continue
            key = (primary_artist(album["artist"]), edition_title(album))
            groups[key].append((album, tracks))
        items = []
        for editions in groups.values():
            if len(editions) > 1:
                item = self.review(editions, files)
                if item:
                    items.append(item)
        items.sort(key=lambda i: (i["artist"].casefold(), i["keep"]["title"]))
        return {"albums": items}

    def review(self, editions, files):
        editions.sort(
            key=lambda e: (-len(e[1]), year_order(e[0].get("year")) or 9999, e[0]["id"])
        )
        keeper, kept = editions[0]

        def filed(track, album):
            return {**track, "album": album["title"]}

        def location(track):
            known = files[track["id"]]
            return ("nas", known["path"]) if known["nas"] else ("work", known["staged"])

        kept_paths = {location(k) for k in kept if location(k)[1]}
        matches, leftovers, others, separate = defaultdict(list), [], [], []
        for album, tracks in editions[1:]:
            found = {
                t["id"]: [
                    k for k in kept if compatible(filed(k, keeper), filed(t, album))
                ]
                for t in tracks
            }
            if sum(len(f) == 1 for f in found.values()) < OVERLAP * len(tracks):
                if not any(found.values()):
                    separate.append((album, tracks))  # Perhaps the other half.
                continue  # Another album that happens to share the title.
            others.append(album)
            for track in tracks:
                if location(track) in kept_paths:
                    continue  # The kept album's own file under another name.
                if len(found[track["id"]]) == 1:
                    matches[found[track["id"]][0]["id"]].append((album, track))
                else:
                    leftovers.append(summary(track, album))
        join, joined = self.joins(keeper, kept, separate, files)
        if not others and not joined:
            return None
        remove, replace = [], []
        for k in kept:
            copies = matches.get(k["id"])
            if not copies:
                continue
            best = max(copies, key=lambda c: quality(c[1]))
            movable = (
                files[best[1]["id"]]["nas"]
                and files[k["id"]]["nas"]
                and Path(files[best[1]["id"]]["path"]).suffix.lower() in TAG_SUFFIXES
            )
            keeping = k
            if better(best[1], k) and movable:
                replace.append(
                    {**summary(best[1], best[0]), "replaces": summary(k, keeper)}
                )
                copies = [c for c in copies if c is not best]
                keeping = best[1]
            elif better(best[1], k):
                # A better copy that cannot be retagged into place stays put.
                leftovers += [
                    summary(t, a)
                    | (
                        {"reason": "not in the music folder yet"}
                        if files[t["id"]]["staged"]
                        else {}
                    )
                    for a, t in copies
                ]
                continue
            # Only a copy the kept file is shown to be as good as goes to
            # Trash; one that might be better stays for review. A download
            # not yet published (in the download workspace) can go to Trash
            # like any copy, but never stands in for a music-folder copy.
            for album, track in copies:
                if not files[track["id"]]["nas"] and not files[track["id"]]["staged"]:
                    leftovers.append(summary(track, album))
                elif files[track["id"]]["nas"] and not files[keeping["id"]]["nas"]:
                    leftovers.append(
                        summary(track, album)
                        | {"reason": "the kept copy is not in the music folder yet"}
                    )
                elif as_good(keeping, track):
                    remove.append({**summary(track, album), "kept": summary(k, keeper)})
                else:
                    leftovers.append(summary(track, album))
        involved = [t["id"] for t in kept] + [
            t["id"]
            for album, tracks in editions[1:]
            if album in others or album in joined
            for t in tracks
        ]
        fingerprint = hashlib.sha256(
            json.dumps(sorted([i, files[i]] for i in involved), sort_keys=True).encode()
        ).hexdigest()
        return {
            "id": keeper["id"],
            "artist": keeper["artist"],
            "keep": {
                "album_id": keeper["id"],
                "title": keeper["title"],
                "year": keeper.get("year") or None,
                "songs": len(kept),
            },
            "editions": [
                {
                    "album_id": a["id"],
                    "title": a["title"],
                    "year": a.get("year") or None,
                    "songs": len(
                        [t for t in a["tracks"] if t.get("available") is not False]
                    ),
                }
                for a in others + joined
            ],
            "remove": remove,
            "replace": replace,
            "join": join,
            "leftovers": leftovers,
            "fingerprint": fingerprint,
        }

    def folder(self, kept, files):
        """The kept album's one music folder, or None when it has none or
        several."""
        folders = {
            Path(files[k["id"]]["path"]).parent for k in kept if files[k["id"]]["nas"]
        }
        return folders.pop() if len(folders) == 1 else None

    def joins(self, keeper, kept, separate, files):
        """The editions whose songs fill free places on the kept album's track
        list: every song with a track number, on a disc and number the kept
        album has free and its track list covers (the catalogue's tracklist
        for its release when this server has read it, else the highest
        number on that disc the kept album has). One song that does not fit
        leaves its whole edition alone: never a guess."""
        if not separate or self.folder(kept, files) is None:
            return [], []
        release = keeper.get("musicbrainz_release_id")
        release = cached_release(self.store, release) if release else None
        listed = (
            {
                (medium.get("position", 1), track.get("position", 0))
                for medium in release.get("media") or []
                for track in medium.get("tracks") or []
            }
            if release
            else None
        )
        last = defaultdict(int)
        for k in kept:
            if position(k):
                disc, number = position(k)
                last[disc] = max(last[disc], number)
        taken = {position(k) for k in kept}
        join, joined = [], []
        for album, tracks in separate:
            rows, places = [], set()
            for track in tracks:
                place = position(track)
                known = files[track["id"]]
                if (
                    not place
                    or place in taken
                    or place in places
                    or not (
                        place in listed
                        if listed is not None
                        else place[1] <= last[place[0]]
                    )
                    or not known["nas"]
                    or Path(known["path"]).suffix.lower() not in TAG_SUFFIXES
                ):
                    break
                places.add(place)
                rows.append(
                    summary(track, album)
                    | {
                        "album_id": album["id"],
                        "to": {"track_number": place[1], "disc_number": place[0]},
                    }
                )
            else:
                taken |= places
                join += rows
                joined.append(album)
        join.sort(key=lambda r: (r["to"]["disc_number"], r["to"]["track_number"]))
        return join, joined

    def unchanged(self, files, ids):
        """Every file still as PigMusic last saw it (else rescan first)."""
        root = Path(os.environ.get("PIG_MUSIC_LIBRARY", "/library"))
        for identity in ids:
            known = files[identity]
            path = (
                root / known["path"]
                if known["nas"]
                else library_root(self.store) / known["staged"]
            )
            try:
                status = path.stat()
            except FileNotFoundError:
                return False
            if known["size"] is not None and status.st_size != known["size"]:
                return False
            if known["mtime"] is not None and status.st_mtime_ns != known["mtime"]:
                return False
        return True

    @coordinated
    def apply(self, album_id, fingerprint):
        """Tidy one album's editions as planned; refused if anything changed.
        Every change is journaled before it happens, so Undo (or a restart)
        finds it however the tidy-up stops."""
        item = next((i for i in self.plan()["albums"] if i["id"] == album_id), None)
        if not item or item["fingerprint"] != fingerprint:
            raise ValueError("These albums changed since the review.")
        files = self.files()
        # The files that move, and the kept copies that justify a removal: a
        # kept copy changed or gone since the scan must not stand in for a
        # good one going to Trash.
        checked = (
            [r["id"] for r in item["remove"]]
            + [r["kept"]["id"] for r in item["remove"]]
            + [i for r in item["replace"] for i in (r["id"], r["replaces"]["id"])]
            + [r["id"] for r in item["join"]]
        )
        if not self.unchanged(
            files, [i for i in checked if files[i]["nas"] or files[i]["staged"]]
        ):
            raise ValueError("Files changed since the last scan.")
        keeper = self.store.albums_by_id[album_id]
        tracks = {t["id"]: t for t in keeper["tracks"]}
        record = {
            "id": str(uuid.uuid4()),
            "album_id": album_id,
            "artist": item["artist"],
            "title": item["keep"]["title"],
            "time": int(time.time()),
            "status": "applying",
            "memberships": [],
            "removals": [],
            "moves": [],
            # Steps are recorded before they run (earlier records lack this).
            "journal": 2,
        }
        self.save(record)
        try:
            for row in item["replace"]:
                old = tracks[row["replaces"]["id"]]
                # The better file's listeners and the kept copy's share it.
                self.share(record, row["replaces"]["id"], row["id"])
                self.trash(record, old["id"])
                self.place(
                    record,
                    keeper,
                    row["id"],
                    {
                        "track_number": old.get("track_number"),
                        "disc_number": old.get("disc_number", 1),
                    },
                    Path(files[old["id"]]["path"]).parent,
                    files,
                )
            for row in item["remove"]:
                kept = row["kept"]["id"]
                # A kept copy replaced above now lives on in the better file.
                kept = next(
                    (r["id"] for r in item["replace"] if r["replaces"]["id"] == kept),
                    kept,
                )
                self.share(record, row["id"], kept)
                self.trash(record, row["id"])
            folder = self.folder(
                [t for t in keeper["tracks"] if t.get("available") is not False], files
            )
            for row in item["join"]:
                # The song keeps its own identity, so its listeners keep it.
                self.place(record, keeper, row["id"], row["to"], folder, files)
            record["status"] = "applied"
        except (ValueError, OSError) as error:
            record.update(status="stopped", error=str(error) or type(error).__name__)
            raise
        finally:
            try:
                self.save(record)
            finally:
                self.store.refresh_library()
        return record

    def place(self, record, keeper, track_id, number, folder, files):
        """Move a song into the kept album's `folder`, retagged to the kept
        album at `number` (track and disc), journaled before it happens."""
        placement = dict(
            album=keeper["title"],
            album_artist=keeper["artist"],
            year=keeper.get("year") or None,
            musicbrainz_release_id=keeper.get("musicbrainz_release_id"),
            **number,
        )
        placement = {k: placement.get(k) for k in ALBUM_FIELDS}
        source = files[track_id]["path"]
        moved = self.store.index.by_id[track_id]
        destination = (
            folder / file_name({**moved, **placement}, Path(source).suffix.lower())
        ).as_posix()
        operation = str(uuid.uuid4())
        record["moves"].append(operation)
        self.save(record)
        self.organisation.move(
            track_id, source, destination, True, placement, operation
        )

    def share(self, record, track_id, keeper):
        """Give the kept copy to everyone who has `track_id`, journaled in the
        same transaction."""
        from .user_library import bump

        committed = list(record["memberships"])
        try:
            with self.store.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                for (user_id,) in conn.execute(
                    "SELECT user_id FROM library_members WHERE track_id=?", (track_id,)
                ).fetchall():
                    if conn.execute(
                        "INSERT OR IGNORE INTO library_members VALUES (?,?,?,'household-add')",
                        (user_id, keeper, int(time.time())),
                    ).rowcount:
                        record["memberships"].append([user_id, keeper])
                        bump(conn, user_id)
                conn.execute(
                    "UPDATE documents SET payload=? WHERE name=?",
                    (json.dumps(record), "tidy:" + record["id"]),
                )
        except BaseException:
            record["memberships"] = committed  # Rolled back with the rows.
            raise

    def trash(self, record, track_id):
        removal = str(uuid.uuid4())
        record["removals"].append(removal)
        self.save(record)
        self.recovery.remove(track_id, exclude=False, refresh=False, identity=removal)

    @coordinated
    def undo(self, identity):
        """Put an album's editions back as they were before the tidy-up,
        settling any step a failure or restart interrupted first."""
        from .user_library import bump

        record = self.store.read_document("tidy:" + identity)
        # "applying" is a tidy-up a restart interrupted.
        if record.get("status") not in {"applying", "applied", "stopped"}:
            raise ValueError("Tidy-up not found.")
        try:
            for operation in reversed(record["moves"]):
                if self.organisation.settle(operation) in {"completed", "undoing"}:
                    self.organisation.undo(operation)
            for removal in reversed(record["removals"]):
                if self.recovery.settle(removal) in {"removed", "removing"}:
                    self.recovery.restore(removal)
        except (ValueError, OSError) as error:
            record["error"] = "Undo stopped: " + (str(error) or type(error).__name__)
            self.save(record)
            self.store.refresh_library()
            raise
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for user_id, track_id in record["memberships"]:
                if conn.execute(
                    "DELETE FROM library_members WHERE user_id=? AND track_id=? AND source='household-add'",
                    (user_id, track_id),
                ).rowcount:
                    bump(conn, user_id)
        remaining = self.pending(record)
        if remaining:
            # What this tidy-up recorded is back, but not necessarily all of it.
            record.update(
                status="stopped",
                error=f"Put back what this tidy-up recorded; {len(remaining)} other change{'s' if len(remaining) > 1 else ''} may belong to it. Include each that does, or mark it not part of this tidy-up, then Undo again.",
            )
        else:
            record["status"] = "undone"
            record.pop("error", None)
        self.save(record)
        self.store.refresh_library()
        return record

    @coordinated
    def recover(self):
        """At startup, after organisation and removal recovery: a tidy-up a
        restart interrupted is stopped, and steps an earlier build ran
        without recording them are reconciled with its tidy-ups.

        A move is adopted when the evidence names one tidy-up: only tidy-ups
        retag a move's album placement, and the earlier build moved a file
        straight after recording the removal of the copy it replaced (same
        folder, track and disc), so a stopped attempt whose last removal is
        that copy owns it. A removal records nothing that tells a tidy-up's
        from a duplicate's, so it is never adopted on a guess. Anything left
        is listed as unresolved on each tidy-up of that album it could
        belong to, for the owner to include or set aside, and an undone
        tidy-up with any is reopened: Undo never reports it complete."""
        records = self.records()
        moves = {m for r in records for m in r["moves"]}
        removals = {m for r in records for m in r["removals"]}
        older = [
            r
            for r in records
            if r["status"] in {"applying", "stopped", "undone"} and "journal" not in r
        ]
        changed = set()

        def flag(record, kind, identity):
            if identity in record.get("dismissed", []):
                return
            known = record.setdefault("unresolved", [])
            if not any(u["id"] == identity for u in known):
                known.append({"kind": kind, "id": identity})
                changed.add(record["id"])

        for operation in self.organisation.history():
            if (
                "placed_from" not in operation
                or operation["id"] in moves
                or operation["status"] in {"cancelled", "undone"}
            ):
                continue
            owners = [
                r
                for r in older
                if r["title"] == operation["track"].get("album")
                and r["artist"] == operation["track"].get("album_artist")
            ]
            proven = [r for r in owners if self.preceded(r, operation)]
            if len(proven) == 1:
                owner = proven[0]
                owner["moves"].append(operation["id"])
                owner.setdefault(
                    "error", "Stopped part-way; a moved file was recovered for Undo."
                )
                if owner["status"] == "undone":
                    owner.update(
                        status="stopped",
                        error="An earlier Undo missed a moved file; Undo again.",
                    )
                changed.add(owner["id"])
            else:
                for owner in owners:
                    flag(owner, "move", operation["id"])
        excluded = set()
        with self.store.connect() as conn:
            for (payload,) in conn.execute(
                "SELECT payload FROM documents WHERE name >= 'excluded:' AND name < 'excluded;'"
            ):
                excluded.add(json.loads(payload).get("removal_id"))
        for removal in self.recovery.list():
            # Tidy-ups never exclude a song from requests.
            if (
                removal["status"] not in {"removed", "removing"}
                or removal["id"] in removals
                or removal["id"] in excluded
            ):
                continue
            track = removal.get("track") or {}
            key = album_key(track.get("album"), track.get("album_artist"))
            for owner in older:
                if album_key(owner["title"], owner["artist"]) == key:
                    flag(owner, "removal", removal["id"])
        for record in older:
            if record["status"] == "applying":
                record.update(
                    status="stopped",
                    error=record.get("error") or "Interrupted by a restart.",
                )
                changed.add(record["id"])
            if record["status"] == "undone" and self.pending(record, records):
                record.update(
                    status="stopped",
                    error="An earlier Undo may have missed changes; review them, then Undo again.",
                )
                changed.add(record["id"])
            if record["id"] in changed:
                self.save(record)

    def preceded(self, record, operation):
        """Whether `record`'s last recorded step removed the copy this move
        replaced: the earlier build's apply ran exactly that pair."""
        if not record["removals"]:
            return False
        removal = self.store.read_document("removed:" + record["removals"][-1])
        track, placed = removal.get("track") or {}, operation["track"]
        return (
            bool(removal)
            and Path(removal["original"]).parent
            == Path(operation["destination"]).parent
            and track.get("track_number") == placed.get("track_number")
            and (track.get("disc_number") or 1) == (placed.get("disc_number") or 1)
        )

    def pending(self, record, records=None):
        """Unresolved steps still in effect and owned by no tidy-up."""
        records = self.records() if records is None else records
        moves = {m for r in records for m in r["moves"]}
        removals = {m for r in records for m in r["removals"]}
        found = []
        for item in record.get("unresolved", []):
            if item["kind"] == "move":
                step = self.store.read_document("organisation:" + item["id"])
                if item["id"] in moves or step.get("status") in {
                    None,
                    "cancelled",
                    "undone",
                }:
                    continue
                track = step["track"]
                where = (step.get("placed_from") or {}).get("album")
                state = f"moved from {where} into {track.get('album')}"
            else:
                step = self.store.read_document("removed:" + item["id"])
                if item["id"] in removals or step.get("status") not in {
                    "removed",
                    "removing",
                }:
                    continue
                track = step["track"]
                state = (
                    "in Removed music"
                    if step["status"] == "removed"
                    else "removal stopped part-way"
                )
            found.append(
                {
                    **item,
                    "title": track.get("title"),
                    "album": track.get("album"),
                    "state": state,
                }
            )
        return found

    @coordinated
    def resolve(self, identity, operation, include):
        """The owner's answer for an unresolved step: part of this tidy-up
        (Undo reverses it) or not (it is left as it is)."""
        record = self.store.read_document("tidy:" + identity)
        item = next(
            (u for u in self.pending(record) if u["id"] == operation),
            None,
        )
        if record.get("status") != "stopped" or not item:
            raise ValueError("That change is not waiting for review.")
        record["unresolved"] = [u for u in record["unresolved"] if u["id"] != operation]
        if include:
            record["moves" if item["kind"] == "move" else "removals"].append(operation)
        else:
            record.setdefault("dismissed", []).append(operation)
        self.save(record)
        return record

    def records(self):
        with self.store.connect() as conn:
            return [
                json.loads(v)
                for (v,) in conn.execute(
                    "SELECT payload FROM documents WHERE name >= 'tidy:' AND name < 'tidy;'"
                )
            ]

    def history(self):
        records = self.records()
        return sorted(
            (
                {**r, "unresolved": self.pending(r, records)}
                for r in records
                if r["status"] in {"applying", "applied", "stopped"}
            ),
            key=lambda r: -r["time"],
        )

    def save(self, record):
        self.recovery.save("tidy:" + record["id"], record)
