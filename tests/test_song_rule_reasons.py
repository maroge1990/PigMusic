"""The song rule and its reasons are one ordered walk (#282, Codex round 1):
the reason `why` gives is the check that failed. Downloads decide exactly as
before; instant listening differs only in D-130's two ways."""

import itertools

import pytest

from server.downloader import (
    EDITION_WORDS,
    VERSION_WORDS,
    duration,
    folded,
    matches,
    mismatch,
)
from server.library import normalise
from server.matching import (
    ACQUISITION_TOLERANCE,
    flavour,
    primary_artist,
    recording_kind,
    title_key,
)
from server.remote import REASONS, acceptable, altered, why


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


def old_mismatch(target, candidate):
    """`downloader.mismatch` as it was before D-130 (frozen copy)."""
    for key in ("musicbrainz_recording_id", "musicbrainz_release_id"):
        if target.get(key) and candidate.get(key) and target[key] != candidate[key]:
            return "ids"

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
        return "ids"
    strict = target.get("explicit_edition")
    if (
        strict
        and candidate.get("source") == "monochrome"
        and target.get("album")
        and candidate.get("album")
        and folded(target["album"]) != folded(candidate["album"])
    ):
        return "album"
    if strict:
        if folded(candidate.get("channel")) != folded(target["artist"]):
            return "artist"
        if folded(candidate.get("title")) != folded(target["title"]):
            return "title"
    elif primary_artist(candidate.get("channel")) != primary_artist(target["artist"]):
        return "artist"
    elif title_key(candidate.get("title")) != title_key(target["title"]):
        return "title"
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
        return "version"
    expected, actual = duration(target.get("duration")), duration(
        candidate.get("duration")
    )
    if not actual > 0:
        return "no length"
    if expected and not abs(actual - expected) <= ACQUISITION_TOLERANCE:
        return "length"
    return None


def old_why(track, c):
    """`remote.why` as it was before D-130 (frozen copy)."""
    failed = old_mismatch(track, c)
    if failed:
        return failed
    if flavour(c.get("album")) != recording_kind(track):
        return "album kind"
    if altered(c.get("title"), c.get("album")) != altered(
        track.get("title"), track.get("album")
    ):
        return "altered"
    if not (
        (
            not track.get("explicit_edition")
            and track.get("duration")
            and not recording_kind(track)
            and not altered(track.get("title"), track.get("album"))
        )
        or normalise(c.get("album", "")) == normalise(track["album"])
        or bool(track.get("isrcs") and c.get("isrc") and c["isrc"] in track["isrcs"])
    ):
        return "album"
    if not (not track.get("isrcs") or not c.get("isrc") or c["isrc"] in track["isrcs"]):
        return "isrc"
    return "matched"


ISRC = "GBAAA0000001"
# The generated albums D-130 counts as ordinary, listed here rather than read
# from the new code; "Live at Leeds" and "The Remixes" are refused before the
# album check; a lullaby album, a re-recording no request names and no album
# at all are not ordinary.
ORDINARY = {"Album", "Other Album", "Summer Hits 2026"}


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


CHANNELS = ("Artist", "Artist, Guest", "Ärtist", "The Artist")


