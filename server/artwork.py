"""Display-only artwork enrichment; never writes tags or catalogue identity."""

import asyncio
import io
import json
import re
import time
from pathlib import PurePosixPath
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from PIL import Image, ImageOps

from .catalogue import credit, mbid
from .library import normalise, identifier
from .matching import album_title, lead_artist, strip_edition, title_key


def quoted(value):
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def archive_url(value):
    url = urlsplit(value)
    host = url.hostname or ""
    if (
        url.scheme not in {"http", "https"}
        or url.username
        or url.password
        or url.port not in {None, 443}
    ):
        raise ValueError("Invalid artwork address")
    if host not in {"coverartarchive.org", "archive.org"} and not host.endswith(
        ".archive.org"
    ):
        raise ValueError("Untrusted artwork address")
    return urlunsplit(("https", url.netloc, url.path, url.query, ""))


def artwork_title(value):
    # Display-only aliases; never alter file tags or acquisition identity.
    value = strip_edition(value)
    value = re.sub(
        r"\s*\((?:original\s+)?(?:motion picture|movie|television|video game)\s+(?:soundtrack|score)\)\s*$",
        "",
        value,
        flags=re.I,
    )
    return normalise(value).removeprefix("the ")


def artwork_year(album):
    if album.get("year"):
        return str(album["year"])
    # The snapshot may omit a date tag while the album folder explicitly
    # identifies the release year. Use only an exact album-title folder.
    years = set()
    for track in album["tracks"]:
        for folder in PurePosixPath(track.get("relative_path", "")).parts[:-1]:
            match = re.fullmatch(r"(.+?)\s*[\[(]((?:19|20)\d{2})[\])]", folder)
            if match and artwork_title(match[1]) == artwork_title(album["title"]):
                years.add(match[2])
    return next(iter(years)) if len(years) == 1 else ""


