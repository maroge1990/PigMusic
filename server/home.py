"""Bounded discovery feeds; only catalogue IDs are sent to providers, never listens."""

import asyncio
import json
import logging
import re
import time
from datetime import date
import httpx
from .artist_identity import Ambiguous, cached_identities, resolve_artist
from .artwork import quoted
from .catalogue import background_metadata, mbid, credit, settled, warming
from .library import normalise
from .media import chart_region, settings
from .releases import feature_album

log = logging.getLogger("pig_music.home")

CHART_TTL = 6 * 3600
MATCH_TTL = 7 * 86400
REGIONS = {
    "au": "Australia",
    "nz": "New Zealand",
    "us": "United States",
    "gb": "United Kingdom",
}
CHART_URL = (
    "https://rss.marketingtools.apple.com/api/v2/{}/music/most-played/25/{}.json"
)
SEED_POOL, SEED_LIMIT, NEW_RESOLUTIONS = 15, 5, 4
RECOMMENDED_CAPS = {"artists": 6, "albums": 6, "songs": 8}
RECOMMENDED_SOURCE = "Similar to artists in your library"


def region_name(region):
    return REGIONS.get(region, region.upper())


def clean_title(value):
    value = re.sub(
        r"\s*[\(\[]\s*(?:feat\.?|featuring|ft\.?|with)\b[^\)\]]*[\)\]]",
        "",
        value or "",
        flags=re.I,
    )
    return re.sub(r"\s+-\s+(?:single|ep)\s*$", "", value, flags=re.I).strip()


def first_artist(value):
    return re.split(
        r"\s*(?:,|&|\band\b|\bfeat\.?|\bfeaturing\b)\s*",
        value or "",
        maxsplit=1,
        flags=re.I,
    )[0]


def credit_names(items):
    first = next(
        (
            i.get("name") or i.get("artist", {}).get("name", "")
            for i in items
            if isinstance(i, dict)
        ),
        "",
    )
    return {normalise(credit(items)), normalise(first)} - {""}


def first_credit_artist(items):
    for item in items:
        if isinstance(item, dict) and item.get("artist", {}).get("id"):
            return item["artist"]["id"], item.get("name") or item["artist"].get(
                "name", ""
            )
    return None, ""


def release_choice(releases):
    def rank(release):
        group = release.get("release-group", {})
        kind = group.get("primary-type", "").casefold()
        return (
            release.get("status") != "Official",
            bool(group.get("secondary-types")),
            {"album": 0, "single": 1, "ep": 1}.get(kind, 2),
            release.get("date") or "9999",
        )

    ordered = sorted((r for r in releases if r.get("id")), key=rank)
    return ordered[0] if ordered else None


VERSION_WORDS = {"live", "demo", "acoustic", "remix", "session", "instrumental"}


def studio_rank(recording):
    """Lower is better: the studio recording on an official, plain release."""
    words = set(normalise(recording.get("disambiguation", "")).split())
    plain = any(
        r.get("status") == "Official"
        and not r.get("release-group", {}).get("secondary-types")
        for r in recording.get("releases", [])
    )
    return (bool(words & VERSION_WORDS), not plain)


def renew_margin():
    # Hour-long feeds are renewed by the warmer with 20 minutes to spare.
    return 1200 if warming.get() else 0