def candidates(channel):
    for title, album, length, extra in itertools.product(
        ("Song", "Song (Live)", "Song (Slowed)", "Song (2011 Remaster)"),
        (
            "Album",
            "Other Album",
            "Summer Hits 2026",
            "Live at Leeds",
            "The Remixes",
            "Lullaby Versions of Artist",
            "Other Album (Taylor's Version)",
            "",
        ),
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


def intended(track, c, old):
    """D-130's two changes, read from the pair alone, not from the new code:
    with no request length a plain request takes the song from an ordinary
    album; a lead artist differing only by a leading "The" is the artist.
    In this grid the only other-recording word a plain request and its
    matching result can share is the "(Live)" in both titles: such a pair
    needs an identifier in common, or none on either side."""
    if track.get("explicit_edition"):
        return False
    if old == "album":
        asked = {("isrc", code) for code in track.get("isrcs") or []}
        if track.get("musicbrainz_recording_id"):
            asked.add(("rec", track["musicbrainz_recording_id"]))
        offered = {("isrc", code.upper()) for code in c.get("isrcs") or []}
        if c.get("isrc") and not c.get("isrcs"):
            offered.add(("isrc", c["isrc"].upper()))
        if c.get("musicbrainz_recording_id"):
            offered.add(("rec", c["musicbrainz_recording_id"]))
        proven = bool(asked & offered) or not (asked or offered)
        return (
            not track.get("duration")
            and not recording_kind(track)
            and not altered(track.get("title"), track.get("album"))
            and c["album"] in ORDINARY
            and ("(Live)" not in track["title"] or proven)
        )
    return (
        old == "artist"
        and c["channel"] == "The Artist"
        and track["artist"] == "Artist feat. Guest"
    )


@pytest.mark.parametrize("channel", CHANNELS)
def test_downloads_decide_as_before_and_instant_listening_only_as_d130(channel):
    """Every generated pair, 864 requests by 480 results per channel (414,720;
    1,658,880 in all): downloads (`mismatch`, `matches`) give exactly the old
    reason and answer; instant listening (`why`, `acceptable`) changes a
    pair's reason only where D-130 says, and every such pair changes."""
    every = list(candidates(channel))
    compared = changed = accepted = 0
    for track in tracks():
        for c in every:
            assert mismatch(track, c) == old_mismatch(track, c), (track, c)
            assert matches(track, c) == old_matches(track, c), (track, c)
            before, after = old_why(track, c), why(track, c)
            assert (before == "matched") == old_acceptable(track, c), (track, c)
            assert (after == "matched") == acceptable(track, c), (track, c)
            if intended(track, c, before):
                # Waived: the walk goes on to a later check.
                assert REASONS.index(after) > REASONS.index(before), (track, c)
                changed += 1
                accepted += after == "matched"
            else:
                assert after == before, (track, c, before, after)
            compared += 1
    assert compared == 864 * 480
    assert (changed, accepted) == EXPECTED[channel]


# Pairs whose instant-listening reason changed, and of those now accepted.
# "The Artist" changes every pair against "Artist feat. Guest" that reached
# the artist check; most then fail a later check.
EXPECTED = {
    "Artist": (360, 240),
    "Artist, Guest": (360, 240),
    "Ärtist": (360, 240),
    "The Artist": (88_128, 1_179),
}


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
    unknown = {**song, "duration": None}
    assert why(unknown, {**found, "album": "Tribute to Artist"}) == "album"
    assert why(unknown, {**found, "album": "Other"}) == "matched"  # D-130
    assert why(song, {**found, "album": "Live at Leeds"}) == "version"
    assert why(song, {**found, "album": "The Remixes"}) == "album kind"
    assert why({**song, "album": "Session Tapes"}, found) == "album kind"
    assert why(song, {**found, "album": "Slowed Songs"}) == "altered"


def request(artist, title, album, length=None):
    """A tap on a chart song: most have no known length."""
    track = {"artist": artist, "title": title, "album": album}
    if length:
        track["duration"] = length
    return track


def on_jiosaavn(artist, title, album, length):
    slug = folded(title).replace(" ", "-")
    return {
        "channel": artist,
        "title": title,
        "album": album,
        "duration": str(length),  # JioSaavn gives seconds as text.
        "source_url": f"https://aac.saavncdn.com/{slug}_320.mp4",
        "quality": "320kbps",
    }


def on_youtube(artist, title, album, length):
    return {
        "video_id": "abcdefghijk",
        "channel": artist,
        "title": title,
        "album": album,
        "duration": length,
        "explicit": False,
    }


EVERLONG = request("Foo Fighters", "Everlong", "Greatest Hits")
# The first live no-match records (21 Nov): (request, result, reason before).
NOW_PLAYS = [
    (
        EVERLONG,
        on_jiosaavn("Foo Fighters", "Everlong", "The Colour And The Shape", 250),
        "album",
    ),
    (
        EVERLONG,
        on_youtube("Foo Fighters", "Everlong", "The Essential Foo Fighters", 250),
        "album",
    ),
    (
        request("The Killers", "Mr. Brightside", "Direct Hits"),
        on_jiosaavn("The Killers", "Mr. Brightside", "Hot Fuss", 222),
        "album",
    ),
    (
        request("Ella Langley", "Be Her", "Be Her - Single"),
        on_jiosaavn("Ella Langley", "Be Her", "Dandelion", 196),
        "album",
    ),
    (
        request("Teddy Swims", "Lose Control", "Lose Control - Single"),
        on_jiosaavn(
            "Teddy Swims",
            "Lose Control",
            "I've Tried Everything But Therapy (Part 1)",
            210,
        ),
        "album",
    ),
    (
        request("Olivia Dean", "Nice To Each Other", "Nice To Each Other - Single"),
        on_youtube("Olivia Dean", "Nice To Each Other", "The Art of Loving", 183),
        "album",
    ),
    (
        request(
            "Oasis",
            "Wonderwall (Remastered)",
            "(What's the Story) Morning Glory? (Remastered)",
        ),
        on_jiosaavn(
            "Oasis",
            "Wonderwall - Remastered",
            "(What's The Story) Morning Glory? [Deluxe Remastered Edition]",
            258,
        ),
        "album",
    ),
    (
        request("Fleetwood Mac", "Dreams (2004 Remaster)", "Rumours (Super Deluxe)"),
        on_jiosaavn("Fleetwood Mac", "Dreams", "Rumours", 257),
        "album",
    ),
    # Compilations are ordinary albums (D-130: "any ordinary album").
    (
        EVERLONG,
        on_youtube("Foo Fighters", "Everlong", "Best Hockey Songs Ever", 250),
        "album",
    ),
    (
        request("Teddy Swims", "Lose Control", "Lose Control - Single"),
        on_jiosaavn("Teddy Swims", "Lose Control", "Summer Hits 2026", 210),
        "album",
    ),
    # A leading "The", either way round, with or without a known length.
    (
        request("The Goo Goo Dolls", "Iris", "Dizzy Up The Girl", 290),
        on_youtube("Goo Goo Dolls", "Iris", "Dizzy Up The Girl", 289),
        "artist",
    ),
    (
        request("Goo Goo Dolls", "Iris", "Ego, Opinion, Art & Commerce"),
        on_jiosaavn("THE GOO GOO DOLLS", "Iris", "Dizzy Up The Girl", 289),
        "artist",
    ),
    (
        request("The Killers", "Mr. Brightside", "Hot Fuss"),
        on_jiosaavn("Killers", "Mr. Brightside", "Hot Fuss", 222),
        "artist",
    ),
]
STAYS_REFUSED = [
    (
        EVERLONG,
        on_youtube("ZZang KARAOKE", "Everlong", "Everlong (Karaoke Version)", 250),
        "artist",
    ),
    (
        request("The Killers", "Mr. Brightside", "Hot Fuss"),
        on_jiosaavn(
            "String Tribute Players",
            "Mr. Brightside",
            "String Tribute to The Killers",
            223,
        ),
        "artist",
    ),
    (
        request("Vance Joy", "Riptide", "Dream Your Life Away"),
        on_jiosaavn(
            "Party Tyme Karaoke",
            "Riptide (Made Popular By Vance Joy) [Karaoke Version]",
            "Party Tyme Karaoke - Pop Hits",
            204,
        ),
        "artist",
    ),
    (
        EVERLONG,
        on_jiosaavn(
            "Twinkle Twinkle Little Rock Star",
            "Everlong",
            "Lullaby Versions of Foo Fighters",
            240,
        ),
        "artist",
    ),
    (
        request("Zara Larsson", "Lush Life", "So Good"),
        on_jiosaavn("Zara Larsson", "Lush Life", "Lush Life (The Remixes)", 201),
        "album kind",
    ),
    (
        request("Teddy Swims", "Lose Control", "Lose Control - Single"),
        on_youtube("Teddy Swims", "Lose Control (Strings Version)", "", 214),
        "title",
    ),
    (
        EVERLONG,
        on_jiosaavn(
            "Foo Fighters", "Everlong (Acoustic Version)", "Skin And Bones", 268
        ),
        "title",
    ),
    (
        EVERLONG,
        on_youtube("Foo Fighters", "Everlong", "Everlong (Live)", 290),
        "version",
    ),
    (
        EVERLONG,
        on_youtube("Foo Fighters", "Everlong", "Everlong (Slowed + Reverb)", 300),
        "altered",
    ),
    # A known length still decides: Dreams 4:39 is not the 4:17 recording.
    (
        request("Fleetwood Mac", "Dreams (2004 Remaster)", "Rumours", 279),
        on_jiosaavn("Fleetwood Mac", "Dreams", "Rumours (Super Deluxe)", 257),
        "length",
    ),
    # No album named: not an ordinary album (the stricter reading).
    (EVERLONG, on_youtube("Foo Fighters", "Everlong", "", 250), "album"),
    # An explicitly chosen edition keeps its album and exact artist.
    (
        {**EVERLONG, "explicit_edition": True},
        on_jiosaavn("Foo Fighters", "Everlong", "The Colour And The Shape", 250),
        "album",
    ),
    (
        {
            **request("The Goo Goo Dolls", "Iris", "Dizzy Up The Girl"),
            "explicit_edition": True,
        },
        on_jiosaavn("Goo Goo Dolls", "Iris", "Dizzy Up The Girl", 289),
        "artist",
    ),
    # A leading "The" is a whole word, and only a leading one.
    (
        request("Theory of a Deadman", "Bad Girlfriend", "Scars & Souvenirs"),
        on_jiosaavn("ory of a Deadman", "Bad Girlfriend", "Scars & Souvenirs", 205),
        "artist",
    ),
    (
        request("The The", "This Is the Day", "Soul Mining", 300),
        on_jiosaavn("The", "This Is the Day", "Soul Mining", 300),
        "artist",  # "The The" is not "The" (the stricter reading).
    ),
    (
        request("The Band", "The Weight", "Music from Big Pink", 275),
        on_jiosaavn("Band of Horses", "The Weight", "Music from Big Pink", 275),
        "artist",
    ),
    # Codex, D-130 review: a re-recording, and markers in full-width letters
    # or joined by hyphens.
    (
        request("Taylor Swift", "Love Story", "Fearless"),
        on_jiosaavn("Taylor Swift", "Love Story", "Fearless (Taylor's Version)", 235),
        "album",
    ),
    (
        EVERLONG,
        on_jiosaavn("Foo Fighters", "Everlong", "Piano Ｃｏｖｅｒｓ", 250),
        "album",
    ),
    (
        EVERLONG,
        on_youtube(
            "Foo Fighters", "Everlong", "Originally-Performed-by Foo Fighters", 250
        ),
        "album",
    ),
    # Codex, second D-130 review (#308): version evidence on one side only,
    # or only in a title. Taylor's Version asked, the original offered.
    (
        request("Taylor Swift", "Love Story", "Fearless (Taylor's Version)"),
        on_jiosaavn("Taylor Swift", "Love Story", "Fearless", 231),
        "album",
    ),
    (
        request("Taylor Swift", "Love Story (Taylor's Version)", "Fearless"),
        on_youtube("Taylor Swift", "Love Story", "Love Songs", 231),
        "title",
    ),
    # The original asked, Taylor's Version offered (Spanish label; a title
    # whose year qualifier the title check strips).
    (
        request("Taylor Swift", "Love Story", "Fearless"),
        on_jiosaavn("Taylor Swift", "Love Story", "Fearless (Versión de Taylor)", 235),
        "album",
    ),
    (
        request("Taylor Swift", "Love Story", "Fearless"),
        on_youtube(
            "Taylor Swift",
            "Love Story (Taylor's Version - 2021)",
            "Country Collection",
            235,
        ),
        "album",
    ),
    # A piano version on an unrelated piano album, unproven: the request
    # has an ISRC the result does not carry.
    (
        {
            **request(
                "Foo Fighters",
                "Everlong (Piano Version)",
                "Everlong (Piano Version) - Single",
            ),
            "isrcs": ["USRW30700001"],
        },
        on_jiosaavn("Foo Fighters", "Everlong (Piano Version)", "Piano Hits", 262),
        "album",
    ),
]


def test_the_live_no_match_records_now_play_for_instant_listening():
    """D-130 (#282): every one was refused before, for the reason given."""
    for track, c, before in NOW_PLAYS:
        assert old_why(track, c) == before, (track, c)
        assert why(track, c) == "matched", (track, c)
        assert acceptable(track, c)
        # Downloads keep their rule: the album waiver is instant listening
        # only, and the artist check still reads "The".
        if before == "artist":
            assert not matches(track, c)


def test_karaoke_covers_versions_and_known_lengths_stay_refused():
    for track, c, before in STAYS_REFUSED:
        assert why(track, c) == before, (track, c)
        assert old_why(track, c) == before, (track, c)
        assert not acceptable(track, c)


def test_the_same_artist_with_or_without_a_leading_the():
    def same(left, right):
        return (
            why(
                request(left, "Song", "Album", 200),
                on_jiosaavn(right, "Song", "Album", 200),
            )
            == "matched"
        )

    assert same("The Goo Goo Dolls", "Goo Goo Dolls")
    assert same("goo goo dolls", "the goo goo dolls")
    assert same("The The", "The The")
    assert not same("The The", "The")
    assert not same("The", "The The")
    assert same("The Killers feat. Someone", "Killers")
    assert not same("Theory of a Deadman", "ory of a Deadman")
    assert not same("Theory of a Deadman", "Theory")
    assert not same("The Weeknd", "The Weekend")
    assert not same("Thee Oh Sees", "Oh Sees")
    # Codex, D-130 review: only "The" standing as a word in the credit as
    # written; a hyphenated name is another artist.
    assert not same("Dream", "The-Dream")
    assert not same("The-Dream", "Dream")
    assert same("The-Dream", "the-dream")
    assert same("  The\tGoo Goo Dolls", "Goo Goo Dolls")


# Album names that mark another performer's take on the song, each with a
# look-alike ordinary album (D-130): with no request length only the
# ordinary one plays.
NOT_ORDINARY = [
    ("A Tribute to Foo Fighters", "Attributes"),
    ("Rock Tributes Vol. 2", "Tributary"),
    ("Lullaby Versions of Foo Fighters", "Hullabaloo"),
    ("Rockabye Baby! Lullabies", "Rockabye"),
    ("Foo Fighters Covers", "Undercovers"),
    ("Hits Made Popular By Foo Fighters", "Popular by Design"),
    ("Originally Performed By Foo Fighters", "Originally Yours"),
    ("8-Bit Foo Fighters", "808s & Heartbreak"),
    ("8 Bit Rock Hits", "Bit by Bit"),
]


def test_albums_naming_another_performer_are_not_ordinary():
    for marked, plain in NOT_ORDINARY:
        result = on_jiosaavn("Foo Fighters", "Everlong", marked, 250)
        assert why(EVERLONG, result) == "album", marked
        assert why({**EVERLONG, "duration": 250}, result) == "matched", marked
        lookalike = on_jiosaavn("Foo Fighters", "Everlong", plain, 250)
        assert why(EVERLONG, lookalike) == "matched", plain


# Album words naming another recording of the song (Codex, D-130 review),
# each with a look-alike: with no request length an album carrying one the
# request does not name is refused. Words the earlier checks already refuse
# (live, acoustic, demo, remix, karaoke, instrumental, session, unplugged,
# cover, sped up, slowed, nightcore, reverb, 8D) are included to show they
# stay refused; the others were accepted by 1b3a076.
OTHER_RECORDING = [
    ("Fearless (Taylor's Version)", "Taylor Made", True),
    ("Red - Re-Recorded", "Recorded at Abbey Road", True),
    ("The Anniversary Re-Recording", "20th Anniversary Edition", True),
    ("Songs (Stripped)", "Striptease", True),
    ("Summer Hits (Radio Edit)", "Edith", True),
    ("Club Mix 2026", "Mixtape", True),
    ("Everlong (Reprise)", "Enterprise", True),
    ("Orchestral Rock", "Orchestration", True),
    ("Piano Hits", "Pianola", True),
    ("Live in Tokyo", "Alive", False),
    ("Acoustic Hits", "Acoustics", False),
    ("Demos", "Democracy", False),
    ("Remixes", "Remixology", False),
    ("Karaoke Party", "Karaok", False),
    ("Instrumentals", "Instrument", False),
    ("BBC Sessions", "Obsessions", False),
    ("Unplugged", "Plugged In", False),
    ("Cover Me", "Coverage", False),
    ("Sped Up Hits", "Speedway", False),
    ("Slowed Songs", "Slow Songs", False),
    ("Nightcore Mix", "Nightclub", False),
    ("Reverb Hits", "Reverberations", False),
    ("8D Audio", "8 Days", False),
]


def test_albums_naming_another_recording_are_refused_without_a_length():
    for marked, plain, new in OTHER_RECORDING:
        result = on_jiosaavn("Foo Fighters", "Everlong", marked, 250)
        assert why(EVERLONG, result) != "matched", marked
        if new:
            assert why(EVERLONG, result) == "album", marked
        lookalike = on_jiosaavn("Foo Fighters", "Everlong", plain, 250)
        assert why(EVERLONG, lookalike) == "matched", plain
    # A marker the request names itself is no reason to refuse.
    love_story = request("Taylor Swift", "Love Story", "Fearless (Taylor's Version)")
    deluxe = on_jiosaavn(
        "Taylor Swift", "Love Story", "Fearless (Taylor's Version) [Deluxe]", 235
    )
    assert why(love_story, deluxe) == "matched"
    # Edition words stay ordinary (the owner's Wonderwall and Dreams).
    for edition in ("Rumours (Super Deluxe)", "Expanded Edition", "Bonus Tracks"):
        assert why(EVERLONG, on_jiosaavn("Foo Fighters", "Everlong", edition, 250)) == (
            "matched"
        ), edition


def test_a_marked_recording_needs_the_same_words_on_both_sides_and_proof():
    """#308: the request's and the result's raw titles and albums must name
    the same other-recording words; a marked recording must also share an
    identifier, or neither side may have one."""
    asked = request("Taylor Swift", "Love Story", "Fearless (Taylor's Version)")
    spanish = on_jiosaavn(
        "Taylor Swift", "Love Story", "Fearless (Versión de Taylor)", 235
    )
    # Every language's form is one re-recording; no identifiers either side.
    assert why(asked, spanish) == "matched"
    isrc = "USUG12100001"
    # The request has an ISRC: the result must carry it.
    assert why({**asked, "isrcs": [isrc]}, spanish) == "album"
    assert why({**asked, "isrcs": [isrc]}, {**spanish, "isrc": isrc}) == "matched"
    # The result has one the request cannot check: no proof either.
    assert why(asked, {**spanish, "isrc": isrc}) == "album"
    # A piano version proven by its ISRC plays from a piano album; with no
    # identifier on either side the equal words are all there is.
    piano = request("Foo Fighters", "Everlong (Piano Version)", "Everlong - Single")
    hits = on_jiosaavn("Foo Fighters", "Everlong (Piano Version)", "Piano Hits", 262)
    assert why(piano, hits) == "matched"
    assert why({**piano, "isrcs": [isrc]}, {**hits, "isrc": isrc}) == "matched"
    assert why({**piano, "isrcs": [isrc]}, hits) == "album"
    # A piano title on the request only, or on the result only.
    assert why(piano, on_jiosaavn("Foo Fighters", "Everlong", "Hits", 250)) == "title"
    assert why(EVERLONG, hits) == "title"


# Re-recordings named in other languages, with look-alikes that are not.
RERECORDINGS = [
    ("Fearless (Versión de Taylor)", "Versión de lujo"),
    ("Fearless (Version de Taylor)", "Version de luxe"),
    ("Fearless (Versione di Taylor)", "Versione deluxe"),
    ("Fearless (Versão de Taylor)", "Versão de luxo"),
    ("Fearless (Taylor's Version)", "Version Française"),
]


def test_re_recordings_in_other_languages_are_another_recording():
    original = request("Taylor Swift", "Love Story", "Fearless")
    for marked, plain in RERECORDINGS:
        offered = on_jiosaavn("Taylor Swift", "Love Story", marked, 235)
        assert why(original, offered) == "album", marked
        lookalike = on_jiosaavn("Taylor Swift", "Love Story", plain, 235)
        assert why(original, lookalike) == "matched", plain


def test_a_song_named_with_a_version_word_is_not_another_recording():
    """Words of the song's own name mark nothing: "Live Forever" is a song,
    "Piano Man" an album named after it; edition and year tags stay plain."""
    live_forever = request("Oasis", "Live Forever", "Definitely Maybe (Deluxe)")
    best_of = on_jiosaavn("Oasis", "Live Forever", "Time Flies... 1994-2009", 277)
    assert why(live_forever, best_of) == "matched"
    piano_man = request("Billy Joel", "Piano Man", "The Essential Billy Joel")
    assert why(piano_man, on_jiosaavn("Billy Joel", "Piano Man", "Piano Man", 339)) == (
        "matched"
    )
    wonderwall = request("Oasis", "Wonderwall", "Wonderwall - Single")
    remaster = on_youtube("Oasis", "Wonderwall - Remastered 2014", "Hits 1995", 258)
    assert why(wonderwall, remaster) == "matched"
    # A featured credit is no qualifier: "(feat. The Piano Guys)" marks nothing.
    feat = on_jiosaavn("Foo Fighters", "Everlong (feat. The Piano Guys)", "Hits", 250)
    assert why(EVERLONG, feat) == "matched"
