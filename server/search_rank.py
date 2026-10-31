"""How well a search matches, for one ranked list of every kind (D-61).

Where music lives never forms its own list: a person's library only breaks
ties. Each kind is matched on its own name (a song's title, an artist's
name), so a song-title search puts that song above unrelated artists and
albums.
"""

from functools import lru_cache

from .library import normalise as plain

# Every search compares the query with every name on the server; the names
# rarely change, so their normalised forms are remembered (#54: 20,000
# songs took ~140 ms a search without it).
normalise = lru_cache(maxsize=1 << 18)(plain)

EXACT, START, WORDS, WORD_START, ANYWHERE, ALL_WORDS = 100, 80, 65, 55, 40, 30
# A song or album found only by its artist's name ranks below anything whose
# own name matches: it is the artist the search meant.
BY_ARTIST = 15
# A tie-break, never enough to lift a weaker match over a better one.
IN_LIBRARY = 3
# How many of each kind Top results may show.
TOP_LIMITS = {"song": 5, "album": 3, "artist": 2, "playlist": 2}
TOP_SIZE = 8


def score(query, text):
    """0 when `text` doesn't match `query`; higher for a closer match."""
    query, text = normalise(query), normalise(text)
    if not query or not text:
        return 0
    if text == query:
        return EXACT
    if text.startswith(query):
        return START
    padded = " " + text + " "
    if " " + query + " " in padded:
        return WORDS
    if " " + query in padded:
        return WORD_START
    if query in text:
        return ANYWHERE
    words = text.split()
    if all(any(w.startswith(q) for w in words) for q in query.split()):
        return ALL_WORDS
    return 0


def named(query, name, artist=""):
    """A song or album: its own name first, then artist and name together
    ("u2 one"), then the artist alone."""
    own = score(query, name)
    # Artist and name together count only when some searched word is in the
    # name: "coldplay" alone means the artist, not each of its songs.
    words = normalise(name).split()
    names_it = any(w.startswith(q) for q in normalise(query).split() for w in words)
    together = score(query, f"{artist} {name}") * 0.9 if artist and names_it else 0
    by_artist = BY_ARTIST if artist and score(query, artist) >= WORD_START else 0
    return max(own, together, by_artist)


def order(results):
    """Best match first; the library breaks ties, then the shorter name."""
    return sorted(
        results,
        key=lambda r: (
            -(r["score"] + (IN_LIBRARY if r["in_library"] else 0)),
            len(r["name"]),
            r["name"],
        ),
    )


def top(ranked):
    """One mixed list of the best matches of every kind, at most
    TOP_LIMITS of each."""
    pool = order([r for kind in ranked.values() for r in kind])
    counts, chosen = {}, []
    for result in pool:
        kind = result["kind"]
        if counts.get(kind, 0) >= TOP_LIMITS[kind]:
            continue
        counts[kind] = counts.get(kind, 0) + 1
        chosen.append({"kind": kind, "id": result["id"]})
        if len(chosen) == TOP_SIZE:
            break
    return chosen
