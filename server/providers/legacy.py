"""Run retained HTML/API parsers inside a bounded, guarded search process."""

import json
import sys
import tempfile
import threading
import time
from pathlib import Path
from .contracts import ProviderError
from .process import run_checked

# freemp3cloud silently returns empty pages when searched in parallel or too
# fast: its searches, from every caller (downloads, mixes, the relay), go one
# at a time and FREEMP3CLOUD_GAP seconds apart, and an empty answer is asked
# again once after FREEMP3CLOUD_RETRY_PAUSE (#140).
FREEMP3CLOUD_GAP = 2.5
FREEMP3CLOUD_RETRY_PAUSE = 10.0
_freemp3cloud_lock = threading.Lock()
_freemp3cloud_last = [0.0]


def wait(context, seconds):
    """Sleep `seconds`, giving up as the context says (cancelled, out of
    time)."""
    end = time.monotonic() + seconds
    while (left := end - time.monotonic()) > 0:
        context.remaining()
        time.sleep(min(0.5, left))


def search(source, query, context, limit=20):
    if source != "freemp3cloud":
        return _search(source, query, context, limit)
    for attempt in range(2):
        while not _freemp3cloud_lock.acquire(timeout=0.5):
            context.remaining()
        try:
            wait(context, _freemp3cloud_last[0] + FREEMP3CLOUD_GAP - time.monotonic())
            try:
                found = _search(source, query, context, limit)
            finally:
                _freemp3cloud_last[0] = time.monotonic()
        finally:
            _freemp3cloud_lock.release()
        if found or attempt:
            return found
        wait(context, FREEMP3CLOUD_RETRY_PAUSE)
    return found


def _search(source, query, context, limit=20):
    with tempfile.TemporaryDirectory(prefix=".provider-search-") as directory:
        manifest = Path(directory) / "request.json"
        manifest.write_text(
            json.dumps({"source": source, "query": query, "limit": limit})
        )
        result = run_checked(
            [sys.executable, "-m", "server.providers.legacy_worker", str(manifest)],
            lambda: context.remaining(),
            context.remaining(),
            max_output_bytes=3_000_000,
        )
        try:
            value = json.loads(result.stdout)
        except (ValueError, UnicodeError) as exc:
            raise ProviderError("invalid_response") from exc
        if result.returncode or "error" in value:
            raise ProviderError(value.get("error", "unavailable"))
        return value["candidates"]
