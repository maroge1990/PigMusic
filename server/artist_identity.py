"""One resolver maps a library artist to its MusicBrainz artist, or asks for a choice."""

from .artwork import quoted
from .catalogue import mbid
from .library import normalise
from .matching import lead_artist

# Albums whose songs credit performers one by one (a cast recording, a
# soundtrack): a song's credit files under the album's own artist.
STAGED = {"musical", "soundtrack"}
# MusicBrainz's catch-all credits (Various Artists, [unknown], [anonymous],
# [no artist], [traditional]): never a performer's identity.
SPECIAL = {
    "89ad4ac3-39f7-470e-963a-56509c546377",
    "125ec42a-7229-4250-afc5-e057484327fe",
    "f731ccc4-e22a-43af-a747-64213329e088",
    "eec63d3c-3b81-4ad4-b1e4-7c147d4d2b61",
    "9be7f096-97ec-4615-8957-8d40b5dcbc41",
}


class Ambiguous(Exception):
    """No single catalogue artist fits; candidates lists the exact-name matches."""

    def __init__(self, candidates=()):
        super().__init__("Ambiguous artist identity")
        self.candidates = list(candidates)


def cached_identity(store, artist):
    return _identity(store.read_document("library-artist:" + artist["id"]), artist)


def cached_identities(store, artists):
    """`cached_identity` for many artists with one read: {artist id: identity}."""
    known = store.read_documents("library-artist:" + a["id"] for a in artists)
    return {
        a["id"]: _identity(known.get("library-artist:" + a["id"]), a) for a in artists
    }


def _identity(known, artist):
    # An identity found for a collaborative credit records that credit.
    named = known.get("credit") or known.get("name", "") if known else ""
    if known and normalise(named) == normalise(artist["name"]):
        try:
            return {"id": mbid(known["id"]), "name": artist["name"]}
        except (ValueError, TypeError, KeyError, AttributeError):
            return None
    return None


async def resolve_artist(store, catalogue, client, artist, albums):
    """Return {"id","name"} for a library artist; raise Ambiguous when unsure.

    Provider failures (httpx errors) propagate to the caller.
    """
    known = cached_identity(store, artist)
    if known:
        return known
    wanted = normalise(artist["name"])
    identities = {}

    def credits(items):
        for item in items:
            candidate = item.get("artist", {})
            if (
                candidate.get("id")
                and candidate["id"] not in SPECIAL
                and normalise(candidate.get("name", "")) == wanted
            ):
                identities[candidate["id"]] = {
                    "id": candidate["id"],
                    "name": candidate["name"],
                }

    def exact(result, name):
        """Search results that are exactly this name, never a catch-all."""
        return [
            a
            for a in result.get("artists", [])
            if normalise(a["name"]) == name and a.get("id") not in SPECIAL
        ]

    # Release credits distinguish namesakes without guessing from popularity.
    release_ids = list(
        dict.fromkeys(
            a.get("musicbrainz_release_id")
            for a in albums
            if a.get("musicbrainz_release_id")
        )
    )
    released = []
    for release_id in release_ids[:3]:
        try:
            release_id = mbid(release_id)
        except (ValueError, TypeError):
            continue
        release = await catalogue.get(
            client, "release/" + release_id, inc="artist-credits"
        )
        released += release.get("artist-credit", [])
        credits(release.get("artist-credit", []))
    matches = []
    if not identities:
        result = await catalogue.get(
            client, "artist/", query="artist:" + quoted(artist["name"]), limit=100
        )
        matches = exact(result, wanted)
        if len(matches) == 1 and result.get("count", len(matches)) <= 100:
            identities[matches[0]["id"]] = {
                "id": matches[0]["id"],
                "name": matches[0]["name"],
            }
        elif len(matches) > 1:
            candidates = {a["id"] for a in matches}
            for album in albums[:3]:
                found = await catalogue.get(
                    client,
                    "release-group/",
                    query="releasegroup:"
                    + quoted(album["title"])
                    + " AND artist:"
                    + quoted(artist["name"]),
                    limit=100,
                )
                if found.get("count", 0) > 100:
                    continue
                for group in found.get("release-groups", []):
                    if normalise(group.get("title", "")) == normalise(album["title"]):
                        credits(
                            [
                                c
                                for c in group.get("artist-credit", [])
                                if c.get("artist", {}).get("id") in candidates
                            ]
                        )
    # MATCH1: "A, B & C" or "A feat. B", which MusicBrainz knows by no single
    # artist (Earth, Wind & Fire was tried whole above): a cast or soundtrack
    # album's songs file under the album's own artist, others under the
    # credit's lead, as the release credits or an exact search name them.
    lead = normalise(lead_artist(artist["name"]))
    if not identities and not matches and lead != wanted:
        performers = [
            c.get("artist", {})
            for c in released
            if c.get("artist", {}).get("id")
            and c.get("artist", {}).get("id") not in SPECIAL
        ]
        if performers and any(album.get("category") in STAGED for album in albums):
            chosen = [performers[0]]
        else:
            chosen = list(
                {
                    a["id"]: a
                    for a in performers
                    if normalise(a.get("name", "")) == lead
                }.values()
            )
            if not chosen:
                result = await catalogue.get(
                    client,
                    "artist/",
                    query="artist:" + quoted(lead_artist(artist["name"])),
                    limit=100,
                )
                found = exact(result, lead)
                if result.get("count", len(found)) <= 100:
                    chosen = found
        if len(chosen) > 1:
            matches = chosen  # Namesakes: the owner chooses.
        for a in chosen[:1] if len(chosen) == 1 else []:
            identities[a["id"]] = {"id": a["id"], "name": a["name"]}
    if len(identities) != 1:
        options = matches or list(identities.values())
        raise Ambiguous(
            {
                "id": a["id"],
                "name": a["name"],
                "disambiguation": a.get("disambiguation", ""),
            }
            for a in options
        )
    identity = next(iter(identities.values()))
    stored = dict(identity)
    if normalise(identity["name"]) != wanted:
        stored["credit"] = artist["name"]
    store.write_document("library-artist:" + artist["id"], stored)
    return identity
