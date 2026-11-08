"""App-owned slskd API adapter. Only completed files enter validation."""

import json
import os
import re
import time
import threading
from pathlib import Path, PureWindowsPath
from urllib.parse import quote

import httpx
from .quality import satisfies
from .storage import download_root, open_owned_folder, unlink_regular


def words(value):
    import unicodedata

    return re.findall(
        r"\w+",
        "".join(
            c
            for c in unicodedata.normalize("NFKD", value).casefold()
            if not unicodedata.combining(c)
        ),
    )


# Filename words beside the title that name an edition, not another song.
EDITION_NAMES = {
    "remaster",
    "remastered",
    "deluxe",
    "edition",
    "expanded",
    "anniversary",
    "bonus",
    "track",
    "album",
    "version",
    "explicit",
    "clean",
    "mono",
    "stereo",
    "original",
    "digital",
    "special",
    "reissue",
}


def candidate_for(job, response, file):
    name = file.get("filename", "")
    path = PureWindowsPath(name.replace("/", "\\"))
    codec = path.suffix.lower()
    profile = job["quality_profile"]
    if profile == "native_standard":
        if codec not in {".mp3", ".m4a", ".flac", ".opus", ".ogg"}:
            return None
    elif codec != {"lossless": ".flac", "aac_256": ".m4a"}.get(profile, ".mp3"):
        return None
    size = file.get("size", 0)
    if not isinstance(size, int) or not 0 < size <= 256_000_000:
        return None
    from .matching import ACQUISITION_TOLERANCE, duration_of, lead_artist
    from .matching import recording_kind, strip_edition

    path_words = set(words(name))
    # Any edition's folder and title will do unless this edition was asked
    # for: the shared edition-free title and lead artist decide (matching).
    strict = job.get("explicit_edition")
    artist = job["artist"] if strict else lead_artist(job["artist"])
    if not set(words(artist)).issubset(path_words) or (
        strict and not set(words(job["album"])).issubset(path_words)
    ):
        return None
    title = words(job["title"] if strict else strip_edition(job["title"]))
    stem = words(path.stem)
    if not title or not any(
        stem[i : i + len(title)] == title for i in range(len(stem))
    ):
        return None
    start = next(i for i in range(len(stem)) if stem[i : i + len(title)] == title)
    remainder = stem[:start] + stem[start + len(title) :]
    allowed = set(words(job["artist"] + " " + job["album"]))
    if not strict:
        allowed |= EDITION_NAMES
        # "Song (feat. Guest)": a featured credit, not another title.
        for marker in ("feat", "ft", "featuring", "with"):
            if marker in remainder:
                remainder = remainder[: remainder.index(marker)]
    if any(
        w not in allowed
        and not w.isdigit()
        and not re.fullmatch(r"(?:cd|disc|d|t)\d+", w)
        for w in remainder
    ):
        return None
    from .downloader import EDITION_WORDS, VERSION_WORDS

    if strict:
        expected = set(words(job["title"] + " " + job["album"])) & VERSION_WORDS
        versions = VERSION_WORDS
    else:
        # Distinct recordings (live, remix, demo...) stay distinct: by title
        # qualifier, album title or known release type.
        versions = VERSION_WORDS - EDITION_WORDS
        expected = (
            set(words(strip_edition(job["title"]))) | set(recording_kind(job))
        ) & versions
    if path_words & versions != expected:
        return None
    length = file.get("length", 0)
    if not isinstance(length, (int, float)) or length <= 0:
        return None
    wanted = duration_of(job)
    if wanted and abs(length - wanted) > ACQUISITION_TOLERANCE:
        return None
    bitrate = file.get("bitRate", 0) or 0
    if profile == "native_standard" and not satisfies(
        profile,
        {
            "codec": {".m4a": "aac", ".ogg": "vorbis"}.get(codec, codec.lstrip(".")),
            "bitrate": bitrate,
        },
    ):
        return None
    minimum = {"mp3_320": 300, "mp3_128": 120, "aac_256": 240}.get(profile, 0)
    if codec != ".flac" and bitrate < minimum:
        return None
    return {
        "title": job["title"],
        "channel": job["artist"],
        "album": job["album"],
        "duration": length,
        "filename": name,
        "size": size,
        "username": response["username"],
        "extension": codec,
        "folder": str(path.parent),
        "queue_length": response.get("queueLength", 0) or 0,
        "quality": "320kbps" if bitrate >= 300 else str(bitrate),
    }


