"""Opt-in metadata/transfer acceptance in a newly created, isolated directory.

Run as a module from the repository root. Use recordings you control or have
permission to download. No production appdata, mount, source settings or cookies
are read. Library transfers are fully validated/tagged/published by Downloader;
--mix-cache instead verifies temporary validated storage, restart and cleanup.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import time

from server.downloader import Downloader, matches
from server.providers.contracts import Context, ProviderError
from server.routes.personal import TrackInput
from server.store import Store
from server.mix_cache import MixCache, MixDownloader, song_key


def verify(source, target, output, metadata_only=False, mix_cache=False, profile=None):
    output = Path(output).absolute()
    output.mkdir(parents=True, exist_ok=False)
    # The normal download root validates NAS identity when explicitly set. This
    # process uses fresh appdata's local staging/library and never that mount.
    os.environ.pop("PIG_MUSIC_DOWNLOAD_ROOT", None)
    os.environ["PIG_MUSIC_LIBRARY"] = str(output / "unmounted-library")
    store = Store(output / "state")
    profile = profile or ("lossless" if source == "monochrome" else "native_standard")
    store.save_download_settings(
        {"enabled": True, "profile": profile, "sources": [source]}
    )
    if mix_cache:
        cache = MixCache(store)
        store.write_document("mix-settings", {"profile": profile})
        worker = MixDownloader(cache, None, lambda: False)
    else:
        worker = Downloader(store)
    report = {
        "source": source,
        "profile": profile,
        "metadata_only": metadata_only,
        "audio_verified": False,
        "purpose": "mix-cache" if mix_cache else "library",
        "recording": {
            key: target.get(key) for key in ("artist", "title", "album", "duration")
        },
    }
    started = time.monotonic()
    if metadata_only:
        try:
            candidates = worker.runtime.search(source, target, Context(profile))
            report.update(
                metadata_reachable=True,
                matching_candidates=len([c for c in candidates if matches(target, c)]),
            )
        except ProviderError as exc:
            report.update(metadata_reachable=False, error=str(exc))
    elif mix_cache:
        complete = worker.fetch(target)
        report["status"] = worker.result
        if complete:
            # Fresh objects must recover the validated file without a library row.
            restarted = Store(store.directory)
            persisted = MixCache(restarted)
            path = persisted.file(song_key(target))
            with restarted.connect() as conn:
                probe = json.loads(
                    conn.execute(
                        "SELECT probe FROM mix_audio WHERE key=?", (song_key(target),)
                    ).fetchone()[0]
                )
                for table in ("queue", "acquired", "library_members"):
                    if conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]:
                        raise ValueError(
                            "Mix preparation created durable library state"
                        )
            report.update(
                audio_verified=True,
                probe=probe,
                bytes=path.stat().st_size,
                restart_verified=True,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            )
            # No active edition owns this standalone acceptance file.
            persisted.cleanup(time.time() + 7200)
            report["cleanup_verified"] = not path.exists()
            if path.exists():
                raise ValueError("Unreferenced acceptance audio was not reclaimed")
        else:
            report["error"] = worker.error or "Mix preparation deferred"
    else:
        store.submit_catalogue([{**target, "status": "missing"}])
        worker.process(store.claim_request())
        request = store.queue()["items"][0]
        report["status"] = request["status"]
        if request["status"] == "completed":
            with store.connect() as conn:
                saved = json.loads(
                    conn.execute("SELECT payload FROM acquired").fetchone()[0]
                )
            path = worker.library_root / saved["relative_path"]
            # Re-open durable state, preserving both publication and queue evidence.
            restarted = Store(store.directory)
            if restarted.queue()["items"][0]["status"] != "completed":
                raise ValueError("Published request did not survive restart")
            report.update(
                audio_verified=True,
                acquisition=saved["acquisition"],
                relative_path=saved["relative_path"],
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        else:
            report["error"] = request.get("error", "Recording did not complete")
    report["seconds"] = round(time.monotonic() - started, 2)
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, choices=("soundcloud", "monochrome"))
    parser.add_argument(
        "--track",
        required=True,
        type=Path,
        help="JSON recording with artist, title, album and duration; optional ISRCs",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="New isolated directory; existing directories are refused",
    )
    parser.add_argument(
        "--metadata-only",
        action="store_true",
        help="Search/hydrate without downloading audio",
    )
    parser.add_argument(
        "--mix-cache",
        action="store_true",
        help="Verify temporary cache download, restart and cleanup instead of library publication",
    )
    parser.add_argument(
        "--profile",
        choices=("native_standard", "mp3_320", "lossless"),
        help="Expected retained quality; defaults to source-native (lossless for Monochrome)",
    )
    args = parser.parse_args()
    target = TrackInput.model_validate(json.loads(args.track.read_text())).model_dump(
        exclude_none=True
    )
    target["album_artist"] = target["album_artist"] or target["artist"]
    if target["duration"] <= 0 or not target["album"]:
        parser.error("Acceptance needs an expected album and full recording duration")
    report = verify(
        args.source,
        target,
        args.output,
        args.metadata_only,
        args.mix_cache,
        args.profile,
    )
    print(json.dumps(report, indent=2))
    success = (
        report.get("metadata_reachable")
        if args.metadata_only
        else report.get("audio_verified")
    )
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
