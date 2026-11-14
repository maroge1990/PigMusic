"""Release-aware read-only library model and Apple export comparison."""

import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

VARIOUS = {"various artists", "various", "va", "v a"}


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").casefold()
    return " ".join(re.findall(r"\w+", text))


def year_order(value) -> int:
    # Years arrive as tag integers or request strings ("2019", "2019-05-01", "").
    found = re.search(r"\d{4}", str(value or ""))
    return int(found[0]) if found else 0


def identifier(*parts) -> str:
    return hashlib.sha256("\x1f".join(str(p) for p in parts).encode()).hexdigest()[:24]


def is_compilation(album_artist: str, tracks: list[dict]) -> bool:
    """A single artist's greatest hits must never hide that artist."""
    performers = {normalise(t["artist"]) for t in tracks}
    if normalise(album_artist) in VARIOUS:
        return True
    return len(performers) > 1 and any(t.get("compilation") for t in tracks)


SOUNDTRACK_TITLE = re.compile(
    r"\b(soundtracks?|ost|original score|film score|music from|motion picture"
    r"|video game|game music|original music|inspired by)\b"
)
# "Transformers: The Album" or "(The Score)" are tie-ins; "The Score" alone is not.
TIE_IN = re.compile(r"(?:[:(\[]|\s[-–]\s)\s*(?:the |original )?(?:score|album)\b")
SOUNDTRACK_GENRE = re.compile(
    r"\b(soundtracks?|score|video game|game music|vgm|anime)\b"
)
MUSICAL_TEXT = re.compile(
    r"\b(broadway|west end|cast recording|original cast|london cast|cast album"
    r"|musical theatre|musical theater)\b"
)
MUSICAL_TITLE = re.compile(r"\b(the|an?(?: \w+)?) musical\b")
MUSICAL_GENRE = re.compile(r"\b(musicals?|show tunes|cast recordings?)\b")
KNOWN_MUSICALS = {
    "hamilton",
    "hamilton an american musical",
    "the book of mormon",
    "book of mormon",
}


def classify(album: str, album_artist: str, genres: list[str], compilation: bool):
    """Automatic category; an owner's explicit choice always wins over this."""
    title = normalise(album)
    genre = normalise(" ".join(dict.fromkeys(genres)))
    if (
        MUSICAL_TEXT.search(title + " " + normalise(album_artist) + " " + genre)
        or MUSICAL_TITLE.search(title)
        or MUSICAL_GENRE.search(genre)
        or title in KNOWN_MUSICALS
    ):
        return "musical"
    if (
        SOUNDTRACK_TITLE.search(title)
        or TIE_IN.search((album or "").casefold())
        or SOUNDTRACK_GENRE.search(genre)
        or re.search(r"\bsound team\b", normalise(album_artist))
    ):
        return "soundtrack"
    return "compilation" if compilation else "album"