class Artwork:
    def __init__(self, store, catalogue):
        self.store, self.catalogue = store, catalogue
        self.locks = {}
        self.slots = asyncio.Semaphore(3)

    def target(self, album):
        return self.store.cover_dir / (
            identifier("catalogue-cover", album["id"]) + ".jpg"
        )

    def remember_missing(self, key, seconds):
        with self.store.connect() as conn:
            conn.execute(
                "INSERT INTO documents VALUES (?,?) ON CONFLICT(name) DO UPDATE SET payload=excluded.payload",
                (key, json.dumps({"retry_after": time.time() + seconds})),
            )

    async def cover(self, client, album):
        target = self.target(album)
        if target.exists():
            return target
        # v6: catalogue album titles (no " - EP", "Artist - ") retry every miss.
        key = "missing-artwork:v6:" + album["id"]
        if self.store.read_document(key).get("retry_after", 0) > time.time():
            return None
        lock = self.locks.setdefault(album["id"], asyncio.Lock())
        async with lock:
            if target.exists():
                return target
            if self.store.read_document(key).get("retry_after", 0) > time.time():
                return None
            async with self.slots:
                try:
                    release_ids = {
                        t.get("musicbrainz_release_id")
                        for t in album["tracks"]
                        if t.get("musicbrainz_release_id")
                    }
                    if len(release_ids) == 1:
                        kind, selected = "release", mbid(next(iter(release_ids)))
                    elif release_ids:
                        # Mixed explicit editions cannot establish one cover.
                        self.remember_missing(key, 86400)
                        return None
                    else:
                        wanted = album_title(album["title"], album["artist"])
                        query = (
                            "releasegroup:"
                            + quoted(wanted)
                            + " AND artistname:"
                            + quoted(album["artist"])
                        )
                        data = await self.catalogue.get(
                            client, "release-group/", query=query, limit=100
                        )
                        candidates = [
                            r
                            for r in data.get("release-groups", [])
                            if artwork_title(r.get("title", ""))
                            == artwork_title(wanted)
                            and normalise(credit(r.get("artist-credit", [])))
                            == normalise(album["artist"])
                        ]
                        matches = {r["id"] for r in candidates}
                        year = artwork_year(album)
                        if len(matches) > 1 and year:
                            # Same-titled releases remain ambiguous unless the
                            # saved year establishes exactly one release group.
                            matches = {
                                r["id"]
                                for r in candidates
                                if (r.get("first-release-date") or "")[:4] == year
                            }
                        if len(matches) == 1 and data.get("count", 0) <= 100:
                            kind = "release-group"
                            selected = mbid(next(iter(matches)))
                        else:
                            # Its songs name the release: the album's own
                            # release group, on its recommended edition.
                            selected = await self.from_songs(client, album)
                            if not selected:
                                self.remember_missing(key, 86400)
                                return None
                            kind = "release"
                    content = await self.front(client, kind, selected)
                    await asyncio.to_thread(self.save, content, target)
                    return target
                except httpx.HTTPStatusError as exc:
                    self.remember_missing(
                        key, 86400 if exc.response.status_code == 404 else 300
                    )
                except (
                    httpx.HTTPError,
                    ValueError,
                    OSError,
                    KeyError,
                    Image.DecompressionBombError,
                ):
                    self.remember_missing(key, 300)
                return None

    async def from_songs(self, client, album):
        """The release of this album that holds its songs, or None."""
        from .editions import choose

        wanted = title_key(album_title(album["title"], album["artist"]))
        for track in album["tracks"][:2]:
            query = (
                "recording:"
                + quoted(strip_edition(track["title"]))
                + " AND artist:"
                + quoted(lead_artist(track.get("artist") or album["artist"]))
            )
            data = await self.catalogue.get(client, "recording/", query=query, limit=50)
            recordings = [
                {
                    **r,
                    "releases": [
                        release
                        for release in r.get("releases") or []
                        if title_key((release.get("release-group") or {}).get("title"))
                        == wanted
                    ],
                }
                for r in data.get("recordings", [])
            ]
            chosen = choose(recordings, {**track, "album": album["title"]})
            if chosen:
                return mbid(chosen[0]["id"])
        return None

    async def catalogue_cover(self, client, kind, item_id):
        item_id = mbid(item_id)
        key = "catalogue-artwork:" + kind + ":" + item_id
        target = self.store.cover_dir / (identifier(key) + ".jpg")
        if target.exists():
            return target
        lock = self.locks.setdefault(key, asyncio.Lock())
        async with lock:
            if target.exists():
                return target
            if self.store.read_document(key).get("retry_after", 0) > time.time():
                return None
            async with self.slots:
                try:
                    content = await self.front(client, kind, item_id)
                    await asyncio.to_thread(self.save, content, target)
                    return target
                except httpx.HTTPStatusError as exc:
                    # A cover that does not exist is asked for again tomorrow,
                    # not on every warm pass (ART2: each retry also costs a
                    # MusicBrainz request song matching waits for).
                    self.remember_missing(
                        key, 86400 if exc.response.status_code == 404 else 300
                    )
                    return None
                except (
                    httpx.HTTPError,
                    ValueError,
                    OSError,
                    KeyError,
                    Image.DecompressionBombError,
                ):
                    self.remember_missing(key, 300)
                    return None

    async def front(self, client, kind, item_id):
        """Front cover for a release, else for that same release group.

        Many tagged editions (regional pressings, reissues) have no scan of
        their own while the release group does. Never crosses release groups.
        """
        try:
            return await self.fetch(
                client, f"https://coverartarchive.org/{kind}/{item_id}/front-500"
            )
        except httpx.HTTPStatusError as exc:
            if kind != "release" or exc.response.status_code != 404:
                raise
        release = await self.catalogue.get(
            client, "release/" + item_id, inc="release-groups"
        )
        group_id = mbid(release["release-group"]["id"])
        return await self.fetch(
            client, f"https://coverartarchive.org/release-group/{group_id}/front-500"
        )

    async def artist(self, client, artist):
        albums = self.store.albums_by_id
        ids = list(artist["album_ids"]) or [artist["cover_album_id"]]
        for album_id in ids:
            album = albums.get(album_id)
            if album and (album.get("cover_url") or "").startswith("/api/v1/covers/"):
                return self.store.cover_dir / album["cover_url"].rsplit("/", 1)[-1]
        for album_id in ids[:3]:
            if album_id in albums:
                target = await self.cover(client, albums[album_id])
                if target:
                    return target
        return None

    async def fetch(self, client, url, address=archive_url):
        """`address` checks every hop, redirects included: only trusted hosts."""
        for _ in range(6):
            url = address(url)
            async with client.stream(
                "GET",
                url,
                follow_redirects=False,
                headers={"Accept": "image/*"},
                timeout=15,
            ) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers["location"])
                    continue
                response.raise_for_status()
                if int(response.headers.get("content-length", 0)) > 5_000_000:
                    raise ValueError("Artwork too large")
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > 5_000_000:
                        raise ValueError("Artwork too large")
                return bytes(content)
        raise ValueError("Too many artwork redirects")

    @staticmethod
    def save(content, target):
        with Image.open(io.BytesIO(content)) as image:
            if image.width * image.height > 20_000_000:
                raise ValueError("Artwork too large")
            image = ImageOps.exif_transpose(image).convert("RGB")
            image.thumbnail((1024, 1024))
            temporary = target.with_suffix(".tmp")
            try:
                image.save(temporary, "JPEG", quality=84)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