# Imports stream in pieces this size (#96).
CHUNK = 1024 * 1024
# A search is read this often, and ends once this many of its files would
# do for the song or the engine says it is complete (#131), at the latest
# after FIRST_SEARCH seconds (the album search) or LATER_SEARCH (each
# fallback search, which follows a wait already spent).
SEARCH_POLL = 2
ENOUGH_FILES = 3
FIRST_SEARCH = 35
LATER_SEARCH = 15
# A search is deleted only once the engine has said it is complete and has
# had this long to finish storing it (#131): deleting it sooner fails inside
# the engine. Ones never seen complete are dropped from the list after
# FORGET_SEARCH; the engine's own retention has them then.
DELETE_AFTER = 10
FORGET_SEARCH = 3600
# A transfer is read this often. A peer that has sent nothing after
# QUEUED_LIMIT seconds (queued behind its other uploads, usually) is given
# up for the next candidate (#132); one sending data has up to
# TRANSFER_LIMIT. A peer with more than BUSY_QUEUE uploads waiting ranks
# after every less busy one, whatever share of the album it has.
TRANSFER_POLL = 1
# How long a succeeded transfer's file may take to appear in its folder (#167).
MOVE_WAIT = 10
QUEUED_LIMIT = 40
TRANSFER_LIMIT = 240
BUSY_QUEUE = 10
# The engine's leftovers are swept at most this often (#96).
SWEEP_EVERY = 3600
# How the engine (slskd) reports a transfer that finished without success:
# "Completed, Errored", "Completed, Cancelled" and so on.
UNSUCCESSFUL = ("cancelled", "errored", "failed", "rejected", "timedout", "aborted")


def engine_key(filename):
    """The engine keeps a transfer's file as <remote folder name>/<file name>
    under its folders (see `completed_path`)."""
    original = PureWindowsPath(filename)
    return original.parent.name, original.name


def unsuccessful(state):
    state = state.lower().replace(" ", "")
    return (
        "completed" in state
        and "succeeded" not in state
        and any(word in state for word in UNSUCCESSFUL)
    )


