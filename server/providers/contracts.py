"""Operation limits and public, redacted provider failures."""

import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Callable

MESSAGES = {
    "unavailable": "Source temporarily unavailable",
    "access_required": "Source requires access or a refreshed session",
    "protected": "Recording is private, protected, or unavailable",
    "no_match": "No full recording passed identity and quality checks",
    "review_required": "Upload is credited only by its uploader and needs review",
    "invalid_response": "Source returned an unsupported response",
    "network_policy": "Source address is outside its allowed network policy",
    "limit": "Source exceeded its operation limits",
    "not_configured": "Required source runtime is not configured",
}


def review_metadata(fields):
    """What a `review_required` refusal may carry back: the freshly extracted
    title, uploader, album and length, nothing else (no URLs, formats or
    provider data), bounded in size. None when it is not usable."""
    if not isinstance(fields, dict):
        return None
    text = {
        key: fields.get(key)
        for key in ("title", "channel", "album")
        if isinstance(fields.get(key), str)
    }
    try:
        duration = float(fields.get("duration"))
    except (TypeError, ValueError):
        return None
    if not text.get("title") or not text.get("channel") or not 0 < duration <= 86400:
        return None
    return {
        **{key: value.strip()[:300] for key, value in text.items()},
        "duration": duration,
    }


def same_name(left, right):
    """Names equal apart from case, accents, punctuation and spacing."""

    def fold(text):
        return " ".join(
            re.findall(
                r"\w+",
                "".join(
                    c
                    for c in unicodedata.normalize("NFKD", text or "").casefold()
                    if not unicodedata.combining(c)
                ),
            )
        )

    return bool(fold(left)) and fold(left) == fold(right)


class ProviderError(ValueError):
    def __init__(self, code="unavailable", review=None):
        self.code = code if code in MESSAGES else "unavailable"
        self.transient = self.code in {"unavailable", "limit"}
        # Only a review refusal carries metadata, and only the safe subset.
        self.review = (
            review_metadata(review) if self.code == "review_required" else None
        )
        super().__init__(MESSAGES[self.code])


@dataclass
class Context:
    profile: str
    check: Callable = lambda: None
    progress: Callable = lambda received, total: None
    deadline: float = field(default_factory=lambda: time.monotonic() + 180)
    max_bytes: int = 160_000_000

    def remaining(self):
        self.check()
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ProviderError("limit")
        return remaining
