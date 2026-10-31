"""Match an Apple search result to the exact MusicBrainz version (#103).

Home's chart matcher prefers the studio recording, which suits a chart.
Search must keep versions apart (PRODUCT.md: live, remix and demo stay
distinct): Apple's title and album say which version it is, and only a
MusicBrainz recording or release group of that same kind matches.
"""

import asyncio
import re
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
from .matching import DISTINCT, EDITION, QUALIFIER, flavour

MATCH_TTL = 7 * 86400
# Release-group types that make every recording on them that kind.
TYPES = {"live": "live", "remix": "remix", "demo": "demo"}
LIVE_ALBUM = re.compile(r"^(?:mtv )?live (?:at|in|from|on)\b")


def split(title):
    """The title without the trailing qualifiers that name an edition
    ("Remastered 2011") or a known kind of version ("Live at …", "Remix"),
    and the kinds those named. Any other qualifier ("Part Two", "Club Mix")
    stays part of the title: it names a different recording (Codex, high)."""
    text, kinds = clean_title(title), set()
    while (found := QUALIFIER.search(text)) and found.start() > 0:
        words = found.group(1) or found.group(2) or ""
        named = flavour(words)
        if not named and not (EDITION.search(words) and not DISTINCT.search(words)):
            break
        kinds |= named
        text = text[: found.start()]
    return normalise(text) or normalise(title), same_kinds(kinds)


def same_kinds(kinds):
    # An unplugged set is a live recording; MusicBrainz says either.
    return frozenset("live" if k == "unplugged" else k for k in kinds)


def base(title):
    return split(title)[0]


def album_kinds(album):
    """Kinds an album's title names in a qualifier ("Album (Live)") or as a
    live album ("Live at …", "MTV Unplugged"), never ordinary words in it
    ("Live Through This" is a studio album; Codex, medium)."""
    kinds = set(split(album)[1])
    words = normalise(album)
    if LIVE_ALBUM.match(words) or "unplugged" in words.split():
        kinds.add("live")
    return frozenset(kinds)


def apple_kinds(title, album=""):
    return split(title)[1] | (album_kinds(album) if album else frozenset())


def group_kinds(group):
    types = {t.lower() for t in group.get("secondary-types") or []}
    return album_kinds(group.get("title", "")) | {TYPES[t] for t in types if t in TYPES}


def recording_kinds(recording):
    kinds = split(recording.get("title", ""))[1] | same_kinds(
        flavour(recording.get("disambiguation", ""))
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
