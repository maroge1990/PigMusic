"""Disposable HTTPS PigMusic with a realistic library, for photographing screens.

The big sibling of scripts/serve_ui_test_server.py: the same address
(https://127.0.0.1:8790), certificate authority (`--tls`) and account file
(`--account`), but a library of well-known albums (audio is short FFmpeg
tones, never music) and an account with favourites, playlists, two months of
listening, Home mixes and a Downloads list in every state. Metadata providers
are allowed, so real covers, artist photos, catalogue pages and charts load;
only public artist, album and song names leave the machine. Downloads stay
off. Everything lives in a temporary folder deleted on exit. Needs FFmpeg and
openssl.

    python -m scripts.serve_showcase_server [--tls DIR] [--account FILE] [--check]

`--check` builds everything, prints a JSON summary and serves nothing.
"""

import argparse
import asyncio
import json
import os
import random
import secrets
import subprocess
import tempfile
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

from scripts.serve_ui_test_server import ORIGIN, PORT, ROOT, certificates

USERNAME, DISPLAY_NAME = "showcase", "Alex Morgan"
MEMBER, MEMBER_NAME = "sam", "Sam Morgan"
# The library states real lengths (the server shows the snapshot's duration,
# never the file's), so each tone is only this long.
TONE_SECONDS = 15

