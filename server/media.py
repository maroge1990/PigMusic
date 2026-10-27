"""Private provider settings and read-only playback file resolution."""

import json
import os
import re
import stat
import sys
import time
from pathlib import Path

from starlette.responses import FileResponse

from .library import load_json
from .storage import library_root


def settings(store):
    target = store.directory / "media-settings.json"
    try:
        stat = target.stat()
        signature = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
    except FileNotFoundError:
        signature = None
    cached = getattr(store, "_media_settings_cache", None)
    if cached is None or cached[0] != signature:
        cached = (
            signature,
            {"portraits": True, "remote_playback": True, **load_json(target)},
        )
        store._media_settings_cache = cached
    return dict(cached[1])


def chart_region(value):
    region = str(value.get("chart_region") or "au").lower()
    return region if re.fullmatch(r"[a-z]{2}", region) else "au"


def public_settings(store):
    value = settings(store)
    return {
        "portraits": value["portraits"],
        "remote_playback": value["remote_playback"],
        "fanart_configured": bool(value.get("fanart_key")),
        "listenbrainz_configured": bool(value.get("listenbrainz_token")),
        "chart_region": chart_region(value),
    }


def save_settings(
    store,
    portraits,
    remote_playback,
    fanart_key=None,
    listenbrainz_token=None,
    chart_region=None,
):
    value = {
        **settings(store),
        "portraits": portraits,
        "remote_playback": remote_playback,
    }
    if fanart_key is not None:
        if fanart_key and not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", fanart_key):
            raise ValueError("Enter a valid Fanart API key.")
        value["fanart_key"] = fanart_key
    if listenbrainz_token is not None:
        if listenbrainz_token and not re.fullmatch(
            r"[A-Za-z0-9_-]{16,128}", listenbrainz_token
        ):
            raise ValueError("Enter a valid ListenBrainz user token.")
        value["listenbrainz_token"] = listenbrainz_token
    if chart_region is not None:
        if not re.fullmatch(r"[a-z]{2}", chart_region):
            raise ValueError("Choose a valid chart region.")
        value["chart_region"] = chart_region
    target = store.directory / "media-settings.json"
    temporary = target.with_suffix(".tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    store._media_settings_cache = None
    return public_settings(store)


def saved_track(store, track_id, max_age=0):
    # Acquired tracks may be staged or published; snapshot tracks use the NAS root.
    # `max_age`: reuse a recent mount check (reads only, see library_root).
    with store.connect() as conn:
        row = conn.execute(
            "SELECT payload FROM acquired WHERE id=?", (track_id,)
        ).fetchone()
    if row:
        track = json.loads(row[0])
        if track.get("export_relative_path"):
            return {**track, "relative_path": track["export_relative_path"]}, Path(
                os.environ.get("PIG_MUSIC_LIBRARY", "/library")
            )
        return track, library_root(store, max_age)
    track = store.snapshot_tracks.get(track_id)
    return track, Path(os.environ.get("PIG_MUSIC_LIBRARY", "/library"))


def saved_track_ids(store, track_ids):
    """The ids among `track_ids` that `saved_track` finds: library songs from
    the snapshot, the rest from one query of acquired songs."""
    known = {i for i in track_ids if i in store.snapshot_tracks}
    rest = [i for i in track_ids if i not in known]
    if rest:
        with store.connect() as conn:
            known.update(
                identity
                for (identity,) in conn.execute(
                    "SELECT id FROM acquired WHERE id IN (SELECT value FROM json_each(?))",
                    (json.dumps(rest),),
                )
            )
    return known


AUDIO_SUFFIXES = {".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wav"}
# Playback reuses a mount check and the resolved library root this long
# (NAS1): every lookup on the SMB mount is a network round trip.
PLAYBACK_CACHE_SECONDS = 60
_ROOTS = {}


def saved_relative(track):
    """The saved song's path below its root, checked by its spelling alone."""
    if track is None:
        raise FileNotFoundError
    relative = track.get("relative_path", "").replace("\\", "/")
    if (
        not relative
        or relative.startswith("/")
        or ":" in relative
        or ".." in relative.split("/")
        or Path(relative).suffix.lower() not in AUDIO_SUFFIXES
    ):
        raise FileNotFoundError
    return relative


def playable(store, track_id):
    """Whether a playback address may be given, without asking the NAS: the
    snapshot's path is trusted here, and a missing file fails on open."""
    track, _ = saved_track(store, track_id, PLAYBACK_CACHE_SECONDS)
    saved_relative(track)


def resolved_root(root):
    key = str(root)
    cached = _ROOTS.get(key)
    if cached and time.monotonic() - cached[1] < PLAYBACK_CACHE_SECONDS:
        return cached[0]
    value = Path(root).resolve()
    _ROOTS[key] = (value, time.monotonic())
    return value


def descriptor_path(fd):
    """Where the kernel says an open file is, symlinks followed; no lookup."""
    if sys.platform == "darwin":
        import fcntl

        raw = fcntl.fcntl(fd, fcntl.F_GETPATH, bytes(1024))
        return os.fsdecode(raw.split(b"\0", 1)[0])
    return os.readlink(f"/proc/self/fd/{fd}")


def open_playback(store, track_id):
    """The saved song opened for playing, its fstat and its file type: one NAS
    open, and no
    path walk to check first (NAS1). The descriptor itself must lie inside
    the library root, so a symlinked file or folder pointing elsewhere is
    refused, and a deleted file raises FileNotFoundError. The caller closes
    the descriptor."""
    track, root = saved_track(store, track_id, PLAYBACK_CACHE_SECONDS)
    relative = saved_relative(track)
    try:
        fd = os.open(Path(root) / relative, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise FileNotFoundError from exc
    try:
        info = os.fstat(fd)
        inside = Path(descriptor_path(fd)).is_relative_to(resolved_root(root))
        if not stat.S_ISREG(info.st_mode) or not inside:
            raise FileNotFoundError
    except BaseException:
        os.close(fd)
        raise
    return fd, info, Path(relative).suffix.lower()


def descriptor_name(fd):
    """A name that opens this very descriptor's file again, without walking
    any library path (Linux /proc, macOS /dev/fd)."""
    return f"/dev/fd/{fd}" if sys.platform == "darwin" else f"/proc/self/fd/{fd}"


class DescriptorFileResponse(FileResponse):
    """A FileResponse for a file already opened and checked: each read opens
    that descriptor's own name, never the library path again. Nothing is
    allocated before such an open, so a cancelled request leaks nothing; the
    checked descriptor closes when the response ends."""

    def __init__(self, fd, info, **options):
        self.fd = fd
        super().__init__(descriptor_name(fd), stat_result=info, **options)

    async def __call__(self, scope, receive, send):
        # The server must not send by path: the name is this process's own.
        extensions = dict(scope.get("extensions") or {})
        extensions.pop("http.response.pathsend", None)
        try:
            await super().__call__({**scope, "extensions": extensions}, receive, send)
        finally:
            os.close(self.fd)


def track_file(store, track_id):
    track, root = saved_track(store, track_id)
    if track is None:
        raise FileNotFoundError
    relative = track.get("relative_path", "").replace("\\", "/")
    if (
        not relative
        or relative.startswith("/")
        or ":" in relative
        or ".." in relative.split("/")
    ):
        raise FileNotFoundError
    root = root.resolve()
    target = (root / relative).resolve()
    if (
        not target.is_relative_to(root)
        or not target.is_file()
        or target.suffix.lower() not in AUDIO_SUFFIXES
    ):
        raise FileNotFoundError
    return target