class Soulseek:
    def __init__(self, store):
        self.store = store
        self.cache = {}
        self.search_lock = threading.Lock()
        self.peer_locks = {}
        self.peer_guard = threading.Lock()
        # Engine files this process is transferring or importing, by
        # `engine_key`, from before the engine is asked for them until the
        # import is over; the leftover sweep never touches one, and decides
        # and deletes under this lock (#96).
        self.files_lock = threading.Lock()
        self.active = {}
        self.swept_at = None
        # Searches to delete: {engine id: (when we finished with it, whether
        # the engine had said it was complete)} (#131).
        self.finished = {}
        self.finished_lock = threading.Lock()
        # Per thread, the dict a transfer records the peer's last state in,
        # for the job's timings (#134); the downloader sets it.
        self.reports = threading.local()

    def config(self):
        path = self.store.directory / "slskd-connection.json"
        if not path.exists():
            raise ValueError("Soulseek engine connection has not been configured")
        config = json.loads(path.read_text(encoding="utf-8-sig"))
        if not config.get("url") or not config.get("api_key"):
            raise ValueError("Soulseek engine connection is incomplete")
        return config

    def client(self):
        config = self.config()
        return httpx.Client(
            base_url=config["url"].rstrip("/") + "/api/v0/",
            timeout=15,
            headers={"X-API-Key": config["api_key"]},
        )

    def health_client(self):
        """One engine client kept for the health check, asked every 15 s
        (#142); a new one when the connection settings change."""
        config = self.config()
        key = (config["url"], config["api_key"])
        with self.peer_guard:
            kept = getattr(self, "kept_client", None)
            if kept and kept[0] == key and not kept[1].is_closed:
                return kept[1]
            if kept:
                kept[1].close()
            client = self.client()
            self.kept_client = (key, client)
            return client

    def health(self):
        try:
            response = self.health_client().get("server")
            response.raise_for_status()
            data = response.json()
            connected = bool(data.get("isConnected") and data.get("isLoggedIn"))
            return {"configured": True, "connected": connected}
        except (ValueError, OSError, httpx.HTTPError):
            return {
                "configured": (self.store.directory / "slskd-connection.json").exists(),
                "connected": False,
            }

    def search(self, job, check):
        while not self.search_lock.acquire(timeout=0.5):
            check()
        try:
            check()
            return self._search(job, check)
        finally:
            self.search_lock.release()

    def _run_search(self, text, check, enough=lambda responses: False, limit=None):
        """The engine's responses to `text`, read every SEARCH_POLL seconds
        until `enough(responses)`, the engine completes the search, or
        `limit` seconds pass (#131); and whether the engine had completed
        it."""
        with self.client() as client:
            self.tidy(client)
            check()
            started = client.post("searches", json={"searchText": text})
            started.raise_for_status()
            search_id = quote(started.json()["id"], safe="")
            complete = False
            try:
                deadline = time.monotonic() + (limit or FIRST_SEARCH)
                while True:
                    check()
                    status = client.get("searches/" + search_id)
                    status.raise_for_status()
                    complete = bool(status.json().get("isComplete"))
                    result = client.get(f"searches/{search_id}/responses")
                    result.raise_for_status()
                    responses = result.json()
                    if (
                        complete
                        or enough(responses)
                        or time.monotonic() + SEARCH_POLL > deadline
                    ):
                        return responses, complete
                    time.sleep(SEARCH_POLL)
            finally:
                # Deleted later, once the engine has finished with it.
                with self.finished_lock:
                    self.finished[search_id] = (time.monotonic(), complete)

    def tidy(self, client, now=None):
        """Delete the searches the engine has finished storing (#131)."""
        now = time.monotonic() if now is None else now
        with self.finished_lock:
            pending = list(self.finished.items())
        for search_id, (ended, complete) in pending:
            if now - ended < DELETE_AFTER:
                continue
            try:
                if not complete:
                    status = client.get("searches/" + search_id)
                    if status.status_code == 404:
                        self.forget(search_id)
                        continue
                    status.raise_for_status()
                    if not status.json().get("isComplete"):
                        if now - ended > FORGET_SEARCH:
                            self.forget(search_id)
                        continue
                    # Seen complete only now: it gets its own settling time.
                    with self.finished_lock:
                        self.finished[search_id] = (now, True)
                    continue
                client.delete("searches/" + search_id)
                self.forget(search_id)
            except (httpx.HTTPError, ValueError):
                pass  # Tried again on the next search or sweep.

    def forget(self, search_id):
        with self.finished_lock:
            self.finished.pop(search_id, None)

    def _search(self, job, check):
        def enough(responses):
            found = 0
            for response in responses:
                for file in response.get("files", []):
                    found += bool(candidate_for(job, response, file))
                    if found >= ENOUGH_FILES:
                        return True
            return False

        def found(responses):
            return any(
                candidate_for(job, r, f) for r in responses for f in r.get("files", [])
            )

        def cached(key, run):
            """A search's responses, reused for ten minutes. One that ended
            early (enough files for another song of the album) is searched
            again when it has nothing for this song (#131)."""
            entry = self.cache.get(key)
            if (
                entry
                and time.monotonic() - entry[0] < 600
                and (entry[2] or found(entry[1]))
            ):
                return entry[1]
            responses, complete = run()
            self.cache.pop(key, None)
            self.cache[key] = (time.monotonic(), responses, complete)
            if len(self.cache) > 50:
                self.cache.pop(next(iter(self.cache)))
            return responses

        def album_search():
            primary = f"{job['artist']} {job['album']}"
            responses, complete = self._run_search(primary, check, enough)
            album = (job["album"] or "").strip()
            if not responses and album and album.lower() != primary.lower():
                # The Soulseek server silently filters some artist names.
                return self._run_search(album, check, enough, LATER_SEARCH)
            return responses, complete

        responses = cached((job["artist"], job["album"]), album_search)
        if not job.get("explicit_edition") and not found(responses):
            # No folder of that album has it: look for the song on any album.
            from .matching import lead_artist, strip_edition

            text = f"{lead_artist(job['artist'])} {strip_edition(job['title'])}"
            responses = cached(
                ("song", text),
                lambda: self._run_search(text, check, enough, LATER_SEARCH),
            )
        siblings = [
            t
            for t in self.store.queue()["items"]
            if t["album"] == job["album"]
            and t["album_artist"] == job["album_artist"]
            and t.get("musicbrainz_release_id") == job.get("musicbrainz_release_id")
        ]
        candidates = []
        for response in responses:
            files = response.get("files", [])
            for file in files:
                candidate = candidate_for(job, response, file)
                if candidate:
                    candidate["album_coverage"] = sum(
                        any(
                            candidate_for(
                                {**s, "quality_profile": job["quality_profile"]},
                                response,
                                f,
                            )
                            for f in files
                            if str(PureWindowsPath(f.get("filename", "")).parent)
                            == candidate["folder"]
                        )
                        for s in siblings
                    )
                    candidates.append(candidate)
        return sorted(
            candidates,
            key=lambda c: (
                c["queue_length"] > BUSY_QUEUE,
                -c["album_coverage"],
                c["queue_length"],
                c["username"],
                c["filename"],
            ),
        )[:20]

    def transfer(self, candidate, destination, check, progress):
        key = engine_key(candidate["filename"])
        with self.files_lock:
            self.active[key] = self.active.get(key, 0) + 1
        try:
            with self.peer_guard:
                lock = self.peer_locks.setdefault(
                    candidate["username"], threading.Lock()
                )
            while not lock.acquire(timeout=0.5):
                check()
            try:
                check()
                return self._transfer(candidate, destination, check, progress)
            finally:
                lock.release()
        finally:
            with self.files_lock:
                self.active[key] -= 1
                if not self.active[key]:
                    del self.active[key]

    def _transfer(self, candidate, destination, check, progress):
        report = getattr(self.reports, "current", None)
        report = {} if report is None else report
        username = quote(candidate["username"], safe="")
        transfer_id = None
        with self.client() as client:
            check()
            try:
                response = client.post(
                    "transfers/downloads/" + username,
                    json=[
                        {"filename": candidate["filename"], "size": candidate["size"]}
                    ],
                )
                response.raise_for_status()
                began = time.monotonic()
                deadline = began + TRANSFER_LIMIT
                received = 0
                while time.monotonic() < deadline:
                    check()
                    response = client.get("transfers/downloads/" + username)
                    response.raise_for_status()
                    data = response.json()
                    files = (
                        [
                            f
                            for d in data.get("directories", [])
                            for f in d.get("files", [])
                        ]
                        if isinstance(data, dict)
                        else data
                    )
                    match = next(
                        (
                            f
                            for f in files
                            if f.get("filename") == candidate["filename"]
                            and f.get("size") == candidate["size"]
                        ),
                        None,
                    )
                    if match:
                        transfer_id = match["id"]
                        state = str(
                            match.get("stateDescription", match.get("state", ""))
                        ).lower()
                        report["state"] = state
                        received = match.get("bytesTransferred", 0) or 0
                        progress(received, candidate["size"])
                        if "succeeded" in state:
                            self.copy_completed(candidate, destination, check)
                            check()
                            return
                        if any(
                            s in state
                            for s in (
                                "failed",
                                "cancelled",
                                "rejected",
                                "errored",
                                "aborted",
                            )
                        ):
                            raise ValueError("Soulseek peer transfer failed")
                    if not received and time.monotonic() - began >= QUEUED_LIMIT:
                        raise ValueError("Soulseek peer sent nothing; trying another")
                    time.sleep(TRANSFER_POLL)
                raise ValueError("Soulseek peer transfer timed out")
            finally:
                if transfer_id is None:
                    try:
                        response = client.get("transfers/downloads/" + username)
                        response.raise_for_status()
                        data = response.json()
                        files = (
                            [
                                f
                                for d in data.get("directories", [])
                                for f in d.get("files", [])
                            ]
                            if isinstance(data, dict)
                            else data
                        )
                        match = next(
                            (
                                f
                                for f in files
                                if f.get("filename") == candidate["filename"]
                                and f.get("size") == candidate["size"]
                            ),
                            None,
                        )
                        transfer_id = match.get("id") if match else None
                    except (httpx.HTTPError, ValueError, KeyError):
                        pass
                if transfer_id:
                    # Cancel only the exact transfer owned by this request. Keep files for recovery.
                    try:
                        client.delete(
                            f"transfers/downloads/{username}/{quote(str(transfer_id), safe='')}",
                            params={"remove": False},
                        )
                    except httpx.HTTPError:
                        pass

    def copy_completed(self, candidate, destination, check=None):
        config = self.config()
        if config.get("ssh_host"):
            import base64
            import subprocess

            payload = base64.b64encode(
                json.dumps(
                    {
                        "root": (
                            "/input"
                            if config.get("ssh_copy_image")
                            else config["download_root"]
                        ),
                        "filename": candidate["filename"],
                        "size": candidate["size"],
                    }
                ).encode()
            ).decode()
            script = "\n".join(
                [
                    "import base64,json,pathlib,sys",
                    f"data=json.loads(base64.b64decode('{payload}'))",
                    "root=pathlib.Path(data['root']).resolve()",
                    "original=pathlib.PureWindowsPath(data['filename'])",
                    "assert 0<data['size']<=256000000",
                    "matches=[p for p in root.rglob('*') if p.name==original.name and p.is_file() and p.resolve().is_relative_to(root) and p.stat().st_size==data['size'] and p.parent.name==original.parent.name]",
                    "assert len(matches)==1",
                    "sys.stdout.buffer.write(matches[0].open('rb').read(data['size']+1))",
                ]
            )
            encoded = base64.b64encode(script.encode()).decode()
            command = (
                f"python3 -c \"import base64;exec(base64.b64decode('{encoded}'))\""
            )
            if config.get("ssh_copy_image"):
                import shlex

                code = f"import base64;exec(base64.b64decode('{encoded}'))"
                command = (
                    "docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges --entrypoint python3 -v "
                    + shlex.quote(config["download_root"] + ":/input:ro")
                    + " "
                    + shlex.quote(config["ssh_copy_image"])
                    + " -c "
                    + shlex.quote(code)
                )
            result = subprocess.run(
                [
                    "ssh",
                    "-i",
                    config["ssh_key"],
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ConnectTimeout=5",
                    config["ssh_host"],
                    command,
                ],
                capture_output=True,
                timeout=60,
            )
            if result.returncode or len(result.stdout) != candidate["size"]:
                raise ValueError(
                    "Completed Soulseek file could not be safely read from the test engine"
                )
            destination.write_bytes(result.stdout)
            return
        root = Path(config["download_root"]).resolve()
        # The engine reports a transfer succeeded about a second before it
        # moves the file out of its incomplete folder (#167): wait for it.
        moved_by = time.monotonic() + MOVE_WAIT
        while True:
            try:
                source = completed_path(root, candidate)
                break
            except NotYetMoved:
                if time.monotonic() >= moved_by:
                    raise
                if check:
                    check()
                time.sleep(0.25)
        owned = engine_owned(root)
        size = candidate["size"]
        # The engine's own folder is on the work disk: a hard link imports
        # without a copy. Otherwise the file is streamed, never held whole
        # in memory (up to 256 MB, six at once).
        linked = False
        if owned:
            try:
                os.link(source, destination)
                linked = True
            except OSError:
                pass
        if not linked:
            with source.open("rb") as incoming, destination.open("wb") as outgoing:
                left = size
                while left > 0:
                    chunk = incoming.read(min(left, CHUNK))
                    if not chunk:
                        break
                    outgoing.write(chunk)
                    left -= len(chunk)
                extra = incoming.read(1)
            if left or extra:
                destination.unlink(missing_ok=True)
                raise ValueError("Completed Soulseek file changed during import")
        elif destination.stat().st_size != size:
            destination.unlink(missing_ok=True)
            raise ValueError("Completed Soulseek file changed during import")
        if owned:
            source.unlink()

    def engine_transfers(self):
        """Every download the engine lists: {engine_key: [state, ...]}.
        ValueError when the answer is not the expected shape, so a sweep
        never acts on a list it could not read whole."""
        with self.client() as client:
            response = client.get("transfers/downloads")
            response.raise_for_status()
            data = response.json()
        users = data if isinstance(data, list) else [data]
        states = {}
        try:
            for user in users:
                for directory in user["directories"]:
                    for item in directory["files"]:
                        filename = item["filename"]
                        state = item.get("stateDescription") or item.get("state")
                        if not isinstance(filename, str) or not isinstance(state, str):
                            raise TypeError
                        states.setdefault(engine_key(filename), []).append(state)
        except (KeyError, TypeError, AttributeError):
            raise ValueError("Unexpected engine transfer list")
        return states

    def sweep(self, now=None):
        """Delete partial files the engine left for transfers it reports as
        finished without success (failed, cancelled, rejected...), at most
        once an hour; how many went. Never by age: a file is deleted only
        when every transfer the engine lists for it is such a one and none
        is being transferred or imported here. Only regular files one
        folder down in engine-incomplete directly beneath the configured
        work root, opened without following a link from the work root on;
        nothing when the work root or either engine folder is a link. A
        file in engine-downloads is a finished transfer: never swept."""
        now = time.monotonic() if now is None else now
        if self.swept_at is not None and now - self.swept_at < SWEEP_EVERY:
            return 0
        self.swept_at = now
        if self.finished:
            # Searches left over from the last one this process ran (#131).
            try:
                with self.client() as client:
                    self.tidy(client)
            except (OSError, ValueError, httpx.HTTPError):
                pass
        work = os.environ.get("PIG_MUSIC_DOWNLOAD_ROOT")
        try:
            config = self.config()
            if not work or config.get("ssh_host"):
                return 0
            work = Path(work)
            # Only the embedded engine, saving into the work root's folder.
            if os.path.normpath(
                str(config.get("download_root", ""))
            ) != os.path.normpath(work / "engine-downloads"):
                return 0
            download_root(self.store)  # The expected library mount, or nothing.
        except (OSError, ValueError):
            return 0
        removed = 0
        with self.files_lock:
            try:
                os.close(open_owned_folder(work, "engine-downloads"))
                incomplete = open_owned_folder(work, "engine-incomplete")
            except OSError:
                return 0
            try:
                states = self.engine_transfers()
                for folder_name in os.listdir(incomplete):
                    try:
                        folder = os.open(
                            folder_name,
                            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=incomplete,
                        )
                    except OSError:
                        continue  # A file or link at the top: never touched.
                    try:
                        for name in os.listdir(folder):
                            key = (folder_name, name)
                            found = states.get(key)
                            if (
                                not found
                                or key in self.active
                                or not all(unsuccessful(s) for s in found)
                            ):
                                continue
                            try:
                                removed += unlink_regular(folder, name)
                            except OSError:
                                pass
                    finally:
                        os.close(folder)
            except (OSError, ValueError, httpx.HTTPError):
                pass
            finally:
                os.close(incomplete)
        return removed


