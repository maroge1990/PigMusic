"""Match an Apple search result to the exact MusicBrainz version (#103).

Home's chart matcher prefers the studio recording, which suits a chart.
Search must keep versions apart (PRODUCT.md: live, remix and demo stay
distinct): Apple's title and album say which version it is, and only a
MusicBrainz recording or release group of that same kind matches.
"""

import asyncio
import time

from .artwork import quoted
from .home import (
    clean_title,
    credit_names,
    first_artist,
    first_credit_artist,
    release_choice,
    studio_rank,
)
from .catalogue import credit
from .library import normalise
from .matching import QUALIFIER, flavour

MATCH_TTL = 7 * 86400
# Release-group types that make every recording on them that kind.
TYPES = {"live": "live", "remix": "remix", "demo": "demo"}


def base(title):
    """The title without trailing qualifiers ("(Live at …)", "- Remix"):
    the version is compared through its kinds instead."""
    text = clean_title(title)
    while found := QUALIFIER.search(text):
        if found.start() == 0:
            break
        text = text[: found.start()]
    return normalise(text) or normalise(title)


def apple_kinds(title, album=""):
    return flavour(title) | flavour(album)


def group_kinds(group):
    types = {t.lower() for t in group.get("secondary-types") or []}
    return flavour(group.get("title", "")) | {TYPES[t] for t in types if t in TYPES}


def recording_kinds(recording):
    kinds = flavour(recording.get("title", "")) | flavour(
        recording.get("disambiguation", "")
    )
    groups = [r.get("release-group", {}) for r in recording.get("releases", [])]
    # Only on live releases: a live recording even when nothing else says so.
    if groups and all("Live" in (g.get("secondary-types") or []) for g in groups):
        kinds |= {"live"}
    return kinds


async def match(store, catalogue, client, kind, item):
    """The MusicBrainz recording ("song") or release group ("album") that is
    the same version as Apple's `item`; None when there is none. Cached a
    week by Apple id, apart from Home's chart matches."""
    key = f"search-match:v1:{kind}:{item['apple_id']}"
    cached = await asyncio.to_thread(store.read_document, key)
    if cached and cached.get("until", 0) > time.time():
        return cached.get("item")
    found = await find(catalogue, client, kind, item, item["artist"])
    if found is None and first_artist(item["artist"]) != item["artist"]:
        found = await find(catalogue, client, kind, item, first_artist(item["artist"]))
    await asyncio.to_thread(
        store.write_document, key, {"until": time.time() + MATCH_TTL, "item": found}
    )
    return found


async def find(catalogue, client, kind, item, artist):
    title = clean_title(item["title"])
    wanted = base(title)
    names = {normalise(artist), normalise(first_artist(artist))} - {""}
    if kind == "song":
        kinds = apple_kinds(item["title"], item.get("album", ""))
        data = await catalogue.get(
            client,
            "recording/",
            query="recording:" + quoted(wanted) + " AND artist:" + quoted(artist),
            limit=25,
        )
        candidates = sorted(
            data.get("recordings", []),
            key=lambda r: (
                not close(r.get("length"), item.get("duration")),
                studio_rank(r),
            ),
        )
        same = recording_kinds
    else:
        kinds = apple_kinds(item["title"])
        data = await catalogue.get(
            client,
            "release-group/",
            query="releasegroup:" + quoted(wanted) + " AND artist:" + quoted(artist),
            limit=25,
        )
        candidates = data.get("release-groups", [])
        same = group_kinds
    for found in candidates:
        credits = found.get("artist-credit", [])
        artist_id, artist_name = first_credit_artist(credits)
        if (
            not artist_id
            or base(found.get("title", "")) != wanted
            or not credit_names(credits) & names
            or same(found) != kinds
        ):
            continue
        result = {
            "id": found["id"],
            "name": found["title"],
            "artist": credit(credits),
            "artist_id": artist_id,
            "artist_name": artist_name,
        }
        if kind == "song":
            release = release_choice(found.get("releases", []))
            result["release_id"] = release["id"] if release else None
            result["album"] = release.get("title", "") if release else ""
        return result
    return None


def close(milliseconds, seconds):
    """Apple's length and MusicBrainz's agree within 5 s (unknown: no)."""
    if not milliseconds or not seconds:
        return False
    return abs(milliseconds / 1000 - seconds) <= 5
