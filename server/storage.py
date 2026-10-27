"""Configured transfer storage; appdata remains the default for previews/tests."""

import hashlib
import os
from pathlib import Path
import time


def require_library_mount(
    directory, root, marker_path=".pig-music-library.json", required=False
):
    from .library import load_json

    config = load_json(Path(directory) / "library-export.json")
    if required or config.get("library_id"):
        marker = load_json(Path(root) / marker_path)
        if (
            not config.get("library_id")
            or marker.get("library_id") != config["library_id"]
        ):
            raise ValueError("The expected SMB library mount is unavailable.")


# Mount checks a reader may reuse for a while: {store directory: monotonic
# time the check passed}. File moves always check afresh (max_age 0).
_MOUNT_CHECKED = {}


def download_root(store, max_age=0):
    value = os.environ.get("PIG_MUSIC_DOWNLOAD_ROOT")
    if value:
        key = str(store.directory)
        checked = _MOUNT_CHECKED.get(key)
        if not (max_age and checked and time.monotonic() - checked < max_age):
            root = Path(os.environ.get("PIG_MUSIC_LIBRARY", "/library"))
            require_library_mount(store.directory, root, required=True)
            _MOUNT_CHECKED[key] = time.monotonic()
    return Path(value or store.directory / "downloads")


def library_root(store, max_age=0):
    """`max_age`: seconds a passed mount check may be reused, for reads only
    (playback, NAS1); anything that writes or deletes keeps the default."""
    return download_root(store, max_age) / "library"


def file_sha256(path):
    """Hash a file in chunks, so large lossless files never load whole into memory."""
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()
