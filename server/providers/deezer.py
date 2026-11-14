"""Bounded recording metadata lookup; never substitutes conflicting ISRCs."""

import re
import time
from urllib.parse import urlencode
from .contracts import Context, ProviderError
from .network import get_json


def identify(target, context):
    from ..downloader import matches, folded

    # Context-free title matches are insufficient recording evidence.
    if not target.get("album") or not target.get("duration"):
        return set()
    lookup = Context(
        context.profile,
        check=context.check,
        deadline=min(context.deadline, time.monotonic() + 20),
    )
    try:
        body = get_json(
            "https://api.deezer.com/search?"
            + urlencode(
                {
                    # Deezer answers nothing to artist:"..." track:"..."
                    # (Nov 2026); free text, matched below, still works.
                    "q": f"{target['artist']} {target['title']}",
                    "limit": 10,
                }
            ),
            ("deezer.com",),
            lookup,
        )
        if not isinstance(body, dict):
            return set()
        for raw in body.get("data", [])[:10]:
            if not isinstance(raw, dict):
                continue
            candidate = {
                "channel": (raw.get("artist") or {}).get("name", ""),
                "title": raw.get("title", ""),
                "album": (raw.get("album") or {}).get("title", ""),
                "duration": raw.get("duration", 0),
            }
            if not matches(target, candidate) or folded(candidate["album"]) != folded(
                target["album"]
            ):
                continue
            identity = str(raw.get("id") or "")
            if not re.fullmatch(r"\d{1,30}", identity):
                continue
            details = get_json(
                "https://api.deezer.com/track/" + identity, ("deezer.com",), lookup
            )
            code = (
                str(details.get("isrc") or "").upper()
                if isinstance(details, dict)
                else ""
            )
            if re.fullmatch(r"[A-Z]{2}[A-Z0-9]{3}\d{7}", code):
                return {code}
    except ProviderError:
        # Optional evidence lookup cannot make a reachable Monochrome catalogue
        # unavailable. Parent cancellation/deadline exceptions still propagate.
        return set()
    return set()
