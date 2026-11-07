"""Thread-safe, ephemeral provider circuit breakers with redacted diagnostics."""

import logging
import threading
import time
from contextlib import contextmanager

from .contracts import ProviderError

# Failures that prove the source responded, unlike outages or policy blocks.
ANSWERED = {
    "no_match",
    "review_required",
    "protected",
    "access_required",
    "invalid_response",
}


log = logging.getLogger("pig_music.downloads")


class Health:
    def __init__(self, clock=time.time):
        self.clock = clock
        self.lock = threading.RLock()
        self.records = {}

    def record(self, source):
        return self.records.setdefault(
            source,
            {
                "failures": 0,
                "transfer_failures": 0,
                "strikes": 0,
                "retry_at": 0,
                "probe": False,
                "metadata_reachable": None,
                "last_transfer": None,
                "last_transfer_at": None,
                "reason": None,
            },
        )

    @contextmanager
    def operation(self, source):
        with self.lock:
            record = self.record(source)
            if record["retry_at"]:
                if record["retry_at"] > self.clock() or record["probe"]:
                    raise ProviderError("unavailable")
                record["probe"] = True
        try:
            yield
        finally:
            with self.lock:
                self.record(source)["probe"] = False

    def outcome(self, source, *, error=None, transfer=False):
        with self.lock:
            was = self.record(source)["retry_at"]
            self._outcome(source, error, transfer)
            now = self.record(source)["retry_at"]
        # One line when a source's breaker opens or closes (#134).
        if now and now != was:
            log.warning(
                "Source %s paused for %.0f s after repeated failures (%s)",
                source,
                now - self.clock(),
                error,
            )
        elif was and not now:
            log.info("Source %s available again", source)

    def _outcome(self, source, error, transfer):
        record = self.record(source)
        if transfer:
            record["last_transfer"] = "failed" if error else "verified"
            record["last_transfer_at"] = int(self.clock())
        elif not error:
            record["metadata_reachable"] = True
        if error and error.code in ANSWERED:
            # The source answered (identity, protection or access result):
            # it is reachable, so an expired cooldown probe must not linger
            # and block concurrent operations.
            record["reason"] = str(error)
            if not transfer:
                record.update(failures=0, retry_at=0)
                record["metadata_reachable"] = True
            elif record["retry_at"] and record["retry_at"] <= self.clock():
                record.update(transfer_failures=0, retry_at=0)
        elif error:
            record["reason"] = str(error)
            if error.transient:
                key = "transfer_failures" if transfer else "failures"
                record[key] += 1
                if record[key] >= 3 or record["retry_at"]:
                    delay = min(300 * 2 ** min(record["strikes"], 4), 3600)
                    record.update(
                        retry_at=self.clock() + delay,
                        **{key: 0},
                        strikes=record["strikes"] + 1,
                    )
                    if not transfer:
                        record["metadata_reachable"] = False
        else:
            if transfer:
                record.update(
                    failures=0,
                    transfer_failures=0,
                    strikes=0,
                    retry_at=0,
                    reason=None,
                )
            else:
                record.update(failures=0, retry_at=0)
                if not record["transfer_failures"]:
                    record.update(reason=None)

    def snapshot(self, source):
        with self.lock:
            record = self.record(source)
            return {
                k: record[k]
                for k in (
                    "metadata_reachable",
                    "last_transfer",
                    "last_transfer_at",
                    "reason",
                )
            } | {"next_retry_at": int(record["retry_at"]) or None}
