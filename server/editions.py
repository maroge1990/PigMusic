"""Which edition a requested song is filed under, and its cover.

Known identity comes first. A request naming a release keeps that release
group (an album asked for stays that album), and every song asked for from
one release lands on one edition: the best-ranked edition holding all of
that release's recordings (D-64). A request naming only a recording is
filed under the best album holding that recording. A request naming neither
(a playlist import) is filed under the artist's own studio album containing
the song. Release types (Live, Remix, Demo) are recording kinds: a live
recording is never filed as a studio one. `explicit_edition` requests keep
the release they name. What the request asked for stays in `requested`.
"""

import asyncio
import logging

import httpx
from PIL import Image

from .artwork import quoted
from .catalogue import (
    RELEASE_INC,
    RELEASE_KINDS,
    credit,
    edition_rank,
    group_kinds,
    mbid,
    release_tracks,
)
from .matching import (
    album_title,
    flavour,
    lead_artist,
    primary_artist,
    recording_kind,
    same_length,
    strip_edition,
    title_key,
)

logger = logging.getLogger(__name__)

DISTINCT_TYPES = RELEASE_KINDS
SKIPPED_TYPES = {
    "Interview",
    "Audiobook",
    "Audio drama",
    "Spokenword",
    "Field recording",
}


def tier(group, wanted):
    """Lower is better: studio album, EP, single, compilation/soundtrack."""
    secondary = set(group.get("secondary-types") or [])
    if secondary & SKIPPED_TYPES:
        return None
    kinds = {DISTINCT_TYPES[t] for t in secondary if t in DISTINCT_TYPES}
    if kinds - wanted:
        return None
    base = {"Album": 0, "EP": 1, "Single": 2}.get(group.get("primary-type"), 4)
    if secondary - set(DISTINCT_TYPES):
        base = max(base, 3)
    # A live/remix request prefers a release of that kind when there is one.
    return base + (0 if wanted <= kinds else 5)


def choose(recordings, job, kinds=frozenset(), recording_id=None):
    """(release, recording id) of the best edition for this song, or None.

    `recordings` is a MusicBrainz recording search or lookup answer. A known
    `recording_id` is that recording whatever its title; `kinds` are recording
    kinds already known (from the requested release's type)."""
    artist = primary_artist(job.get("artist"))
    title = title_key(job.get("title"))
    wanted = recording_kind(job) | flavour(job.get("title")) | kinds
    album = title_key(album_title(job.get("album"), job.get("album_artist")))
    options = []
    for recording in recordings:
        if recording_id:
            if recording.get("id") != recording_id:
                continue
        else:
            if title_key(recording.get("title")) != title:
                continue
            if primary_artist(credit(recording.get("artist-credit", []))) != artist:
                continue
            if flavour(recording.get("disambiguation")) - wanted:
                continue
            length = (recording.get("length") or 0) / 1000
            if not same_length({"duration": length}, job):
                continue
        for release in recording.get("releases") or []:
            if release.get("status") not in {None, "Official"}:
                continue
            group = release.get("release-group") or {}
            rank = tier(group, wanted)
            if rank is None or not group.get("id"):
                continue
            options.append((rank, group, release, recording["id"]))
    if not options:
        return None
    groups = {}
    for rank, group, release, recording in options:
        date = release.get("date") or "9999"
        best = groups.get(group["id"])
        groups[group["id"]] = (
            min(rank, best[0]) if best else rank,
            min(date, best[1]) if best else date,
            group,
        )
    rank, _, group = min(
        groups.values(),
        key=lambda g: (
            g[0],
            title_key(g[2].get("title")) != album,
            g[1],
            g[2]["id"],
        ),
    )
    release, recording = min(
        (
            (release, recording)
            for _, g, release, recording in options
            if g["id"] == group["id"]
        ),
        key=lambda pair: edition_rank(pair[0]),
    )
    return release, recording


def recordings(release):
    """The recording IDs on a release's track list."""
    return {
        (track.get("recording") or {}).get("id")
        for medium in release.get("media") or []
        for track in medium.get("tracks") or []
    } - {None}


# Pages of 100 editions read for one release group (a few albums have
# hundreds; past this the requested release itself still qualifies).
EDITION_PAGES = 5

