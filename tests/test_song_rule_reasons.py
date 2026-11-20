"""The song rule and its reasons are one ordered walk (#282, Codex round 1):
`acceptable` decides exactly as before, and the reason it gives is the
check that failed."""

import itertools

from server.downloader import (
    EDITION_WORDS,
    VERSION_WORDS,
    duration,
    folded,
    matches,
)
from server.library import normalise
from server.matching import (
    ACQUISITION_TOLERANCE,
    flavour,
    primary_artist,
    recording_kind,
    title_key,
)
from server.remote import acceptable, altered, why


def old_matches(target, candidate):
    """`downloader.matches` as it was before the reasons (frozen copy)."""
    for key in ("musicbrainz_recording_id", "musicbrainz_release_id"):
        if target.get(key) and candidate.get(key) and target[key] != candidate[key]:
            return False

    def isrcs(value):
        raw = value.get("isrcs") or value.get("isrc") or []
        if isinstance(raw, str):
            raw = [raw]
        return {str(code).strip().upper() for code in raw if code}

    requested_isrcs, candidate_isrcs = isrcs(target), isrcs(candidate)
    if (
        requested_isrcs
        and candidate_isrcs
        and requested_isrcs.isdisjoint(candidate_isrcs)
    ):
        return False
    strict = target.get("explicit_edition")
    if (
        strict
        and candidate.get("source") == "monochrome"
        and target.get("album")
        and candidate.get("album")
        and folded(target["album"]) != folded(candidate["album"])
    ):
        return False
    if strict:
        if folded(candidate.get("channel")) != folded(target["artist"]):
            return False
        if folded(candidate.get("title")) != folded(target["title"]):
            return False
    elif primary_artist(candidate.get("channel")) != primary_artist(
        target["artist"]
    ) or title_key(candidate.get("title")) != title_key(target["title"]):
        return False
    versions = VERSION_WORDS if strict else VERSION_WORDS - EDITION_WORDS
    expected_versions = (
        set(folded(target["title"] + " " + target["album"]).split())
        | (set() if strict else set(recording_kind(target)))
    ) & versions
    actual_versions = (
        set(
            folded(
                candidate.get("title", "") + " " + candidate.get("album", "")
            ).split()
        )
        & versions
    )
    if expected_versions != actual_versions:
        return False
    expected, actual = duration(target.get("duration")), duration(
        candidate.get("duration")
    )
    return actual > 0 and (
        not expected or abs(actual - expected) <= ACQUISITION_TOLERANCE
    )


def old_acceptable(track, c):
    """`remote.acceptable` as it was before the reasons (frozen copy)."""
    return bool(
        old_matches(track, c)
        and flavour(c.get("album")) == recording_kind(track)
        and altered(c.get("title"), c.get("album"))
        == altered(track.get("title"), track.get("album"))
        and (
            (
                not track.get("explicit_edition")
                and track.get("duration")
                and not recording_kind(track)
                and not altered(track.get("title"), track.get("album"))
            )
            or normalise(c.get("album", "")) == normalise(track["album"])
            or bool(
                track.get("isrcs") and c.get("isrc") and c["isrc"] in track["isrcs"]
            )
        )
        and (not track.get("isrcs") or not c.get("isrc") or c["isrc"] in track["isrcs"])
    )


ISRC = "GBAAA0000001"


def tracks():
    for (
        artist,
        title,
        album,
        length,
        explicit,
        isrcs,
        kind,
        recording,
    ) in itertools.product(
        ("Artist feat. Guest", "Ärtist"),
        ("Song", "Song (Live)", "Song (Slowed) - 2011 Remaster"),
        ("Album", "Live at Leeds", "Album - Single"),
        (200, None, "nan"),
        (False, True),
        (None, [ISRC]),
        (None, ["remix"]),
        (None, "rec-1"),
    ):
        track = {"artist": artist, "title": title, "album": album}
        if length is not None:
            track["duration"] = length
        if explicit:
            track["explicit_edition"] = True
        if isrcs:
            track["isrcs"] = isrcs
        if kind:
            track["recording_kind"] = kind
        if recording:
            track["musicbrainz_recording_id"] = recording
        yield track


def candidates():
    for channel, title, album, length, extra in itertools.product(
        ("Artist", "Artist, Guest", "Ärtist"),
        ("Song", "Song (Live)", "Song (Slowed)", "Song (2011 Remaster)"),
        ("Album", "Other Album", "Live at Leeds", "The Remixes"),
        (201, "3:26", "nan"),
        (
            {},
            {"isrc": ISRC},
            {"isrc": ISRC.lower(), "musicbrainz_recording_id": "rec-1"},
            {"isrc": "GBAAA0000002", "musicbrainz_recording_id": "rec-2"},
            {"isrcs": [ISRC], "isrc": "GBAAA0000009", "source": "monochrome"},
        ),
    ):
        yield {
            "channel": channel,
            "title": title,
            "album": album,
            "duration": length,
            **extra,
        }


def test_acceptable_decides_as_before_and_the_reason_agrees():
    every = list(candidates())
    decided = 0
    for track in tracks():
        for c in every:
            before = old_acceptable(track, c)
            assert acceptable(track, c) == before, (track, c)
            assert matches(track, c) == old_matches(track, c), (track, c)
            assert (why(track, c) == "matched") == before, (track, c)
            decided += 1
    assert decided > 500_000  # Codex compared 518,400.


def test_each_reason_is_the_check_that_failed():
    song = {"artist": "Artist", "title": "Song", "album": "Album", "duration": 200}
    found = {"channel": "Artist", "title": "Song", "album": "Album", "duration": 201}
    with_isrc = {**song, "isrcs": [ISRC]}
    # matches() reads the ISRC case-folded and passes it; the final check
    # compares it as given: an ISRC rejection, not an album one.
    assert why(with_isrc, {**found, "isrc": ISRC.lower()}) == "isrc"
    assert why(with_isrc, {**found, "isrcs": [ISRC], "isrc": "GBAAA0000009"}) == "isrc"
    assert why(with_isrc, {**found, "isrc": "GBAAA0000002"}) == "ids"
    assert why(with_isrc, {**found, "isrc": ISRC}) == "matched"
    assert why(song, {**found, "duration": 205}) == "matched"  # 5 s: the limit.
    assert why(song, {**found, "duration": 205.5}) == "length"
    assert why(song, {**found, "duration": "nan"}) == "no length"
    assert why({**song, "duration": "nan"}, found) == "length"
    assert why({**song, "duration": None}, {**found, "album": "Other"}) == "album"
    assert why(song, {**found, "album": "Live at Leeds"}) == "version"
    assert why(song, {**found, "album": "The Remixes"}) == "album kind"
    assert why({**song, "album": "Session Tapes"}, found) == "album kind"
    assert why(song, {**found, "album": "Slowed Songs"}) == "altered"