# Albums: artist, title, year, genre, days since it was added, then one list
# of songs per disc. A song is (title, length) or (title, length, performer).
LIBRARY = (
    (
        "Coldplay",
        "Parachutes",
        2000,
        "Alternative",
        410,
        [
            [
                ("Don't Panic", "2:17"),
                ("Shiver", "4:59"),
                ("Spies", "5:18"),
                ("Sparks", "3:47"),
                ("Yellow", "4:29"),
                ("Trouble", "4:30"),
                ("Parachutes", "0:46"),
                ("High Speed", "4:14"),
                ("We Never Change", "4:09"),
                ("Everything's Not Lost", "7:15"),
            ]
        ],
    ),
    (
        "Coldplay",
        "A Rush of Blood to the Head",
        2002,
        "Alternative",
        410,
        [
            [
                ("Politik", "5:18"),
                ("In My Place", "3:48"),
                ("God Put a Smile upon Your Face", "4:57"),
                ("The Scientist", "5:09"),
                ("Clocks", "5:07"),
                ("Daylight", "5:27"),
                ("Green Eyes", "3:43"),
                ("Warning Sign", "5:31"),
                ("A Whisper", "3:58"),
                ("A Rush of Blood to the Head", "5:51"),
                ("Amsterdam", "5:19"),
            ]
        ],
    ),
    (
        "Coldplay",
        "X&Y",
        2005,
        "Alternative",
        380,
        [
            [
                ("Square One", "4:47"),
                ("What If", "4:57"),
                ("White Shadows", "5:28"),
                ("Fix You", "4:54"),
                ("Talk", "5:11"),
                ("X&Y", "4:34"),
                ("Speed of Sound", "4:48"),
                ("A Message", "4:45"),
                ("Low", "5:32"),
                ("The Hardest Part", "4:25"),
                ("Swallowed in the Sea", "3:58"),
                ("Twisted Logic", "5:01"),
                ("Til Kingdom Come", "4:10"),
            ]
        ],
    ),
    (
        "Coldplay",
        "Viva la Vida or Death and All His Friends",
        2008,
        "Alternative",
        380,
        [
            [
                ("Life in Technicolor", "2:29"),
                ("Cemeteries of London", "3:21"),
                ("Lost!", "3:55"),
                ("42", "3:57"),
                ("Lovers in Japan / Reign of Love", "6:51"),
                ("Yes", "7:06"),
                ("Viva la Vida", "4:01"),
                ("Violet Hill", "3:42"),
                ("Strawberry Swing", "4:09"),
                ("Death and All His Friends", "6:18"),
            ]
        ],
    ),
    (
        "Fleetwood Mac",
        "Rumours",
        1977,
        "Rock",
        300,
        [
            [
                ("Second Hand News", "2:56"),
                ("Dreams", "4:14"),
                ("Never Going Back Again", "2:14"),
                ("Don't Stop", "3:13"),
                ("Go Your Own Way", "3:43"),
                ("Songbird", "3:20"),
                ("The Chain", "4:30"),
                ("You Make Loving Fun", "3:31"),
                ("I Don't Want to Know", "3:15"),
                ("Oh Daddy", "3:56"),
                ("Gold Dust Woman", "4:56"),
            ]
        ],
    ),
    (
        "Pink Floyd",
        "The Wall",
        1979,
        "Rock",
        250,
        [
            [
                ("In the Flesh?", "3:19"),
                ("The Thin Ice", "2:27"),
                ("Another Brick in the Wall, Part 1", "3:11"),
                ("The Happiest Days of Our Lives", "1:46"),
                ("Another Brick in the Wall, Part 2", "3:59"),
                ("Mother", "5:32"),
                ("Goodbye Blue Sky", "2:45"),
                ("Empty Spaces", "2:10"),
                ("Young Lust", "3:25"),
                ("One of My Turns", "3:41"),
                ("Don't Leave Me Now", "4:08"),
                ("Another Brick in the Wall, Part 3", "1:18"),
                ("Goodbye Cruel World", "0:48"),
            ],
            [
                ("Hey You", "4:40"),
                ("Is There Anybody Out There?", "2:44"),
                ("Nobody Home", "3:26"),
                ("Vera", "1:35"),
                ("Bring the Boys Back Home", "1:21"),
                ("Comfortably Numb", "6:23"),
                ("The Show Must Go On", "1:36"),
                ("In the Flesh", "4:15"),
                ("Run Like Hell", "4:20"),
                ("Waiting for the Worms", "4:04"),
                ("Stop", "0:30"),
                ("The Trial", "5:13"),
                ("Outside the Wall", "1:41"),
            ],
        ],
    ),
    (
        "Daft Punk",
        "Random Access Memories",
        2013,
        "Electronic",
        200,
        [
            [
                ("Give Life Back to Music", "4:35"),
                ("The Game of Love", "5:22"),
                ("Giorgio by Moroder", "9:04"),
                ("Within", "3:48"),
                ("Instant Crush", "5:37"),
                ("Lose Yourself to Dance", "5:53"),
                ("Touch", "8:18"),
                ("Get Lucky", "6:09"),
                ("Beyond", "4:50"),
                ("Motherboard", "5:41"),
                ("Fragments of Time", "4:39"),
                ("Doin' It Right", "4:11"),
                ("Contact", "6:21"),
            ]
        ],
    ),
    (
        "Adele",
        "21",
        2011,
        "Pop",
        180,
        [
            [
                ("Rolling in the Deep", "3:48"),
                ("Rumour Has It", "3:43"),
                ("Turning Tables", "4:10"),
                ("Don't You Remember", "4:03"),
                ("Set Fire to the Rain", "4:02"),
                ("He Won't Go", "4:38"),
                ("Take It All", "3:48"),
                ("I'll Be Waiting", "4:01"),
                ("One and Only", "5:48"),
                ("Lovesong", "5:16"),
                ("Someone Like You", "4:45"),
            ]
        ],
    ),
    (
        "Kendrick Lamar",
        "DAMN.",
        2017,
        "Hip-Hop",
        150,
        [
            [
                ("BLOOD.", "1:58"),
                ("DNA.", "3:05"),
                ("YAH.", "2:40"),
                ("ELEMENT.", "3:28"),
                ("FEEL.", "3:34"),
                ("LOYALTY.", "3:47"),
                ("PRIDE.", "4:35"),
                ("HUMBLE.", "2:57"),
                ("LUST.", "5:07"),
                ("LOVE.", "3:33"),
                ("XXX.", "4:14"),
                ("FEAR.", "7:40"),
                ("GOD.", "4:08"),
                ("DUCKWORTH.", "4:08"),
            ]
        ],
    ),
    (
        "Miles Davis",
        "Kind of Blue",
        1959,
        "Jazz",
        140,
        [
            [
                ("So What", "9:22"),
                ("Freddie Freeloader", "9:46"),
                ("Blue in Green", "5:37"),
                ("All Blues", "11:33"),
                ("Flamenco Sketches", "9:26"),
            ]
        ],
    ),
    (
        "Amy Winehouse",
        "Back to Black",
        2006,
        "Soul",
        120,
        [
            [
                ("Rehab", "3:34"),
                ("You Know I'm No Good", "4:17"),
                ("Me & Mr Jones", "2:33"),
                ("Just Friends", "3:13"),
                ("Back to Black", "4:01"),
                ("Love Is a Losing Game", "2:35"),
                ("Tears Dry on Their Own", "3:06"),
                ("Wake Up Alone", "3:42"),
                ("Some Unholy War", "2:22"),
                ("He Can Only Hold Her", "2:46"),
                ("Addicted", "2:45"),
            ]
        ],
    ),
    (
        "Tame Impala",
        "Currents",
        2015,
        "Alternative",
        90,
        [
            [
                ("Let It Happen", "7:47"),
                ("Nangs", "1:47"),
                ("The Moment", "4:15"),
                ("Yes I'm Changing", "4:30"),
                ("Eventually", "5:18"),
                ("Gossip", "0:55"),
                ("The Less I Know the Better", "3:36"),
                ("Past Life", "3:48"),
                ("Disciples", "1:48"),
                ("'Cause I'm a Man", "4:01"),
                ("Reality in Motion", "4:12"),
                ("Love/Paranoia", "3:05"),
                ("New Person, Same Old Mistakes", "6:03"),
            ]
        ],
    ),
    (
        "Fiona Apple",
        "The Idler Wheel Is Wiser Than the Driver of the Screw and Whipping Cords"
        " Will Serve You More Than Ropes Will Ever Do",
        2012,
        "Alternative",
        75,
        [
            [
                ("Every Single Night", "3:33"),
                ("Daredevil", "3:28"),
                ("Valentine", "3:32"),
                ("Jonathan", "5:03"),
                ("Left Alone", "4:53"),
                ("Werewolf", "3:12"),
                ("Periphery", "4:58"),
                ("Regret", "5:51"),
                ("Anything We Want", "4:40"),
                ("Hot Knife", "4:02"),
            ]
        ],
    ),
    (
        "Hans Zimmer",
        "Interstellar (Original Motion Picture Soundtrack)",
        2014,
        "Soundtrack",
        60,
        [
            [
                ("Dreaming of the Crash", "3:55"),
                ("Cornfield Chase", "2:06"),
                ("Dust", "5:41"),
                ("Day One", "3:19"),
                ("Stay", "6:52"),
                ("Message from Home", "1:40"),
                ("The Wormhole", "1:30"),
                ("Mountains", "3:39"),
                ("Afraid of Time", "2:32"),
                ("A Place Among the Stars", "3:27"),
            ]
        ],
    ),
    (
        "Stephen Schwartz",
        "Wicked (Original Broadway Cast Recording)",
        2003,
        "Musical",
        45,
        [
            [
                ("No One Mourns the Wicked", "6:40"),
                ("Dear Old Shiz", "1:26"),
                ("The Wizard and I", "5:09"),
                ("What Is This Feeling?", "3:32"),
                ("Something Bad", "1:39"),
                ("Dancing Through Life", "7:37"),
                ("Popular", "3:44"),
                ("I'm Not That Girl", "2:58"),
                ("One Short Day", "3:03"),
                ("A Sentimental Man", "1:15"),
                ("Defying Gravity", "5:53"),
                ("Thank Goodness", "6:22"),
                ("Wonderful", "4:56"),
                ("As Long as You're Mine", "3:47"),
                ("No Good Deed", "3:32"),
                ("March of the Witch Hunters", "1:31"),
                ("For Good", "5:07"),
            ]
        ],
    ),
    (
        "Billie Eilish",
        "When We All Fall Asleep, Where Do We Go?",
        2019,
        "Pop",
        24,
        [
            [
                ("bad guy", "3:14"),
                ("xanny", "4:04"),
                ("you should see me in a crown", "3:01"),
                ("all the good girls go to hell", "2:49"),
                ("wish you were gay", "3:42"),
                ("when the party's over", "3:16"),
                ("8", "2:53"),
                ("my strange addiction", "3:00"),
                ("bury a friend", "3:13"),
                ("ilomilo", "2:36"),
                ("listen before i go", "4:03"),
                ("i love you", "4:52"),
                ("goodbye", "1:59"),
            ]
        ],
    ),
    (
        "Various Artists",
        "Guardians of the Galaxy: Awesome Mix Vol. 1 (Original Motion Picture"
        " Soundtrack)",
        2014,
        "Soundtrack",
        18,
        [
            [
                ("Hooked on a Feeling", "2:52", "Blue Swede"),
                ("Go All the Way", "3:21", "Raspberries"),
                ("Spirit in the Sky", "4:02", "Norman Greenbaum"),
                ("Moonage Daydream", "4:41", "David Bowie"),
                ("Fooled Around and Fell in Love", "4:35", "Elvin Bishop"),
                ("I'm Not in Love", "6:03", "10cc"),
                ("I Want You Back", "2:58", "The Jackson 5"),
                ("Come and Get Your Love", "3:26", "Redbone"),
                ("Cherry Bomb", "2:17", "The Runaways"),
                ("Escape (The Piña Colada Song)", "4:36", "Rupert Holmes"),
                ("O-o-h Child", "3:13", "The Five Stairsteps"),
                (
                    "Ain't No Mountain High Enough",
                    "2:29",
                    "Marvin Gaye & Tammi Terrell",
                ),
            ]
        ],
    ),
    (
        "Various Artists",
        "Now That's What I Call Music!",
        1983,
        "Pop",
        10,
        [
            [
                ("You Can't Hurry Love", "2:55", "Phil Collins"),
                ("Is There Something I Should Know?", "4:07", "Duran Duran"),
                ("Red Red Wine", "3:01", "UB40"),
                ("Temptation", "3:28", "Heaven 17"),
                ("Give It Up", "4:12", "KC and the Sunshine Band"),
                ("Total Eclipse of the Heart", "4:29", "Bonnie Tyler"),
                ("Karma Chameleon", "4:01", "Culture Club"),
                ("The Safety Dance", "2:44", "Men Without Hats"),
                ("Too Shy", "3:43", "Kajagoogoo"),
                ("Moonlight Shadow", "3:36", "Mike Oldfield"),
                ("Down Under", "3:42", "Men at Work"),
                ("Let's Stay Together", "3:38", "Tina Turner"),
            ]
        ],
    ),
    # Singles: one song, filed under its own title.
    (
        "The Weeknd",
        "Blinding Lights",
        2019,
        "Pop",
        7,
        [[("Blinding Lights", "3:20")]],
    ),
    ("Dua Lipa", "Houdini", 2023, "Pop", 5, [[("Houdini", "3:05")]]),
    ("Harry Styles", "As It Was", 2022, "Pop", 3, [[("As It Was", "2:47")]]),
    (
        "Panic! at the Disco",
        "The Only Difference Between Martyrdom and Suicide Is Press Coverage",
        2006,
        "Alternative",
        2,
        [
            [
                (
                    "The Only Difference Between Martyrdom and Suicide Is Press"
                    " Coverage",
                    "3:07",
                )
            ]
        ],
    ),
)