# A song whose lookup keeps failing is filed as requested after this many
# tries (about half an hour of waiting in all), so it never blocks for good.
RESOLUTION_ATTEMPTS = 5
RETRY_SECONDS = 60
# A song from an album download is never filed on a guess: its album-mates
# may reach another edition once MusicBrainz is back (D-64, #110). It keeps
# waiting, at most an hour between tries, for about a day; then its request
# fails with the reason and a fresh budget, for Retry once MusicBrainz is back.
ALBUM_RESOLUTION_ATTEMPTS = 30
MAX_RETRY_SECONDS = 3600


class Deferred(Exception):
    """The catalogue is unavailable for now: retry the request later."""

    def __init__(self, attempts, seconds):
        super().__init__("Edition lookup deferred")
        self.attempts, self.seconds = attempts, seconds


class Unresolved(Exception):
    """An album song's edition stayed unknown through a long outage."""

    def __init__(self):
        super().__init__(
            "MusicBrainz was unavailable for about a day, so this song's album "
            "edition is unknown. Retry this request once it is back."
        )


def transient(error):
    """A network failure or a 429/5xx answer, not a missing or bad entry."""
    if isinstance(error, httpx.HTTPStatusError):
        code = error.response.status_code
        return code == 429 or code >= 500
    return isinstance(error, httpx.TransportError)


