"""Review-first organisation with preserved originals and restart recovery."""

from .lifecycle import coordinated

import json, logging, os, shutil, threading, uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from mutagen.flac import FLAC
from mutagen.id3 import ID3, ID3NoHeaderError
from mutagen.mp4 import MP4
from .library import load_json
from .media import saved_track, track_file
from .exporter import Exporter, contained
from .downloader import Downloader, safe_name
from .scanner import inspect_file


# Album placement a tidy-up retags onto a moved file.
ALBUM_FIELDS = (
    "album",
    "album_artist",
    "year",
    "track_number",
    "disc_number",
    "musicbrainz_release_id",
)
TAG_SUFFIXES = {".mp3", ".flac", ".m4a"}

log = logging.getLogger(__name__)

# The fields `Downloader.tag` writes, with where each format keeps them.
TAG_KEYS = {
    "title": ("TIT2", "TITLE", "\xa9nam"),
    "artist": ("TPE1", "ARTIST", "\xa9ART"),
    "album": ("TALB", "ALBUM", "\xa9alb"),
    "album_artist": ("TPE2", "ALBUMARTIST", "aART"),
    "track_number": ("TRCK", "TRACKNUMBER", "trkn"),
    "disc_number": ("TPOS", "DISCNUMBER", "disk"),
    "year": ("TDRC", "DATE", "\xa9day"),
    "musicbrainz_release_id": (
        "TXXX:MusicBrainz Album Id",
        "MUSICBRAINZ_ALBUMID",
        "----:com.apple.iTunes:MusicBrainz Album Id",
    ),
    "musicbrainz_recording_id": (
        "TXXX:MusicBrainz Track Id",
        "MUSICBRAINZ_TRACKID",
        "----:com.apple.iTunes:MusicBrainz Track Id",
    ),
}
# Fields Apply writes only when set (a year of 0 is no year).
OPTIONAL_TAGS = {"year", "musicbrainz_release_id", "musicbrainz_recording_id"}
# Review reads this many files at once: each read is a few NAS round trips.
TAG_READERS = 8


def tag_value(field, value):
    """One field as Apply would leave it, so a just-tagged file compares equal:
    track "3/12" and (3, 0) are 3, a date is its year, IDs ignore case."""
    while isinstance(value, (list, tuple)) and field in {"track_number", "disc_number"}:
        value = value[0] if value else None
    if isinstance(value, list):
        if len(value) != 1:
            return tuple(tag_value(field, v) for v in value) or None
        value = value[0]
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    if value is None:
        return None
    text = str(value).strip()
    if field in {"track_number", "disc_number"}:
        text = text.split("/")[0].strip()
        return int(text) if text.isdigit() else text
    if field == "year":
        return text[:4] if text[:4].isdigit() else text
    if field.startswith("musicbrainz"):
        return text.lower()
    return text


def file_tags(path):
    """The fields `Downloader.tag` writes, as `path` holds them now."""
    suffix = path.suffix.lower()
    if suffix == ".flac":
        audio = FLAC(path).tags or {}
        index = 1
    elif suffix == ".m4a":
        audio = MP4(path).tags or {}
        index = 2
    else:
        try:
            audio = ID3(path)
        except ID3NoHeaderError:
            audio = {}
        index = 0
    found = {}
    for field, keys in TAG_KEYS.items():
        key = keys[index]
        if key not in audio:
            found[field] = None
            continue
        value = audio[key]
        if index == 0:
            value = [str(v) for v in value.text]
        found[field] = tag_value(field, value)
    return found


def tags_differ(current, track):
    """Whether tagging `track` would change any field the file holds."""
    for field in TAG_KEYS:
        wanted = track.get(field)
        if wanted is None or wanted == "" or (field in OPTIONAL_TAGS and not wanted):
            continue  # Apply leaves such a field as the file has it.
        if tag_value(field, wanted) != current.get(field):
            return True
    return False


def file_name(raw, suffix):
    """ "Artist - Album - 01 - Title.mp3", within 200 bytes before the suffix."""
    number = (
        f"{raw.get('disc_number',1):02}-{raw.get('track_number',0):02}"
        if raw.get("disc_number", 1) > 1
        else f"{raw.get('track_number',0):02}"
    )
    label = " - ".join(
        [
            safe_name(raw["artist"]),
            safe_name(raw["album"]),
            number,
            safe_name(raw["title"]),
        ]
    )
    return label.encode("utf-8")[:200].decode("utf-8", "ignore") + suffix