def build_library(
    snapshot: dict, categories: dict | None = None, anchors: dict | None = None
) -> dict:
    groups = defaultdict(list)
    anchors = anchors if anchors is not None else {}
    for raw in snapshot.get("tracks", []):
        track = dict(raw)
        track.setdefault("album_artist", track.get("artist", "Unknown artist"))
        track.setdefault("album", "Singles")
        track.setdefault("year", 0)
        track.setdefault("track_number", 0)
        track.setdefault("disc_number", 1)
        track.setdefault("duration", 0)
        track.setdefault("codec", "unknown")
        groups[(normalise(track["album_artist"]), normalise(track["album"]))].append(
            track
        )
    # Keep the original album ID for its first known edition. Persisted anchors
    # prevent a newly encountered edition from changing links or preferences.
    edition_groups = {}
    for key, tracks in groups.items():
        legacy_id = identifier("album", *key)
        releases = list(
            dict.fromkeys(
                t.get("musicbrainz_release_id")
                for t in tracks
                if t.get("musicbrainz_release_id")
            )
        )
        if releases and legacy_id not in anchors:
            anchors[legacy_id] = releases[0]
        anchor = anchors.get(legacy_id)
        for track in tracks:
            release = track.get("musicbrainz_release_id")
            # Unknown tags can join a sole known edition, but never choose among
            # multiple editions. Their own group remains reviewable.
            edition = release or (releases[0] if len(releases) == 1 else None)
            album_id = (
                legacy_id
                if not anchor or edition == anchor
                else identifier("album-edition", legacy_id, edition or "unknown")
            )
            edition_groups.setdefault((key, album_id), []).append(track)
    albums = []
    artist_names = {}
    artist_albums = defaultdict(list)
    artist_tracks = defaultdict(list)
    for (key, album_id), tracks in edition_groups.items():
        tracks.sort(key=lambda t: (t["disc_number"], t["track_number"], t["title"]))
        first = tracks[0]
        compilation = is_compilation(first["album_artist"], tracks)
        auto = classify(
            first["album"],
            first["album_artist"],
            [t.get("genre", "") for t in tracks],
            compilation,
        )
        category = (categories or {}).get(album_id) or auto
        compilation = category == "compilation"
        album = {
            "id": album_id,
            "title": first["album"],
            "artist": first["album_artist"],
            "artist_id": identifier("artist", key[0]),
            "year": next((t["year"] for t in tracks if t["year"]), 0),
            "compilation": compilation,
            "track_count": len(tracks),
            "category": category,
            "musicbrainz_release_id": next(
                (
                    t.get("musicbrainz_release_id")
                    for t in tracks
                    if t.get("musicbrainz_release_id")
                ),
                None,
            ),
            "tracks": tracks,
            "codecs": sorted({t["codec"].upper() for t in tracks}),
            "cover_key": first["album_artist"] + "\x1f" + first["album"],
            "cover_sha": next(
                (t.get("cover_sha") for t in tracks if t.get("cover_sha")), None
            ),
            "duration": sum(t["duration"] for t in tracks),
        }
        albums.append(album)
        if category == "album":
            artist_names[album["artist_id"]] = album["artist"]
            artist_albums[album["artist_id"]].append(album_id)
        for t in tracks:
            performer_id = identifier("artist", normalise(t["artist"]))
            artist_names.setdefault(performer_id, t["artist"])
            artist_tracks[performer_id].append(
                {**t, "album_id": album_id, "compilation": compilation}
            )
    artists = []
    for artist_id, name in artist_names.items():
        own_albums = artist_albums.get(artist_id, [])
        credited = artist_tracks.get(artist_id, [])
        representative = next((a for a in albums if a["id"] in own_albums), None)
        if representative is None and credited:
            representative = next(
                a for a in albums if a["id"] == credited[0]["album_id"]
            )
        associated_tracks = {t["id"] for t in credited}
        associated_tracks.update(
            t["id"] for a in albums if a["id"] in own_albums for t in a["tracks"]
        )
        artists.append(
            {
                "id": artist_id,
                "name": name,
                "album_ids": own_albums,
                "album_count": len(own_albums),
                "track_count": len(associated_tracks),
                "compilation_only": not bool(own_albums),
                "cover_album_id": representative["id"] if representative else None,
            }
        )
    artists.sort(key=lambda a: a["name"].casefold().removeprefix("the "))
    albums.sort(
        key=lambda a: (
            a["artist"].casefold().removeprefix("the "),
            year_order(a["year"]),
            a["title"].casefold(),
        )
    )
    return {
        "artists": artists,
        "albums": albums,
        "snapshot": {
            k: snapshot.get(k) for k in ("source", "captured_at", "read_failures")
        },
        "stats": {
            "tracks": sum(a["track_count"] for a in albums),
            "artists": sum(not a["compilation_only"] for a in artists),
            "albums": sum(a["category"] == "album" for a in albums),
            "compilations": sum(a["category"] == "compilation" for a in albums),
            "soundtracks": sum(a["category"] == "soundtrack" for a in albums),
            "musicals": sum(a["category"] == "musical" for a in albums),
        },
    }


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))