def engine_owned(folder):
    """Whether `folder` is the embedded engine's own, under the work root."""
    work = os.environ.get("PIG_MUSIC_DOWNLOAD_ROOT")
    return bool(work) and folder.resolve().is_relative_to(Path(work).resolve())


class NotYetMoved(ValueError):
    """The completed file is not in its folder yet: no file of that name or
    of a renamed copy is there at all."""


def completed_path(root, candidate):
    """Where the engine saves a completed transfer: its remote folder's name
    then its file name, under the download folder. A second file of that name
    the engine saved beside it (name_<digits>) of the same size makes it
    ambiguous: that one folder is read, never the whole tree."""
    original = PureWindowsPath(candidate["filename"])
    folder, name = original.parent.name, original.name
    if name in {"", ".", ".."} or folder in {".", ".."} or "/" in name + folder:
        raise ValueError("Completed Soulseek file has an unusable name")
    directory = root / folder if folder else root
    expected = directory / name
    stem, suffix = os.path.splitext(name)
    renamed = re.compile(re.escape(stem) + r"_[0-9]+" + re.escape(suffix))
    size = candidate["size"]
    matches = []
    try:
        entries = list(os.scandir(directory))
    except OSError:
        entries = []
    for entry in entries:
        if entry.name != name and not renamed.fullmatch(entry.name):
            continue
        path = Path(entry.path)
        try:
            if (
                entry.is_file(follow_symlinks=False)
                and entry.stat(follow_symlinks=False).st_size == size
                and path.resolve().is_relative_to(root)
            ):
                matches.append(path)
        except OSError:
            pass
    if not matches and not any(
        entry.name == name or renamed.fullmatch(entry.name) for entry in entries
    ):
        raise NotYetMoved(
            "Completed Soulseek file could not be uniquely located in the configured download folder"
        )
    if matches != [expected]:
        raise ValueError(
            "Completed Soulseek file could not be uniquely located in the configured download folder"
        )
    return expected