class EditionResolver:
    def __init__(self, store, catalogue, artwork, client):
        self.store, self.catalogue, self.artwork = store, catalogue, artwork
        self.client = client

    async def prepare(self, job):
        """Fields to merge into the job before it is searched: the resolved
        edition (album, numbers, IDs) and a stored cover hash."""
        changes = {}
        if not job.get("explicit_edition") and not job.get("edition_resolved"):
            try:
                changes.update(await self.resolve(job) or {})
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
                attempts = job.get("resolution_attempts", 0) + 1
                album = bool(job.get("musicbrainz_release_id"))
                limit = ALBUM_RESOLUTION_ATTEMPTS if album else RESOLUTION_ATTEMPTS
                if transient(error) and attempts < limit:
                    # Filing it under the request's own album now would bring
                    # back the duplicate and the missing cover: try later.
                    logger.info(
                        "Edition lookup deferred (%s), attempt %d",
                        type(error).__name__,
                        attempts,
                    )
                    raise Deferred(
                        attempts,
                        min(
                            MAX_RETRY_SECONDS, RETRY_SECONDS * 2 ** min(attempts - 1, 6)
                        ),
                    )
                if transient(error) and album:
                    logger.warning(
                        "Edition lookup gave up after %d attempts (%s)",
                        attempts,
                        type(error).__name__,
                    )
                    raise Unresolved() from error
                logger.info("Edition lookup failed (%s)", type(error).__name__)
            changes["edition_resolved"] = True
        release = changes.get("musicbrainz_release_id") or job.get(
            "musicbrainz_release_id"
        )
        if release and not job.get("cover_sha"):
            sha = await self.cover(release)
            if sha:
                changes["cover_sha"] = sha
        return changes

    async def resolve(self, job):
        release_id = job.get("musicbrainz_release_id")
        recording_id = job.get("musicbrainz_recording_id")
        kinds = frozenset()
        if release_id:
            return await self.within_release(job, mbid(release_id), recording_id)
        if recording_id:
            found = await self.catalogue.get(
                self.client,
                "recording/" + mbid(recording_id),
                inc="releases+release-groups+artist-credits",
            )
            recordings = [found]
            # A recording found only on live (remix, demo) releases is one.
            groups = [
                group_kinds(r.get("release-group")) for r in found.get("releases") or []
            ]
            if groups:
                kinds = frozenset.intersection(*groups)
        else:
            query = (
                "recording:"
                + quoted(strip_edition(job["title"]))
                + " AND artist:"
                + quoted(lead_artist(job["artist"]))
            )
            found = await self.catalogue.get(
                self.client, "recording/", query=query, limit=50
            )
            recordings = found.get("recordings", [])
        chosen = choose(recordings, job, kinds, recording_id)
        if not chosen:
            return {"recording_kind": sorted(kinds)} if kinds else None
        release, recording = chosen
        return await self.filing(job, release["id"], recording, kinds)

    async def editions(self, group_id):
        """Every edition of a release group with its track list, paged.

        A recording lookup lists only its first 25 releases, so a popular
        song (on hundreds of compilations) seemed missing from a reissue
        that holds it, and one album landed on two editions (#106)."""
        found, offset = [], 0
        for _ in range(EDITION_PAGES):
            page = await self.catalogue.get(
                self.client,
                "release",
                **{
                    "release-group": group_id,
                    "inc": "media+recordings",
                    "limit": 100,
                    "offset": offset,
                },
            )
            releases = page.get("releases") or []
            found += releases
            offset += len(releases)
            if not releases or offset >= (page.get("release-count") or 0):
                break
        return found

    async def within_release(self, job, release_id, recording_id):
        """The requested release's group on one edition for every song asked
        for from that release (D-64): the best-ranked edition holding all of
        the requested release's recordings, so the requested release itself
        when no other does. A recording not on the requested release goes to
        the best-ranked edition holding it."""
        detail = await self.catalogue.get(
            self.client, "release/" + release_id, inc=RELEASE_INC
        )
        group = detail.get("release-group") or {}
        kinds = group_kinds(group)
        if not recording_id:
            recording_id = next(
                (
                    t["musicbrainz_recording_id"]
                    for t in release_tracks(detail)
                    if title_key(t["title"]) == title_key(job["title"])
                    and t["disc_number"] == job.get("disc_number", t["disc_number"])
                    and t["track_number"] == job.get("track_number", t["track_number"])
                ),
                None,
            ) or next(
                (
                    t["musicbrainz_recording_id"]
                    for t in release_tracks(detail)
                    if title_key(t["title"]) == title_key(job["title"])
                ),
                None,
            )
        chosen = release_id
        if recording_id and group.get("id"):
            try:
                asked = {
                    t["musicbrainz_recording_id"] for t in release_tracks(detail)
                } - {None}
                needed = asked if recording_id in asked else {recording_id}
                ranked = sorted(
                    (
                        e
                        for e in await self.editions(mbid(group["id"]))
                        if needed <= recordings(e)
                    ),
                    key=edition_rank,
                )
                if ranked:
                    chosen = ranked[0]["id"]
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
                # A passing outage is retried (prepare defers it): filing this
                # song on the requested release now while its album-mates
                # later reach the best edition would split the album again.
                if transient(error):
                    raise
                # Otherwise the requested release itself is filed.
        if not recording_id:
            return {"recording_kind": sorted(kinds)} if kinds else None
        return await self.filing(job, chosen, recording_id, kinds)

    async def filing(self, job, release_id, recording_id, kinds):
        """Filing metadata from the release's own track for this recording;
        what the request asked for is kept in `requested`."""
        detail = await self.catalogue.get(
            self.client, "release/" + mbid(release_id), inc=RELEASE_INC
        )
        track = next(
            (
                t
                for t in release_tracks(detail)
                if t.get("musicbrainz_recording_id") == recording_id
            ),
            None,
        )
        if not track:
            return {"recording_kind": sorted(kinds)} if kinds else None
        kinds = kinds | group_kinds(detail.get("release-group"))
        filed = {
            "album": track["album"],
            "album_artist": track["album_artist"] or job["album_artist"],
            "year": str(track["year"] or job.get("year") or ""),
            "disc_number": track["disc_number"] or 1,
            "track_number": track["track_number"] or 1,
            "musicbrainz_release_id": track["musicbrainz_release_id"],
            "musicbrainz_recording_id": recording_id,
            **({"isrcs": track["isrcs"]} if track.get("isrcs") else {}),
            **({"recording_kind": sorted(kinds)} if kinds else {}),
        }
        filed["requested"] = job.get("requested") or {
            k: job.get(k) for k in filed if k not in {"recording_kind"}
        }
        return filed

    async def cover(self, release_id):
        """The release's front cover (or its release group's) in the cover
        store, by hash; None when the Cover Art Archive has none."""
        try:
            path = await self.artwork.catalogue_cover(
                self.client, "release", mbid(release_id)
            )
            if not path:
                return None
            content = await asyncio.to_thread(path.read_bytes)
            return await asyncio.to_thread(self.store.covers.save, content)
        except (ValueError, TypeError, OSError, Image.DecompressionBombError):
            return None