# Listening leans on these: clear top artists and albums in Rewind.
LOVED = {"Coldplay": 5, "Fleetwood Mac": 4, "Daft Punk": 3, "Tame Impala": 3}
PLAYS, HISTORY_DAYS, FAVOURITES = 560, 60, 25
# Name, then how many songs.
PLAYLISTS = (
    ("Road Trip", 40),
    ("Sunday Morning", 12),
    ("Kitchen Disco", 9),
    (
        "Songs for long drives up the coast when nobody can agree on what to"
        " play next (2019 to 2026)",
        14,
    ),
    ("Dinner Party (work in progress)", 0),
)
# The member's songs, by album artist: enough performers for their own mixes
# (a mix takes two songs from each).
MEMBER_ARTISTS = ("Fleetwood Mac", "Adele", "Amy Winehouse", "Various Artists")
MEMBER_PLAYLIST = ("Sam's Commute", 8)

# Downloads: artist, album (None for a song asked for alone), year, days ago,
# then (title, length, state) per song. None of these is in the library.
STEPS = [
    {
        "step": "search",
        "source": "soulseek",
        "profile": "mp3_320",
        "candidates": 4,
        "found": [{"format": "MP3 320", "mb": 9}],
        "seconds": 6.4,
    },
    {"step": "transfer", "source": "soulseek", "bytes": 9_400_000, "seconds": 21.0},
    {"step": "validate", "profile": "mp3_320", "seconds": 1.2},
    {"step": "tag", "seconds": 0.3},
]
FAILED_STEPS = [
    {
        "step": "search",
        "source": "soulseek",
        "profile": "mp3_320",
        "candidates": 0,
        "seconds": 30.0,
    }
]
REQUESTS = (
    (
        "Lorde",
        "Melodrama",
        2017,
        0,
        [
            ("Green Light", "3:54", "completed"),
            ("Sober", "3:17", "completed"),
            ("Homemade Dynamite", "3:09", "downloading:0.62"),
            ("The Louvre", "4:31", "searching"),
            ("Liability", "2:52", "waiting"),
            ("Hard Feelings/Loveless", "6:07", "waiting"),
        ],
    ),
    (
        "Radiohead",
        "OK Computer",
        1997,
        0,
        [
            ("Airbag", "4:44", "waiting"),
            ("Paranoid Android", "6:23", "waiting"),
            ("Subterranean Homesick Alien", "4:27", "waiting"),
            ("Exit Music (For a Film)", "4:24", "waiting"),
            ("Let Down", "4:59", "waiting"),
        ],
    ),
    (
        "Frank Ocean",
        "Blonde",
        2016,
        1,
        [
            ("Nikes", "5:14", "paused"),
            ("Ivy", "4:09", "paused"),
            ("Pink + White", "3:04", "paused"),
            ("Solo", "4:17", "paused"),
        ],
    ),
    (
        "Beyoncé",
        "Lemonade",
        2016,
        2,
        [
            ("Pray You Catch Me", "3:16", "completed"),
            ("Hold Up", "3:41", "completed"),
            ("Don't Hurt Yourself", "3:53", "failed"),
            ("Sorry", "3:52", "completed"),
        ],
    ),
    (
        "Lana Del Rey",
        "Born to Die",
        2012,
        4,
        [
            ("Born to Die", "4:46", "completed"),
            ("Off to the Races", "5:00", "completed"),
            ("Blue Jeans", "3:30", "completed"),
            ("Video Games", "4:42", "completed"),
            ("Diet Mountain Dew", "3:43", "completed"),
        ],
    ),
    ("Glass Animals", None, 2020, 0, [("Heat Waves", "3:58", "downloading:0.35")]),
    ("Sia", None, 2014, 0, [("Chandelier", "3:36", "waiting")]),
    ("Lizzo", None, 2019, 1, [("Juice", "3:15", "paused")]),
    ("Hozier", None, 2013, 3, [("Take Me to Church", "4:02", "failed")]),
    ("Olivia Rodrigo", None, 2021, 6, [("drivers license", "4:02", "completed")]),
)


