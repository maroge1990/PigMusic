"""Hash-guarded, recoverable removal on the existing SMB filesystem."""

import hashlib, json, os, threading, uuid
from .lifecycle import coordinated
from pathlib import Path
from .media import saved_track, track_file
from .library import identifier, normalise, load_json
from .exporter import contained
from .matching import exclusion_key, same_song
from .storage import library_root, require_library_mount


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

    @coordinated
    def recover(self):
        for record in self.list():
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
