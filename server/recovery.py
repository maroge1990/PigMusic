"""Hash-guarded, recoverable removal on the existing SMB filesystem."""

import hashlib, json, os, stat, threading, time, uuid
from .lifecycle import coordinated
from pathlib import Path
from .media import saved_track, track_file
from .library import identifier, normalise, load_json
from .exporter import contained
from .matching import exclusion_key, same_song
from .storage import library_root, open_owned_folder, require_library_mount


class Recovery:
    def __init__(self, store):
        self.store = store
        self.lock = threading.Lock()

    def save(self, key, value):
        with self.store.connect() as c:
            c.execute(
                "INSERT INTO documents VALUES (?,?) ON CONFLICT(name) DO UPDATE SET payload=excluded.payload",
                (key, json.dumps(value)),
            )

    def list(self):
        with self.store.connect() as c:
            return [
                json.loads(v)
                for (v,) in c.execute(
                    "SELECT payload FROM documents WHERE name >= 'removed:' AND name < 'removed;'"
                )
            ]

    def digest(self, path):
        h = hashlib.sha256()
        with path.open("rb") as f:
            for data in iter(lambda: f.read(1024 * 1024), b""):
                h.update(data)
        return h.hexdigest()

    def check_mount(self, root):
        # A download not yet published lives in the download workspace on
        # the library share, which carries no marker of its own: resolving
        # the workspace already requires the library's marker.
        if Path(root) == library_root(self.store):
            return
        require_library_mount(self.store.directory, root)

    def prepare(self, track_id, identity=None):
        track, root = saved_track(self.store, track_id)
        if not track:
            raise ValueError("Song not found.")
        self.check_mount(root)
        if (root / track.get("relative_path", "")).is_symlink():
            raise ValueError("Linked files need review.")
        path = track_file(self.store, track_id)
        root = root.resolve()
        if path.is_symlink():
            raise ValueError("Linked files need review before removal.")
        original = path.relative_to(root).as_posix()
        identity = identity or str(uuid.uuid4())
        trash = ".pig-music-trash/" + identity + "/" + original
        record = {
            "id": identity,
            "track_id": track_id,
            "track": track,
            "root": str(root),
            "original": original,
            "trash": trash,
            "sha256": self.digest(path),
            "status": "removing",
            "removed_at": int(time.time()),
            "shared": any(
                t["id"] != track_id
                and (t.get("export_relative_path") or t.get("relative_path"))
                == original
                for a in self.store.library["albums"]
                for t in a["tracks"]
            ),
        }
        with self.store.connect() as conn:
            record["memberships"] = [
                list(r)
                for r in conn.execute(
                    "SELECT * FROM library_members WHERE track_id=?", (track_id,)
                )
            ]
        return record, root

    def begin(self, conn, record, exclude=True):
        from .user_library import bump

        conn.execute(
            "INSERT INTO documents VALUES (?,?) ON CONFLICT(name) DO UPDATE SET payload=excluded.payload",
            ("removed:" + record["id"], json.dumps(record)),
        )
        if exclude:
            self.exclude(conn, record)
        owners = [
            r[0]
            for r in conn.execute(
                "SELECT user_id FROM library_members WHERE track_id=?",
                (record["track_id"],),
            )
        ]
        conn.execute(
            "DELETE FROM library_members WHERE track_id=?", (record["track_id"],)
        )
        for owner in owners:
            bump(conn, owner)

    def exclude(self, conn, record):
        """A removed song is not requested again until it is restored."""
        conn.execute(
            "INSERT INTO documents VALUES (?,?) ON CONFLICT(name) DO UPDATE SET payload=excluded.payload",
            (
                "excluded:" + exclusion_key(record["track"]),
                json.dumps({"track": record["track"], "removal_id": record["id"]}),
            ),
        )
        for job_id, payload in conn.execute("SELECT id,payload FROM queue").fetchall():
            if same_song(json.loads(payload), record["track"]):
                conn.execute(
                    "UPDATE queue SET status='cancelled' WHERE id=? AND status IN ('waiting_for_sources','searching','downloading','validating','importing')",
                    (job_id,),
                )

    @coordinated
    def remove(self, track_id, exclude=True, refresh=True, identity=None):
        """Move a song's file to Trash. `exclude=False` when another copy of
        the song stays, so requests for it keep finding that copy; a batch
        passes `refresh=False` and refreshes the library once at the end. A
        caller journaling the removal passes the `identity` it recorded."""
        with self.lock:
            record, root = self.prepare(track_id, identity)
            with self.store.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                self.begin(conn, record, exclude)
            self.finish(record, root)
            if refresh:
                self.store.refresh_library()
            return record

    def finish(self, record, root):
        source = contained(root, record["original"])
        trash = contained(root, record["trash"])
        trash.parent.mkdir(parents=True, exist_ok=True)
        contained(root, ".pig-music-trash/.plexignore").write_text(
            "*\n", encoding="utf-8"
        )
        if source.exists() and self.digest(source) != record["sha256"]:
            raise ValueError("The original changed; removal stopped.")
        if trash.exists() and self.digest(trash) != record["sha256"]:
            raise ValueError("Trash destination changed; removal stopped.")
        if not trash.exists():
            if not source.is_file():
                raise ValueError("Original file unavailable; removal stopped.")
            os.link(source, trash)
        if source.exists() and not record.get("shared"):
            source.unlink()
        record["status"] = "removed"
        self.save("removed:" + record["id"], record)

    @coordinated
    def restore(self, identity):
        with self.lock:
            record = self.store.read_document("removed:" + identity)
            if record.get("status") not in {"removed", "removing"}:
                raise ValueError("Removed song not found.")
            if record.get("purged_at"):
                raise ValueError("This song was deleted from Trash for good.")
            if record.get("quarantine"):
                raise ValueError("This song needs review in Trash.")
            _, root = saved_track(self.store, record["track_id"])
            self.check_mount(root)
            if str(root.resolve()) != record["root"]:
                raise ValueError("Original library mount changed; restore stopped.")
            original = contained(root, record["original"])
            trash = contained(root, record["trash"])
            if not trash.is_file() or self.digest(trash) != record["sha256"]:
                raise ValueError("Recoverable file unavailable or changed.")
            if original.exists() and self.digest(original) != record["sha256"]:
                raise ValueError("The original location now contains another file.")
            original.parent.mkdir(parents=True, exist_ok=True)
            if not original.exists():
                os.link(trash, original)
            self.reinstate(record, "restored")
            self.store.refresh_library()
            return record

    def reinstate(self, record, status):
        """The song is back: its listeners and requests return with the
        record's new `status`, in one transaction."""
        from .user_library import bump

        record["status"] = status
        with self.store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute(
                "UPDATE documents SET payload=? WHERE name=?",
                (json.dumps(record), "removed:" + record["id"]),
            )
            for membership in record.get("memberships", []):
                if c.execute(
                    "SELECT 1 FROM users WHERE id=?",
                    (membership[0],),
                ).fetchone():
                    added = c.execute(
                        "INSERT OR IGNORE INTO library_members VALUES (?,?,?,?)",
                        membership,
                    ).rowcount
                    if added:
                        bump(c, membership[0])
            for name, payload in c.execute(
                "SELECT name,payload FROM documents WHERE name >= 'excluded:' AND name < 'excluded;'"
            ).fetchall():
                if json.loads(payload).get("removal_id") == record["id"]:
                    c.execute("DELETE FROM documents WHERE name=?", (name,))

    def settle(self, identity):
        """Bring an interrupted removal to rest and return its status; None
        when it never started. One that never reached Trash (the original is
        in place, unchanged) is cancelled without touching the file, so a
        filesystem that refuses the Trash link cannot keep the song removed;
        otherwise the removal is finished."""
        with self.lock:
            record = self.store.read_document("removed:" + identity)
            if record.get("status") == "removing":
                _, root = saved_track(self.store, record["track_id"])
                self.check_mount(root)
                if str(root.resolve()) != record["root"]:
                    raise ValueError("Original library mount changed.")
                source = contained(root, record["original"])
                trash = contained(root, record["trash"])
                if (
                    not trash.exists()
                    and source.is_file()
                    and self.digest(source) == record["sha256"]
                ):
                    self.reinstate(record, "cancelled")
                else:
                    self.finish(record, root)
            return record.get("status")

    def purge_plan(self, days):
        """What a purge of Trash would delete: every removed song whose file
        went to Trash at least `days` days ago, with its size, and a
        fingerprint of exactly that set (the owner confirms it, as Tidy's
        review). Songs with no Trash file there, a linked or escaping path,
        or a changed library mount are left out. A removal from before
        removal times were kept counts from its Trash file's last status
        change (made when it was linked into Trash, or later), so it is
        never counted older than it is."""
        now = time.time()
        items, rows = [], []
        for record in self.list():
            if (
                record.get("status") != "removed"
                or record.get("purged_at")
                or record.get("quarantine")  # Needs review.
            ):
                continue
            # Only this removal's own Trash folder, never anywhere else.
            if not str(record.get("trash", "")).startswith(
                ".pig-music-trash/" + str(record.get("id")) + "/"
            ):
                continue
            try:
                _, root = saved_track(self.store, record["track_id"])
                self.check_mount(root)
                if str(root.resolve()) != record["root"]:
                    continue
                info = os.lstat(contained(root, record["trash"]))
            except (OSError, ValueError, KeyError):
                continue
            if not stat.S_ISREG(info.st_mode):
                continue
            removed_at = record.get("removed_at") or info.st_ctime
            if now - removed_at < days * 86400:
                continue
            track = record.get("track") or {}
            items.append(
                {
                    "id": record["id"],
                    "title": track.get("title") or "",
                    "artist": track.get("artist") or "",
                    "album": track.get("album") or "",
                    "removed_at": int(removed_at),
                    "size_bytes": info.st_size,
                }
            )
            rows.append([record["id"], record["trash"], record["sha256"], info.st_size])
        items.sort(key=lambda i: (i["removed_at"], i["id"]))
        fingerprint = hashlib.sha256(
            json.dumps([days, sorted(rows)]).encode()
        ).hexdigest()
        return {
            "days": days,
            "items": items,
            "total_bytes": sum(i["size_bytes"] for i in items),
            "fingerprint": fingerprint,
        }

    @coordinated
    def purge(self, days, fingerprint):
        """Delete for good exactly the Trash files the owner reviewed: the
        plan is made again and must match `fingerprint`, and each file must
        still hold the content its removal recorded. Nothing else in Trash
        (Tidy's moves, replaced originals) and nothing outside it is
        touched. The record stays, marked purged, so the song stays removed
        and its requests stay excluded."""
        with self.lock:
            plan = self.purge_plan(days)
            if plan["fingerprint"] != fingerprint:
                raise ValueError("Trash changed since the review; review it again.")
            purged, kept = 0, 0
            for item in plan["items"]:
                record = self.store.read_document("removed:" + item["id"])
                try:
                    _, root = saved_track(self.store, record["track_id"])
                    contained(root, record["trash"])  # No "..", no escape.
                    root = root.resolve()
                except (OSError, ValueError, KeyError):
                    kept += 1
                    continue
                parts = Path(record["trash"]).parts
                # The private name and the folder it is made in (device,
                # inode) are recorded before the rename, so a stop part-way
                # is settled at the next start (`settle_purge`).
                try:
                    folder = open_owned_folder(root, *parts[:-1])
                    try:
                        info = os.fstat(folder)
                    finally:
                        os.close(folder)
                except OSError:
                    kept += 1
                    continue
                record["quarantine"] = ".pig-music-purge-" + uuid.uuid4().hex
                record["quarantine_folder"] = [info.st_dev, info.st_ino]
                self.save("removed:" + record["id"], record)
                try:
                    deleted = self.delete_trashed(
                        root,
                        parts[:-1],
                        parts[-1],
                        record["quarantine"],
                        record["sha256"],
                        record["quarantine_folder"],
                    )
                except (OSError, ValueError):
                    deleted = False
                if deleted is None:
                    # Its folder changed before anything was renamed.
                    kept += 1
                    record.pop("quarantine")
                    record.pop("quarantine_folder")
                    self.save("removed:" + record["id"], record)
                    continue
                if not deleted:
                    kept += 1
                    try:
                        # What it renamed goes back, whatever it holds.
                        self.settle_purge(record, root, verify=False)
                    except (OSError, ValueError):
                        pass  # The intent stays for the next start.
                    continue
                record["purged_at"] = int(time.time())
                record.pop("quarantine")
                record.pop("quarantine_folder")
                self.save("removed:" + record["id"], record)
                purged += 1
                # Its now-empty folders inside this removal's Trash folder,
                # each removed through its parent's descriptor.
                for depth in range(len(parts) - 2, 0, -1):
                    try:
                        parent = open_owned_folder(root, *parts[:depth])
                    except OSError:
                        break
                    try:
                        os.rmdir(parts[depth], dir_fd=parent)  # Only when empty.
                    except OSError:
                        break
                    finally:
                        os.close(parent)
            return {"purged": purged, "kept": kept}

    @staticmethod
    def held(folder, name, sha256):
        """Whether `name` in the open folder is a regular file (never
        followed) holding `sha256`, and still the same file after hashing."""
        try:
            handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=folder)
            with os.fdopen(handle, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or (
                    hashlib.file_digest(stream, "sha256").hexdigest() != sha256
                ):
                    return False
            now = os.stat(name, dir_fd=folder, follow_symlinks=False)
        except OSError:
            return False
        return (now.st_dev, now.st_ino) == (info.st_dev, info.st_ino)

    def delete_trashed(self, root, folders, name, quarantine, sha256, identity):
        """Delete Trash file `name` in root/folders only if it holds `sha256`;
        whether it went. The folders are opened without following a link
        from the root on and held open, so a folder another writer swaps for
        a link meanwhile is never followed; the file is first renamed to the
        private `quarantine` name inside that held folder, then checked
        through its own descriptor, and that same file is deleted. One that
        doesn't match stays under the private name for `settle_purge`. None
        when the folder is no longer the one recorded (`identity`): nothing
        was renamed."""
        folder = open_owned_folder(root, *folders)
        try:
            info = os.fstat(folder)
            if [info.st_dev, info.st_ino] != list(identity):
                return None
            os.rename(name, quarantine, src_dir_fd=folder, dst_dir_fd=folder)
            if not self.held(folder, quarantine, sha256):
                return False
            os.unlink(quarantine, dir_fd=folder)
            return True
        finally:
            os.close(folder)

    def settle_purge(self, record, root, verify=True):
        """Bring a purge that kept or was interrupted with its file under the
        private name to rest, then clear the recorded intent. The file is
        put back under its name when it still holds the removal's content
        (never over another file; if the name is taken, the record points at
        the private name instead, so Restore and purge still find it). With
        neither name there in the very folder recorded before the rename
        (device and inode), the file was deleted before the purge was
        recorded: the record is marked purged. In any other folder that
        proves nothing: ValueError, and the intent stays (the song is then
        listed as needing review). `verify=False` (the purge's
        own rename just now) puts back whatever is there."""
        parts = Path(record["trash"]).parts
        folders, name, quarantine = parts[:-1], parts[-1], record["quarantine"]
        folder = open_owned_folder(root, *folders)
        try:
            try:
                there = os.stat(quarantine, dir_fd=folder, follow_symlinks=False)
            except FileNotFoundError:
                there = None
            if there is None:
                try:
                    os.stat(name, dir_fd=folder, follow_symlinks=False)
                except FileNotFoundError:
                    info = os.fstat(folder)
                    if [info.st_dev, info.st_ino] != list(
                        record.get("quarantine_folder") or []
                    ):
                        raise ValueError("The Trash folder changed; needs review.")
                    record["purged_at"] = int(time.time())
            elif not verify or self.held(folder, quarantine, record["sha256"]):
                try:
                    os.link(
                        quarantine,
                        name,
                        src_dir_fd=folder,
                        dst_dir_fd=folder,
                        follow_symlinks=False,
                    )
                except FileExistsError:
                    # Put back already (a stop before the private name went)?
                    other = os.stat(name, dir_fd=folder, follow_symlinks=False)
                    if (other.st_dev, other.st_ino) != (there.st_dev, there.st_ino):
                        record["trash"] = "/".join(folders + (quarantine,))
                        name = None
                if name is not None:
                    os.unlink(quarantine, dir_fd=folder)
            # Otherwise the private name holds something else: left alone.
        finally:
            os.close(folder)
        record.pop("quarantine")
        record.pop("quarantine_folder", None)
        self.save("removed:" + record["id"], record)

    @coordinated
    def recover(self):
        for record in self.list():
            if record.get("quarantine") and not record.get("purged_at"):
                try:
                    _, root = saved_track(self.store, record["track_id"])
                    self.check_mount(root)
                    if str(root.resolve()) == record["root"]:
                        contained(root, record["trash"])
                        self.settle_purge(record, root.resolve())
                except (OSError, ValueError, KeyError):
                    pass
                continue
            if record["status"] != "removing":
                continue
            try:
                _, root = saved_track(self.store, record["track_id"])
                self.check_mount(root)
                if str(root.resolve()) == record["root"]:
                    self.finish(record, root)
            except (OSError, ValueError):
                pass
        self.store.refresh_library()
