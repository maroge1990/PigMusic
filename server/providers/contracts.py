"""Operation limits and public, redacted provider failures."""

import time
from dataclasses import dataclass, field
from typing import Callable

MESSAGES = {
    "unavailable": "Source temporarily unavailable",
    "access_required": "Source requires access or a refreshed session",
    "protected": "Recording is private, protected, or unavailable",
    "no_match": "No full recording passed identity and quality checks",
    "invalid_response": "Source returned an unsupported response",
    "network_policy": "Source address is outside its allowed network policy",
    "limit": "Source exceeded its operation limits",
    "not_configured": "Required source runtime is not configured",
}


class ProviderError(ValueError):
    def __init__(self, code="unavailable"):
        self.code = code if code in MESSAGES else "unavailable"
        self.transient = self.code in {"unavailable", "limit"}
        super().__init__(MESSAGES[self.code])


@dataclass
class Context:
    profile: str
    check: Callable = lambda: None
    progress: Callable = lambda received, total: None
    deadline: float = field(default_factory=lambda: time.monotonic() + 180)
    max_bytes: int = 160_000_000
    # A listener plays the file while it arrives (the relay, #23): each
    # chunk is flushed for it. Ordinary downloads leave the writes buffered.
    live: bool = False

    def remaining(self):
        self.check()
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ProviderError("limit")
        return remaining