def seconds(length: str) -> int:
    minutes, rest = length.split(":")
    return int(minutes) * 60 + int(rest)


def plan() -> list[dict]:
    """The library's songs as snapshot tracks; no file is touched."""
    tracks = []
    for index, (artist, album, year, genre, added, discs) in enumerate(LIBRARY):
        for disc, songs in enumerate(discs, start=1):
            for number, (title, length, *performer) in enumerate(songs, start=1):
                identity = f"showcase-{index:02d}-{disc}-{number:02d}"
                tracks.append(
                    {
                        "id": identity,
                        "title": title,
                        "artist": performer[0] if performer else artist,
                        "album_artist": artist,
                        "album": album,
                        "genre": genre,
                        "compilation": artist == "Various Artists",
                        "year": year,
                        "track_number": number,
                        "disc_number": disc,
                        "duration": seconds(length),
                        "codec": "flac",
                        "available": True,
                        "relative_path": identity + ".flac",
                        "added_days_ago": added,
                    }
                )
    return tracks


def request_plan() -> list[tuple[list[dict], list[str], int]]:
    """Each request as the songs asked for, their states and its age in days."""
    result = []
    for artist, album, year, days, songs in REQUESTS:
        tracks = [
            {
                "title": title,
                "artist": artist,
                "album": album or title,
                "album_artist": artist,
                "year": str(year),
                "disc_number": 1,
                "track_number": number,
                "duration": seconds(length),
            }
            for number, (title, length, _) in enumerate(songs, start=1)
        ]
        result.append((tracks, [state for _, _, state in songs], days))
    return result


