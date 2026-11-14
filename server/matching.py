"""Indexed music matching shared by every library consumer.

A song is the same song on any edition of any album: a request is satisfied by
the household's copy wherever it sits, unless it explicitly asks for one
edition (`explicit_edition`, from "Other versions"). Version qualifiers that
name different recordings (live, remix, acoustic, demo, ...) keep songs apart.
Explicit requests keep the strict contract: names never override conflicting
release/recording IDs, and multiple candidates are usable only when their
hashes prove the same bytes.
"""

import re
from collections import defaultdict
from dataclasses import dataclass

from .library import identifier, normalise

CONTEXT = ("artist", "title", "album", "album_artist")
IDENTITIES = ("musicbrainz_release_id", "musicbrainz_recording_id")

# A qualifier naming an edition of the same recording ("2011 Remaster",
# "Deluxe Edition", "Bonus Track"). Anything else in brackets names different
# recordings and stays part of the song.
EDITION = re.compile(
    r"\b(deluxe|expanded|edition|remaster(ed)?|anniversary|bonus|special|"
    r"collector'?s|reissue|explicit|clean|mono|stereo|super|album version|"
    r"(19|20)\d\d)\b",
    re.I,
)
DISTINCT = re.compile(
    r"\b(live|acoustic|demos?|remix(es)?|mix|edit|instrumentals?|karaoke|"
    r"sessions?|unplugged|cover|reprise|orchestral|piano)\b",
    re.I,
)
FEATURE = re.compile(r"^\s*(feat\.?|ft\.?|featuring|with)\s", re.I)
QUALIFIER = re.compile(r"\s*(?:[\(\[]([^\)\]]*)[\)\]]|\s-\s([^\(\)\[\]]*))\s*$")
# Songs on two editions of one recording rarely differ by more than this.
SONG_TOLERANCE = 8
# A downloaded file is accepted only this close to the requested length
# (seconds): source selection and validation share it (#139), so no file
# is transferred that validation would then reject.
ACQUISITION_TOLERANCE = 5


def strip_edition(title, keep_years=False):
    """The title without trailing edition qualifiers, punctuation kept:
    "Go Your Own Way - 2004 Remaster" is "Go Your Own Way", "Brat (Deluxe)"
    is "Brat", but "Brat (Live)" stays. Featured-artist credits go too.
    With `keep_years`, a bare year stays: "Weezer (1994)" and "Weezer (2001)"
    are different albums."""
    text = title or ""
    while found := QUALIFIER.search(text):
        qualifier = found.group(1) if found.group(1) is not None else found.group(2)
        if keep_years and re.fullmatch(r"\s*(19|20)\d\d\s*", qualifier):
            break
        if not (
            FEATURE.match(qualifier)
            or EDITION.search(qualifier)
            and not DISTINCT.search(qualifier)
        ):
            break
        text = text[: found.start()]
    return text.strip() or (title or "")


def album_title(title, artist=None):
    """An album title as the catalogue names it: without Apple's " - EP" and
    " - Single" suffixes, a leading "Artist - " and edition qualifiers.
    "The Church - The Best of the Radio Songs" by The Church is "The Best of
    the Radio Songs"; "Paper Planes (Homeland Security Remixes) - EP" keeps
    its remix qualifier."""
    text = re.sub(r"\s+-\s+(EP|Single)\s*$", "", title or "", flags=re.I)
    if artist:
        prefix = re.match(r"\s*(.+?)\s+-\s+(.+)$", text)
        if prefix and normalise(prefix[1]) == normalise(artist):
            text = prefix[2]
    return strip_edition(text) or (title or "")


def title_key(title, keep_years=False):
    """Titles compare without edition qualifiers (see `strip_edition`)."""
    return normalise(strip_edition(title, keep_years)) or normalise(title)


def lead_artist(artist):
    """The lead credit as written: "Coldplay feat. Rihanna" is "Coldplay"."""
    lead = re.split(
        r"\s+(?:feat\.?|ft\.?|featuring|with|&|and|x|vs\.?)\s+|,\s*",
        artist or "",
        maxsplit=1,
        flags=re.I,
    )[0]
    return lead.strip() or (artist or "")


def primary_artist(artist):
    """The lead credit for comparison: "Coldplay & Rihanna" is "coldplay"."""
    return normalise(lead_artist(artist)) or normalise(artist)


def flavour(album_title):
    """Recording kinds an album title names ("Live at Leeds", "Demos"): its
    songs are those recordings even when their own titles say nothing."""
    return frozenset(
        FLAVOURS[word] for word in normalise(album_title).split() if word in FLAVOURS
    )


FLAVOURS = {
    **{w: w for w in ("live", "unplugged", "acoustic", "karaoke")},
    **{w: "demo" for w in ("demo", "demos")},
    **{w: "remix" for w in ("remix", "remixes", "remixed")},
    **{w: "session" for w in ("session", "sessions")},
    **{w: "instrumental" for w in ("instrumental", "instrumentals")},
}