class Organisation:
    def __init__(self, store, recovery):
        self.store = store
        self.recovery = recovery
        self.job_lock = threading.Lock()
        self.job = None
        self.stopping = threading.Event()
        self.worker = None
        self.tag_cache = {}
        self.tag_cache_lock = threading.Lock()

    def current_tags(self, root, relative):
        """`file_tags` of the library file at `relative`, kept by size and
        modification time so a second Review costs one stat per file (the
        containment check, several NAS round trips, runs on a first read
        only). None when unreadable."""
        try:
            stat = (root / relative).stat()
        except OSError:
            return None
        key = (str(root), relative, stat.st_size, stat.st_mtime_ns)
        with self.tag_cache_lock:
            cached = self.tag_cache.get(relative)
        if cached and cached[0] == key:
            return cached[1]
        try:
            found = file_tags(contained(root, relative))
        except Exception:
            log.info("Could not read tags of %s", relative, exc_info=True)
            return None
        with self.tag_cache_lock:
            self.tag_cache[relative] = (key, found)
        return found

    def plan(self, tags=False, only=None):
        """The review: every song's rename, and with `tags` whether its file's
        tags differ from the library metadata (`tags_differ`, read for the
        songs in `only` when given, else for all)."""
        rows = []
        tracks = {}
        root = Path(os.environ.get("PIG_MUSIC_LIBRARY", "/library"))
        snapshot = load_json(self.store.directory / "library-snapshot.json")
        originals = {t["id"]: t for t in snapshot.get("tracks", [])}
        with self.store.connect() as c:
            acquired = {
                identity: json.loads(payload)
                for identity, payload in c.execute("SELECT id,payload FROM acquired")
            }
        for album in self.store.library["albums"]:
            for track in album["tracks"]:
                raw = acquired.get(track["id"]) or originals.get(track["id"])
                if not raw or raw.get("available") is False:
                    continue
                if track["id"] in acquired and not raw.get("export_relative_path"):
                    continue
                old = raw.get("export_relative_path") or raw.get("relative_path")
                if not old:
                    continue
                suffix = Path(old).suffix.lower()
                target = (Path(old).parent / file_name(raw, suffix)).as_posix()
                tracks[raw["id"]] = raw
                rows.append(
                    {
                        "id": raw["id"],
                        "title": raw["title"],
                        "artist": raw["artist"],
                        "from": old,
                        "to": target,
                        "rename": old != target,
                        "tag_supported": suffix in TAG_SUFFIXES,
                        "conflict": old != target and contained(root, target).exists(),
                        "tags_differ": None,
                    }
                )
        targets = {}
        for row in rows:
            targets.setdefault(row["to"], []).append(row)
        for matches in targets.values():
            if len(matches) > 1:
                for row in matches:
                    row["conflict"] = True
        if tags:
            wanted = [
                r
                for r in rows
                if r["tag_supported"] and (only is None or r["id"] in only)
            ]

            def read(row):
                current = self.current_tags(root, row["from"])
                # An unreadable file is listed, so it is never silently skipped.
                return current is None or tags_differ(current, tracks[row["id"]])

            with ThreadPoolExecutor(TAG_READERS) as pool:
                for row, differ in zip(wanted, pool.map(read, wanted)):
                    row["tags_differ"] = differ
            for row in rows:
                if row["tags_differ"] is None:
                    row["tags_differ"] = False
        return {
            "items": rows,
            "note": "Existing album folders and release identities are retained. Tags use the current library metadata. Originals are preserved for recovery.",
        }

    def check(self, ids, tags=False):
        """Refuse the whole selection before anything changes when any song
        needs review; returns the IDs in album order."""
        rows = {r["id"]: r for r in self.plan()["items"]}
        for identity in ids:
            row = rows.get(identity)
            if not row or row["conflict"]:
                raise ValueError("A selected destination needs review.")
            if tags and not row["tag_supported"]:
                raise ValueError("This audio format needs a separate tag review.")
        albums = {}
        for album in self.store.library["albums"]:
            for track in album["tracks"]:
                albums.setdefault(track["id"], album["id"])
        chunks = {}
        for identity in dict.fromkeys(ids):
            chunks.setdefault(albums.get(identity, identity), []).append(identity)
        return list(chunks.values())

    def start(self, ids, tags, gate):
        """Apply a reviewed selection in the background, one album at a time
        (#93). Each album takes the lifecycle lock on its own, so downloads and
        playback carry on between albums. `gate(stopped)` waits until the
        library is free (no scan or download) and returns the release for the
        lock it took, or None once a stop was asked for. The first problem
        stops the job; files already done stay done and can be undone."""
        chunks = self.check(ids, tags)
        with self.job_lock:
            if self.job and self.job["status"] in {"running", "stopping"}:
                raise RuntimeError("Organisation is already running.")
            self.stopping.clear()
            self.job = {
                "status": "running",
                "total": sum(len(c) for c in chunks),
                "done": 0,
                "message": None,
            }
            job = dict(self.job)
        self.worker = threading.Thread(
            target=self.run, args=(chunks, tags, gate), daemon=True
        )
        self.worker.start()
        return job

    def run(self, chunks, tags, gate):
        status, message = "completed", None
        try:
            for chunk in chunks:
                release = None if self.stopping.is_set() else gate(self.stopping.is_set)
                if release is None:
                    status, message = (
                        "stopped",
                        "Stopped; songs already done can be undone.",
                    )
                    break
                try:
                    self.apply(chunk, tags)
                finally:
                    release()
                with self.job_lock:
                    self.job["done"] += len(chunk)
        except (ValueError, OSError) as error:
            log.warning("Organisation stopped: %s", error)
            status = "stopped"
            message = "Stopped at a song that needs review; its original is retained. Songs already done can be undone."
        except Exception:
            log.exception("Organisation failed")
            status = "stopped"
            message = "Organisation stopped unexpectedly; originals are retained."
        with self.job_lock:
            self.job.update(status=status, message=message)

    def stop(self):
        with self.job_lock:
            if self.job and self.job["status"] == "running":
                self.job["status"] = "stopping"
                self.stopping.set()
            return self.status()

    def status(self):
        return dict(self.job) if self.job else {"status": "idle"}

    @coordinated
    def apply(self, ids, tags=False):
        rows = {r["id"]: r for r in self.plan(tags, set(ids))["items"]}
        results = []
        for identity in ids:
            row = rows.get(identity)
            if not row or row["conflict"]:
                raise ValueError("A selected destination needs review.")
            if tags and not row["tag_supported"]:
                raise ValueError("This audio format needs a separate tag review.")
            if not row["rename"] and not (tags and row["tags_differ"]):
                continue  # Nothing to change (#119): no rewrite, no undo copy.
            results.append(self.move(identity, row["from"], row["to"], tags))
        self.store.refresh_library()
        return {"applied": len(results), "operations": results}

    def move(self, identity, origin, to, tags, placement=None, operation=None):
        """Move one file within the library (optionally retagged, with
        `placement` replacing its album fields, absent ones cleared) and keep
        the original for undo. A caller journaling the move passes the
        `operation` ID it recorded first. The caller refreshes the library."""
        with self.recovery.lock:
            track, root = saved_track(self.store, identity)
            self.recovery.check_mount(root)
            source = track_file(self.store, identity)
            destination = contained(root, to)
            operation = operation or str(uuid.uuid4())
            backup = contained(
                root,
                ".pig-music-trash/organisation/" + operation + "/" + origin,
            )
            backup.parent.mkdir(parents=True, exist_ok=True)
            contained(root, ".pig-music-trash/.plexignore").write_text(
                "*\n", encoding="utf-8"
            )
            before = self.recovery.digest(source)
            stage = source.parent / (".pig-music-organise-" + operation + source.suffix)
            record = {
                "id": operation,
                "track_id": identity,
                "track": {**track, **(placement or {})},
                "root": str(root.resolve()),
                "source": origin,
                "destination": to,
                "backup": backup.relative_to(root).as_posix(),
                "stage": stage.relative_to(root).as_posix(),
                "before": before,
                "status": "preparing",
            }
            if placement:
                record["placed_from"] = {k: track.get(k) for k in ALBUM_FIELDS}
            self.recovery.save("organisation:" + operation, record)
            os.link(source, backup)
            shutil.copyfile(source, stage)
            if tags:
                Downloader(self.store).tag(
                    stage,
                    record["track"],
                    "existing-library",
                    clear=ALBUM_FIELDS if placement else (),
                )
            checked, _ = inspect_file(stage, to)
            if abs(checked.get("duration", 0) - track.get("duration", 0)) > 1:
                raise ValueError("Audio duration changed; original retained.")
            record.update(after=self.recovery.digest(stage), status="publishing")
            self.recovery.save("organisation:" + operation, record)
            self.finish(record, root)
            return operation

    def finish(self, record, root):
        source = contained(root, record["source"])
        target = contained(root, record["destination"])
        stage = contained(root, record["stage"])
        backup = contained(root, record["backup"])
        if not backup.is_file() or self.recovery.digest(backup) != record["before"]:
            raise ValueError("Original backup changed; publication stopped.")
        if record["status"] == "preparing":
            if stage.exists():
                stage.unlink()
            record["status"] = "cancelled"
            self.recovery.save("organisation:" + record["id"], record)
            return
        if record["source"] == record["destination"]:
            if target.exists() and self.recovery.digest(target) == record["before"]:
                if (
                    not stage.is_file()
                    or self.recovery.digest(stage) != record["after"]
                ):
                    raise ValueError("Prepared file unavailable.")
                os.replace(stage, target)
            elif (
                not target.is_file() or self.recovery.digest(target) != record["after"]
            ):
                raise ValueError("Destination changed.")
        else:
            if target.exists() and self.recovery.digest(target) != record["after"]:
                raise ValueError("Destination collision; original retained.")
            if not target.exists():
                if (
                    not stage.is_file()
                    or self.recovery.digest(stage) != record["after"]
                ):
                    raise ValueError("Prepared file unavailable.")
                os.link(stage, target)
            if source.exists():
                if self.recovery.digest(source) != record["before"]:
                    raise ValueError("Original changed; both files retained.")
                source.unlink()
            if stage.exists():
                stage.unlink()
        checked, _ = inspect_file(target, record["destination"])
        updated = {
            **record["track"],
            **checked,
            "id": record["track_id"],
            "sha256": record["after"],
        }
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT payload FROM acquired WHERE id=?", (record["track_id"],)
            ).fetchone()
            if row:
                acquired = json.loads(row[0])
                if "placed_from" in record:
                    acquired.update(
                        {k: record["track"].get(k) for k in record["placed_from"]}
                    )
                acquired.update(
                    export_relative_path=record["destination"],
                    sha256=record["after"],
                    size_bytes=target.stat().st_size,
                    mtime_ns=target.stat().st_mtime_ns,
                )
                conn.execute(
                    "UPDATE acquired SET payload=? WHERE id=?",
                    (json.dumps(acquired), record["track_id"]),
                )
        snapshot = load_json(self.store.directory / "library-snapshot.json")
        snapshot["tracks"] = [
            updated if t["id"] == record["track_id"] else t
            for t in snapshot.get("tracks", [])
            # A scan's entry for a moved download's old path is gone with it.
            if not row
            or t["id"] == record["track_id"]
            or t.get("relative_path") != record["source"]
        ]
        temporary = self.store.directory / "organisation-snapshot.tmp"
        temporary.write_text(json.dumps(snapshot), encoding="utf-8")
        temporary.replace(self.store.directory / "library-snapshot.json")
        record["status"] = "completed"
        self.recovery.save("organisation:" + record["id"], record)

    def settle(self, identity):
        """Bring an interrupted operation to rest (an unpublished one is
        cancelled, a published one completed) and return its status; None
        when it never started."""
        with self.recovery.lock:
            record = self.store.read_document("organisation:" + identity)
            if not record:
                return None
            if record["status"] == "preparing":
                _, root = saved_track(self.store, record["track_id"])
                stage = contained(root, record["stage"])
                if stage.exists():
                    stage.unlink()
                record["status"] = "cancelled"
                self.recovery.save("organisation:" + identity, record)
            elif record["status"] == "publishing":
                _, root = saved_track(self.store, record["track_id"])
                self.recovery.check_mount(root)
                if str(root.resolve()) != record["root"]:
                    raise ValueError("Library mount changed.")
                self.finish(record, root)
            return record["status"]

    @coordinated
    def recover(self):
        with self.store.connect() as c:
            records = [
                json.loads(v)
                for (v,) in c.execute(
                    "SELECT payload FROM documents WHERE name >= 'organisation:' AND name < 'organisation;'"
                )
            ]
        for record in records:
            if record["status"] == "undoing":
                try:
                    self.undo(record["id"])
                except (OSError, ValueError):
                    pass
                continue
            if record["status"] not in {"preparing", "publishing"}:
                continue
            try:
                _, root = saved_track(self.store, record["track_id"])
                self.recovery.check_mount(root)
                if str(root.resolve()) != record["root"]:
                    continue
                with self.recovery.lock:
                    self.finish(record, root)
            except (OSError, ValueError):
                pass
        self.store.refresh_library()

    def undoable(self, limit=None, offset=0):
        """Operations that can be undone, newest first."""
        with self.store.connect() as c:
            return [
                json.loads(v)
                for (v,) in c.execute(
                    "SELECT payload FROM documents WHERE name >= 'organisation:' AND name < 'organisation;' "
                    "AND json_extract(payload,'$.status') IN ('completed','undoing') "
                    "ORDER BY rowid DESC LIMIT ? OFFSET ?",
                    (-1 if limit is None else limit, offset),
                )
            ]

    def history(self):
        with self.store.connect() as c:
            return [
                json.loads(v)
                for (v,) in c.execute(
                    "SELECT payload FROM documents WHERE name >= 'organisation:' AND name < 'organisation;'"
                )
            ]

    @coordinated
    def undo(self, identity):
        with self.recovery.lock:
            record = self.store.read_document("organisation:" + identity)
            if record.get("status") not in {"completed", "undoing"}:
                raise ValueError("Organisation record not found.")
            _, root = saved_track(self.store, record["track_id"])
            self.recovery.check_mount(root)
            if str(root.resolve()) != record["root"]:
                raise ValueError("Library mount changed.")
            source = contained(root, record["source"])
            target = contained(root, record["destination"])
            backup = contained(root, record["backup"])
            if not backup.is_file() or self.recovery.digest(backup) != record["before"]:
                raise ValueError("Original backup unavailable.")
            if target.exists() and self.recovery.digest(target) not in {
                record["after"],
                record["before"],
            }:
                raise ValueError("Organised file changed; undo stopped.")
            if (
                source.exists()
                and record["source"] != record["destination"]
                and self.recovery.digest(source) != record["before"]
            ):
                raise ValueError("Original location contains another file.")
            record["status"] = "undoing"
            self.recovery.save("organisation:" + identity, record)
            if record["source"] == record["destination"]:
                restore = source.parent / (
                    ".pig-music-restore-" + identity + source.suffix
                )
                shutil.copyfile(backup, restore)
                os.replace(restore, source)
            else:
                if not source.exists():
                    os.link(backup, source)
                if target.exists():
                    target.unlink()
            checked, _ = inspect_file(source, record["source"])
            updated = {
                **record["track"],
                **(record.get("placed_from") or {}),
                **checked,
                "id": record["track_id"],
                "sha256": record["before"],
            }
            with self.store.connect() as c:
                row = c.execute(
                    "SELECT payload FROM acquired WHERE id=?", (record["track_id"],)
                ).fetchone()
                if row:
                    acquired = json.loads(row[0])
                    acquired.update(record.get("placed_from") or {})
                    acquired.update(
                        export_relative_path=record["source"],
                        sha256=record["before"],
                        size_bytes=source.stat().st_size,
                        mtime_ns=source.stat().st_mtime_ns,
                    )
                    c.execute(
                        "UPDATE acquired SET payload=? WHERE id=?",
                        (json.dumps(acquired), record["track_id"]),
                    )
            snapshot = load_json(self.store.directory / "library-snapshot.json")
            snapshot["tracks"] = [
                updated if t["id"] == record["track_id"] else t
                for t in snapshot.get("tracks", [])
            ]
            temp = self.store.directory / "organisation-snapshot.tmp"
            temp.write_text(json.dumps(snapshot), encoding="utf-8")
            temp.replace(self.store.directory / "library-snapshot.json")
            record["status"] = "undone"
            self.recovery.save("organisation:" + identity, record)
            self.store.refresh_library()
            return {"status": "undone"}