def tones(root: Path, tracks: list[dict]):
    """One short FLAC tone per song, each at its own pitch."""

    def tone(item):
        position, track = item
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi"]
            + ["-i", f"sine=frequency={180 + 3 * position}:duration={TONE_SECONDS}"]
            + ["-ac", "2", "-ar", "44100", str(root / track["relative_path"])],
            check=True,
        )

    with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 2)) as pool:
        list(pool.map(tone, enumerate(tracks)))


def song(track: dict) -> dict:
    """A library song as playlists, favourites and requests name it."""
    return {
        "title": track["title"],
        "artist": track["artist"],
        "album": track["album"],
        "album_artist": track["album_artist"],
        "year": str(track["year"]),
        "disc_number": track["disc_number"],
        "track_number": track["track_number"],
        "duration": track["duration"],
    }


def listens(tracks: list[dict], now: int, week: int, rng) -> list[dict]:
    """Two months of plays, most of them of a few loved songs, with enough in
    the week so far (since `week`) for Rewind's shortest period."""
    pool = rng.sample(tracks, 120)
    weights = [
        LOVED.get(track["album_artist"], 1) / (position + 4) ** 0.8
        for position, track in enumerate(pool)
    ]
    events = []
    for number, track in enumerate(rng.choices(pool, weights, k=PLAYS)):
        if number < 45:
            played = rng.randint(max(week, now - 5 * 86400), now)
        else:
            # More in recent weeks than two months ago.
            played = now - int(HISTORY_DAYS * 86400 * rng.random() ** 1.3)
        events.append(
            {
                "track_id": track["id"],
                "played_at": played,
                "seconds": track["duration"],
                "event_id": str(uuid.UUID(int=rng.getrandbits(128))),
            }
        )
    return events