def recording_kind(track):
    """The kinds of recording a song is: named by its album title ("Live at
    Leeds") or known from its release type (`recording_kind`, set when a
    release group is Live, Remix or Demo). Title qualifiers are part of
    `title_key` already."""
    return flavour(track.get("album")) | frozenset(track.get("recording_kind") or ())


def song_key(track):
    """Edition-free identity of a song: lead artist, edition-free title and
    recording kind. JSON-friendly, so queued requests can store it."""
    return (
        primary_artist(track.get("artist")),
        title_key(track.get("title")),
        ",".join(sorted(recording_kind(track))),
    )


def request_intent(payload):
    """What a queued request originally asked for: resolution files it under
    another edition (`requested` keeps the values it replaced)."""
    return {**payload, **(payload.get("requested") or {})}


def compatible(left, right):
    """The one song-compatibility policy for any edition: library matching,
    queue deduplication, duplicate detection and source selection. Same lead
    artist, edition-free title and recording kind; known recording IDs agree;
    lengths agree when both are known."""
    if song_key(left) != song_key(right):
        return False
    a, b = (t.get("musicbrainz_recording_id") for t in (left, right))
    return not (a and b and a != b) and same_length(left, right)


def context(track):
    return tuple(
        normalise(
            track.get(k) or (track.get("artist", "") if k == "album_artist" else "")
        )
        for k in CONTEXT
    )


def same_song(left, right):
    if context(left) != context(right):
        return False
    if any(left.get(k) and right.get(k) and left[k] != right[k] for k in IDENTITIES):
        return False
    return not (
        left.get("duration")
        and right.get("duration")
        and abs(left["duration"] - right["duration"]) > 5
    )


def exclusion_key(track):
    return identifier(
        "excluded-v2", *context(track), *(track.get(k) or "" for k in IDENTITIES)
    )


@dataclass(frozen=True)
class Match:
    # exact/probable: this album; edition: the same song on another edition;
    # conflict: needs review; absent: not in the library.
    kind: str
    track: dict | None = None

    @property
    def status(self):
        return (
            "downloaded"
            if self.track
            else "review" if self.kind == "conflict" else "missing"
        )


class LibraryIndex:
    def __init__(self, library):
        self.by_id = {}
        self.by_context = defaultdict(list)
        self.by_recording = defaultdict(list)
        self.album_by_track = {}
        self.by_song = defaultdict(list)
        self.search_tracks = []
        for album in library["albums"]:
            size = len(album["tracks"])
            for raw in album["tracks"]:
                track = {
                    **raw,
                    "album_id": album["id"],
                    "cover_url": album.get("cover_url"),
                }
                self.by_id[track["id"]] = track
                self.album_by_track[track["id"]] = album["id"]
                self.search_tracks.append(
                    (normalise(track["title"] + " " + track["artist"]), track)
                )
                if track.get("available") is not False:
                    key = context(track)
                    self.by_context[key].append(track)
                    self.by_recording[key[:2]].append(track)
                    filed = {**track, "album": album.get("title") or track.get("album")}
                    self.by_song[song_key(filed)].append((size, album, filed, track))

    def match(self, requested):
        key = context(requested)
        matches = [t for t in self.by_context.get(key, ()) if same_song(t, requested)]
        hashes = {t.get("sha256") for t in matches}
        if len(matches) == 1 or matches and len(hashes) == 1 and None not in hashes:
            track = matches[0]
            known = all(requested.get(k) and track.get(k) for k in IDENTITIES)
            return Match("exact" if known else "probable", track)
        if not requested.get("explicit_edition"):
            found = self.any_edition(requested)
            if found:
                return Match("edition", found)
        return Match("conflict" if self.by_recording.get(key[:2]) else "absent")

    def any_edition(self, requested):
        """The household's copy of this song on any album, preferring the
        requested album's own title, then the artist's own (not compilation)
        albums, then the fullest album, then the oldest."""
        album_title = title_key(requested.get("album"))
        artist = primary_artist(requested.get("artist"))
        candidates = [
            (size, album, track)
            for size, album, filed, track in self.by_song.get(song_key(requested), ())
            if compatible(filed, requested)
        ]
        if not candidates:
            return None

        def rank(item):
            size, album, track = item
            from .library import year_order

            return (
                title_key(album.get("title")) != album_title,
                primary_artist(album.get("artist")) != artist,
                -size,
                year_order(album.get("year")) or 9999,
                track["id"],
            )

        return min(candidates, key=rank)[2]


def same_length(left, right):
    """Two editions of one recording: lengths agree when both are known."""
    a, b = duration_of(left), duration_of(right)
    return not a or not b or abs(a - b) <= SONG_TOLERANCE


def duration_of(track):
    try:
        return float(track.get("duration") or 0)
    except (TypeError, ValueError):
        return 0