class HomeDiscovery:
    def __init__(self, store, catalogue):
        self.store, self.catalogue = store, catalogue
        self.locks = {}
        self.building = {}

    async def feed(self, client, path, **params):
        key = "home-feed:v1:" + path + repr(sorted(params.items()))
        # SQLite can wait on another writer, so the cache is used off the loop.
        cached = await asyncio.to_thread(self.store.read_document, key)
        if cached and cached.get("until", 0) - renew_margin() > time.time():
            return cached["data"], cached.get("failed", False)
        async with self.locks.setdefault(key, asyncio.Lock()):
            cached = await asyncio.to_thread(self.store.read_document, key)
            if cached and cached.get("until", 0) - renew_margin() > time.time():
                return cached["data"], cached.get("failed", False)
            try:
                headers = {
                    "User-Agent": "PigMusic/1.0 (https://github.com/maroge1990/Pig-Music)"
                }
                token = settings(self.store).get("listenbrainz_token")
                if token:
                    headers["Authorization"] = "Token " + token
                async with client.stream(
                    "GET",
                    "https://api.listenbrainz.org/1/" + path,
                    params=params,
                    headers=headers,
                    timeout=6,
                    follow_redirects=False,
                ) as response:
                    response.raise_for_status()
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > 2_000_000:
                            raise ValueError("Feed too large")
                data = json.loads(content)
                if path == "lb-radio/tags" and isinstance(data, list):
                    data = {"recordings": data}
                if not isinstance(data, dict):
                    raise ValueError("Invalid feed")
                await settled(
                    self.store.write_document,
                    key,
                    {"until": time.time() + 3600, "data": data},
                )
                return data, False
            except (httpx.HTTPError, ValueError):
                # A short retry window avoids hammering an unavailable service.
                data = cached.get("data", {}) if cached else {}
                await settled(
                    self.store.write_document,
                    key,
                    {"until": time.time() + 60, "data": data, "failed": True},
                )
                return data, True

    @staticmethod
    def identity(value):
        try:
            return mbid(value)
        except (ValueError, TypeError, AttributeError):
            return None

    async def popular(self, client):
        """Apple Music's chart for the household region, matched to MusicBrainz.

        Matching costs a rate-limited request per entry, so a missing chart is
        built in the background and the caller sees a preparing state. The
        warmer builds it directly and renews it ahead of expiry.
        """
        region = chart_region(settings(self.store))
        key = "home-chart:v1:" + region
        # SQLite can wait on another writer, so the chart is read off the loop.
        cached = await asyncio.to_thread(self.store.read_document, key)
        if cached and cached.get("until", 0) - renew_margin() > time.time():
            return cached["data"]
        task = self.build_chart(client, region)
        if warming.get():
            await task
            built = await asyncio.to_thread(self.store.read_document, key)
            return built.get("data") or self.chart(region, state="unavailable")
        if cached and cached.get("data"):
            return cached["data"]
        return self.chart(region, state="preparing")

    @staticmethod
    def chart(region, state, **lists):
        name = region_name(region)
        return {
            "title": "Popular in " + name,
            "source": "Apple Music · " + name,
            "artists": lists.get("artists", []),
            "albums": lists.get("albums", []),
            "songs": lists.get("songs", []),
            "unavailable": state == "unavailable",
            "state": state,
        }

    def build_chart(self, client, region):
        task = self.building.get(region)
        if task is None:
            task = asyncio.ensure_future(self.run_chart(client, region))
            self.building[region] = task
            task.add_done_callback(lambda _: self.building.pop(region, None))
        return asyncio.shield(task)

    async def run_chart(self, client, region):
        background_metadata.set(True)
        key = "home-chart:v1:" + region
        # The chart cache is used off the loop. A cancelled build still waits
        # for its write, so self.building covers it and a newer build's chart
        # cannot be overwritten by this one.
        previous = await asyncio.to_thread(self.store.read_document, key)
        try:
            feed = await self.match_chart(client, region)
            await settled(
                self.store.write_document,
                key,
                {"until": time.time() + CHART_TTL, "data": feed},
            )
        except asyncio.CancelledError:
            raise
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            log.warning("Building the %s chart failed", region, exc_info=True)
            data = (previous or {}).get("data") or self.chart(
                region, state="unavailable"
            )
            await settled(
                self.store.write_document,
                key,
                {"until": time.time() + 60, "data": data, "failed": True},
            )

    async def apple_chart(self, client, region, kind):
        response = await client.get(
            CHART_URL.format(region, kind),
            headers={
                "User-Agent": "PigMusic/1.0 (https://github.com/maroge1990/Pig-Music)"
            },
            timeout=6,
            follow_redirects=False,
        )
        response.raise_for_status()
        if len(response.content) > 2_000_000:
            raise ValueError("Feed too large")
        results = response.json()["feed"]["results"]
        if not isinstance(results, list):
            raise ValueError("Invalid feed")
        return [
            r
            for r in results
            if isinstance(r, dict)
            and r.get("id")
            and r.get("name")
            and r.get("artistName")
        ]

    async def match_chart(self, client, region):
        songs, albums = await asyncio.gather(
            self.apple_chart(client, region, "songs"),
            self.apple_chart(client, region, "albums"),
        )
        matched = {"songs": [], "albums": []}
        failure, failures = None, 0
        for kind, entries, cap in (("songs", songs, 10), ("albums", albums, 12)):
            for entry in entries:
                # One busy MusicBrainz reply skips an entry; a run of them stops.
                if len(matched[kind]) >= cap or failures >= 3:
                    break
                try:
                    item = await self.match_entry(client, kind, entry)
                except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                    failure, failures = exc, failures + 1
                    continue
                failures = 0
                if item and item["id"] not in {i["id"] for i in matched[kind]}:
                    matched[kind].append(item)
        if failure and not (matched["songs"] or matched["albums"]):
            raise failure
        artists = {}
        for item in matched["songs"] + matched["albums"]:
            artists.setdefault(
                item["artist_id"],
                {
                    "id": item["artist_id"],
                    "artist_id": item["artist_id"],
                    # A duet's card names the artist it opens, not the credit.
                    "name": item.get("artist_name") or item["artist"],
                    "artist": item.get("artist_name") or item["artist"],
                    "kind": "artists",
                },
            )
        return self.chart(
            region,
            state="ready",
            artists=list(artists.values())[:12],
            albums=matched["albums"],
            songs=matched["songs"],
        )

    async def match_entry(self, client, kind, entry):
        key = "home-chart-match:v1:" + kind + ":" + str(entry["id"])
        # One read and write per chart entry: SQLite can wait, so off the loop.
        cached = await asyncio.to_thread(self.store.read_document, key)
        if cached and cached.get("until", 0) > time.time():
            return cached.get("item")
        title, artist = clean_title(entry["name"]), entry["artistName"]
        item = await self.match_titles(client, kind, title, artist)
        if item is None and first_artist(artist) != artist:
            item = await self.match_titles(client, kind, title, first_artist(artist))
        await asyncio.to_thread(
            self.store.write_document,
            key,
            {"until": time.time() + MATCH_TTL, "item": item},
        )
        return item

    async def match_titles(self, client, kind, title, artist):
        wanted = normalise(title)
        names = {normalise(artist), normalise(first_artist(artist))} - {""}
        if kind == "songs":
            data = await self.catalogue.get(
                client,
                "recording/",
                query="recording:" + quoted(title) + " AND artist:" + quoted(artist),
                limit=10,
            )
            candidates = data.get("recordings", [])
        else:
            data = await self.catalogue.get(
                client,
                "release-group/",
                query="releasegroup:" + quoted(title) + " AND artist:" + quoted(artist),
                limit=10,
            )
            candidates = data.get("release-groups", [])
        if kind == "songs":
            # A chart means the studio song, not a live session of the same name.
            candidates = sorted(candidates, key=studio_rank)
        for found in candidates:
            credits = found.get("artist-credit", [])
            artist_id, artist_name = first_credit_artist(credits)
            if (
                not artist_id
                or normalise(found.get("title", "")) != wanted
                or not credit_names(credits) & names
            ):
                continue
            item = {
                "id": found["id"],
                "name": found["title"],
                "artist": credit(credits),
                "artist_id": artist_id,
                "artist_name": artist_name,
                "kind": kind,
            }
            if kind == "songs":
                release = release_choice(found.get("releases", []))
                item["release_id"] = release["id"] if release else None
                item["album"] = release.get("title", "") if release else ""
            return item
        return None

    async def recommended(self, client, projection):
        result = {
            "artists": [],
            "albums": [],
            "songs": [],
            "source": RECOMMENDED_SOURCE,
            "unavailable": False,
            "state": "ready",
        }
        seeds = await self.seeds(client, projection, result)
        owned = {a["name"].casefold() for a in projection["artists"]}
        # Identities already known for any library artist are never recommended.
        # One read for the whole library, off the loop: SQLite can wait.
        known = await asyncio.to_thread(
            cached_identities, self.store, projection["artists"]
        )
        owned_ids = {value["id"] for value in known.values() if value} | {
            identity for identity, _ in seeds
        }
        candidates = []
        for identity, name in seeds:
            feed, failed = await self.feed(
                client,
                "lb-radio/artist/" + identity,
                mode="easy",
                max_similar_artists=5,
                max_recordings_per_artist=1,
                pop_begin=40,
                pop_end=100,
            )
            result["unavailable"] |= failed
            found = []
            for artist_id, recordings in feed.items():
                candidate = self.identity(artist_id)
                if (
                    not candidate
                    or candidate in owned_ids
                    or not isinstance(recordings, list)
                    or not recordings
                    or not isinstance(recordings[0], dict)
                ):
                    continue
                artist_name = recordings[0].get("similar_artist_name", "")
                if not artist_name or artist_name.casefold() in owned:
                    continue
                found.append((candidate, artist_name, recordings[0], name))
            candidates.append(found)
        # Round-robin so the first seed does not crowd out the others.
        seen = set()
        ordered = []
        for position in range(max((len(c) for c in candidates), default=0)):
            for found in candidates:
                if position < len(found) and found[position][0] not in seen:
                    seen.add(found[position][0])
                    ordered.append(found[position])
        for candidate, artist_name, record, name in ordered:
            if len(result["artists"]) >= RECOMMENDED_CAPS["artists"]:
                break
            reason = "Because you saved " + name
            result["artists"].append(
                {
                    "id": candidate,
                    "artist_id": candidate,
                    "name": artist_name,
                    "artist": artist_name,
                    "reason": reason,
                    "kind": "artists",
                }
            )
            try:
                groups = await self.catalogue.get(
                    client,
                    "release-group/",
                    query="arid:" + candidate + " AND primarytype:album",
                    limit=6,
                )
                for group in groups.get("release-groups", []):
                    if feature_album(group):
                        if len(result["albums"]) < RECOMMENDED_CAPS["albums"]:
                            result["albums"].append(
                                {
                                    "id": group["id"],
                                    "artist_id": candidate,
                                    "artist": artist_name,
                                    "name": group["title"],
                                    "reason": reason,
                                    "kind": "albums",
                                }
                            )
                        break
                recording = self.identity(record.get("recording_mbid"))
                if recording and len(result["songs"]) < RECOMMENDED_CAPS["songs"]:
                    detail = await self.catalogue.get(
                        client,
                        "recording/" + recording,
                        inc="releases+artist-credits",
                    )
                    releases = detail.get("releases", [])
                    result["songs"].append(
                        {
                            "id": recording,
                            "artist_id": candidate,
                            "artist": credit(detail.get("artist-credit", [])),
                            "name": detail["title"],
                            "release_id": releases[0]["id"] if releases else None,
                            "album": releases[0].get("title", "") if releases else "",
                            "reason": reason,
                            "kind": "songs",
                        }
                    )
            except (httpx.HTTPError, ValueError, KeyError):
                result["unavailable"] = True
        if not seeds and not result["unavailable"]:
            result["state"] = "no_seeds"
        elif result["unavailable"] and not result["artists"]:
            result["state"] = "unavailable"
        return result

    async def seeds(self, client, projection, result):
        """Up to five resolved library artists, rotating daily through the top 15."""
        pool = sorted(
            (
                a
                for a in projection["artists"]
                if not a.get("compilation_only") and a.get("album_count", 1) != 0
            ),
            key=lambda a: (-a.get("track_count", 0), a["id"]),
        )[:SEED_POOL]
        if not pool:
            return []
        offset = date.today().toordinal() % len(pool)
        albums = {a["id"]: a for a in projection.get("albums", [])}
        seeds, fresh = [], 0
        cached = await asyncio.to_thread(cached_identities, self.store, pool)
        for artist in pool[offset:] + pool[:offset]:
            if len(seeds) >= SEED_LIMIT:
                break
            known = cached[artist["id"]]
            if known is None:
                if fresh >= NEW_RESOLUTIONS:
                    continue
                fresh += 1
                try:
                    known = await resolve_artist(
                        self.store,
                        self.catalogue,
                        client,
                        artist,
                        [albums[i] for i in artist.get("album_ids", []) if i in albums],
                    )
                except Ambiguous:
                    continue
                except (httpx.HTTPError, ValueError, KeyError):
                    result["unavailable"] = True
                    break
            seeds.append((known["id"], artist["name"]))
        return seeds