def seed_library(store, auth, tracks: list[dict], rng) -> dict:
    """Both accounts with their songs, favourites, playlists and listening.
    Returns the owner's id, password and most played songs."""
    from server.listening import COLUMNS, details
    from server.playlists import Playlists
    from server.rewind import period_start
    from zoneinfo import ZoneInfo

    users = store.user_libraries
    password = secrets.token_urlsafe(18)
    owner = auth.create_owner(USERNAME, password, DISPLAY_NAME)
    # The first owner is given every song already; this keeps it so.
    users.add(owner, [track["id"] for track in tracks])
    invitation = auth.setup_code(owner, MEMBER, MEMBER_NAME)
    auth.activate(invitation["code"], secrets.token_urlsafe(18), "127.0.0.1")
    member = invitation["user_id"]
    theirs = [t for t in tracks if t["album_artist"] in MEMBER_ARTISTS]
    users.add(member, [track["id"] for track in theirs])
    now = int(time.time())
    # Raw SQL: nothing in the server sets when a song joined a library, and
    # Recently added (Home, the mix) reads it; it also leaves out songs the
    # first owner was given at setup ('migration'), as all of these were.
    with store.connect() as conn:
        conn.executemany(
            "UPDATE library_members SET added_at=?,source='household-add' "
            "WHERE track_id=?",
            [
                (now - track["added_days_ago"] * 86400 - position, track["id"])
                for position, track in enumerate(tracks)
            ],
        )
    zone = ZoneInfo(store.mixes.cache.settings()["timezone"])
    events = listens(tracks, now, period_start("week", now, zone), rng)
    recent = [e for e in events if e["played_at"] > now - 29 * 86400]
    users.history(owner, recent, "showcase-seed")
    by_id = {track["id"]: track for track in tracks}
    # Raw SQL: the server refuses a listen reported more than 30 days late,
    # and Rewind's month and year need older ones. The same two rows it writes.
    with store.connect() as conn:
        for event in events:
            if event in recent:
                continue
            conn.execute(
                "INSERT INTO play_events(user_id,track_id,played_at,seconds,"
                "device_session_id,event_id) VALUES (?,?,?,?,?,?)",
                (owner, event["track_id"], event["played_at"], event["seconds"])
                + ("showcase-seed", event["event_id"]),
            )
            conn.execute(
                f"INSERT INTO listen_details(user_id,event_id,{COLUMNS}) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (owner, event["event_id"], *details(by_id[event["track_id"]])),
            )
    top = [by_id[i] for i, _ in Counter(e["track_id"] for e in events).most_common()]
    playlists = Playlists(store, owner)
    for track in top[:FAVOURITES]:
        playlists.favourite(song(track), True)
    for name, count in PLAYLISTS:
        created = playlists.create(name)
        for track in rng.sample(tracks, count):
            playlists.add(created["id"], song(track))
    name, count = MEMBER_PLAYLIST
    created = Playlists(store, member).create(name)
    for track in rng.sample(theirs, count):
        Playlists(store, member).add(created["id"], song(track))
    return {"owner": owner, "member": member, "password": password}


def seed_requests(store, owner: str) -> list[tuple[str, str]]:
    """Queues the Downloads list; returns each request's id and its state."""
    seeded = []
    for tracks, states, days in request_plan():
        store.user_libraries.request(owner, tracks)
        with store.connect() as conn:
            for track, state in zip(tracks, states):
                (identity,) = conn.execute(
                    "SELECT id FROM queue WHERE json_extract(payload,'$.title')=? "
                    "AND json_extract(payload,'$.artist')=?",
                    (track["title"], track["artist"]),
                ).fetchone()
                seeded.append((identity, state))
                # Raw SQL: a request is always made "now".
                conn.execute(
                    "UPDATE queue SET created_at=datetime('now',?),"
                    "updated_at=datetime('now',?) WHERE id=?",
                    (f"-{days * 24 + 2} hours", f"-{days * 24 + 1} hours", identity),
                )
    return seeded


def hold_requests(store, owner: str, seeded: list[tuple[str, str]]):
    """Puts each request in its state. Safe to repeat: a starting server
    returns every request under way to waiting, and this puts them back."""
    for identity, state in seeded:
        if state == "waiting":
            continue
        if state == "paused":
            try:
                store.user_libraries.pause_request(owner, identity)
            except ValueError:
                pass  # Already paused.
            continue
        # Raw SQL: only the downloader claims a request (and only with
        # downloads on, which they never are here); this is its claim.
        with store.connect() as conn:
            conn.execute(
                "UPDATE queue SET status='searching' WHERE id=? "
                "AND status='waiting_for_sources'",
                (identity,),
            )
        if state == "completed":
            store.request_state(
                identity,
                "completed",
                source="soulseek",
                codec="mp3",
                bitrate_kbps=320,
                timings={"queued_seconds": 12, "steps": STEPS},
            )
        elif state == "failed":
            store.request_state(
                identity,
                "failed",
                error="No source had a copy of this song that passed the checks",
                timings={"queued_seconds": 8, "steps": FAILED_STEPS},
            )
        elif state.startswith("downloading"):
            total = 9_400_000
            store.request_state(
                identity,
                "downloading",
                source="soulseek",
                received_bytes=int(total * float(state.split(":")[1])),
                total_bytes=total,
            )


def build_mixes(mixes, user: str) -> None:
    """Today's and tomorrow's library mixes, planned and published by the
    server's own code with no discoveries (a discovery needs a download, and
    downloads are off). With both days ready the running server looks
    nothing up for them."""
    projection, owned, hidden, keys = mixes.library_snapshot(user)
    history = mixes.history(user, owned, keys)
    weights = mixes.favourite_weights(user)
    # The genre the most artists share: a mix takes two songs from each.
    genres = Counter()
    for genre, _ in {(t.get("genre"), t["artist"]) for t in owned if t.get("genre")}:
        genres[genre] += 1
    genre = genres.most_common(1)[0][0] if genres else None
    today = mixes.day()
    for day in (today, today + timedelta(days=1)):
        mixes.plan_library_day(
            user, day, owned, hidden, [], genre, history, weights, keys
        )
        asyncio.run(mixes.prepare_staged(user, day, today))


def mix_listens(store, mixes, owner: str, rng):
    """A few listens of today's mixes, so Rewind names the mixes played."""
    now = int(time.time())
    events = []
    for mix in mixes.list(owner)["mixes"][:3]:
        for entry in rng.sample(mix["tracks"], 6):
            events.append(
                {
                    "track_id": entry.id,
                    "played_at": now - rng.randint(60, 7200),
                    "seconds": int(entry.duration or 180),
                    "event_id": str(uuid.UUID(int=rng.getrandbits(128))),
                }
            )
    store.user_libraries.history(owner, events, "showcase-seed")


def summary(store, mixes, owner: str, member: str) -> dict:
    from server.playlists import Playlists

    with store.connect() as conn:
        plays = dict(
            conn.execute("SELECT user_id,count(*) FROM play_events GROUP BY user_id")
        )
    playlists = Playlists(store, owner).list()
    groups = store.user_libraries.request_groups(owner)["groups"]
    songs = [item for group in groups for item in group["items"]]
    return {
        **store.library["stats"],
        "library_songs": sum(
            t.get("available", True)
            for t in store.user_libraries.projection(owner)["tracks"]
        ),
        "playlists": sum(not p.get("favourites") for p in playlists),
        "favourites": sum(p["track_count"] for p in playlists if p.get("favourites")),
        "plays": plays.get(owner, 0),
        "requests": dict(Counter(group["state"] for group in groups)),
        "request_songs": dict(Counter(item["status"] for item in songs)),
        "mixes": [mix["title"] for mix in mixes.list(owner)["mixes"]],
        "member_songs": len(store.user_libraries.projection(member)["tracks"]),
        "recently_added": len(store.user_libraries.recently_added(owner)["albums"]),
        "member_playlists": len(Playlists(store, member).list()),
        "member_mixes": len(mixes.list(member)["mixes"]),
    }


def keep_requests(app, owner: str, seeded: list[tuple[str, str]]):
    """Once the started server has returned interrupted requests to waiting
    (its downloader does that once), put the seeded states back."""
    while not (
        getattr(app.state, "downloader", None)
        and app.state.downloader.recovered.is_set()
    ):
        time.sleep(0.5)
    hold_requests(app.state.store, owner, seeded)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tls", type=Path, default=ROOT / ".localdata/ui-tests/tls")
    parser.add_argument(
        "--account", type=Path, default=ROOT / ".localdata/showcase/account.json"
    )
    parser.add_argument(
        "--check", action="store_true", help="Build, print a summary, serve nothing"
    )
    args = parser.parse_args(argv)
    # Real metadata providers answer; the warmer and mixes run.
    for name in ("PIG_MUSIC_TEST_LEGACY_API", "PIG_MUSIC_TEST_SERVICES"):
        os.environ.pop(name, None)
    os.environ.update(
        PIG_MUSIC_PRELOAD="true",
        PIG_MUSIC_EMBEDDED_ENGINE="false",
        PIG_MUSIC_PUBLIC_ORIGIN=ORIGIN,
    )
    rng = random.Random(2026)
    with tempfile.TemporaryDirectory(prefix="pig-showcase-") as temporary:
        root = Path(temporary)
        os.environ["PIG_MUSIC_LIBRARY"] = str(root)
        os.environ["PIG_MUSIC_DATA"] = str(root)
        tracks = plan()
        tones(root, tracks)
        (root / "library-snapshot.json").write_text(json.dumps({"tracks": tracks}))
        from server.app import create_app

        app = create_app(root)
        store, mixes = app.state.store, app.state.mixes
        people = seed_library(store, app.state.auth, tracks, rng)
        owner, member = people["owner"], people["member"]
        for user in (owner, member):
            build_mixes(mixes, user)
        mix_listens(store, mixes, owner, rng)
        seeded = seed_requests(store, owner)
        hold_requests(store, owner, seeded)
        if args.check:
            print(json.dumps(summary(store, mixes, owner, member), indent=2))
            return
        key, cert = certificates(args.tls)
        args.account.parent.mkdir(parents=True, exist_ok=True)
        args.account.write_text(
            json.dumps(
                {"server": ORIGIN, "username": USERNAME, "password": people["password"]}
            )
        )
        threading.Thread(
            target=keep_requests, args=(app, owner, seeded), daemon=True
        ).start()
        import uvicorn

        print(f"Serving {ORIGIN} as {USERNAME}", flush=True)
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=PORT,
            ssl_keyfile=str(key),
            ssl_certfile=str(cert),
            access_log=False,
            # The app keeps a connection open; don't wait on it at Ctrl-C.
            timeout_graceful_shutdown=5,
        )


if __name__ == "__main__":
    main()
