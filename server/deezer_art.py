"""Deezer as the first source for catalogue covers and artist photos (#194).

Display only: an image is looked up for a MusicBrainz release, release group
or artist and saved by the existing cover helpers. Deezer's public API needs no
key. Matching, strongest first:

- a release's barcode (Deezer's ``album/upc:``), or a Deezer link MusicBrainz
  holds for the release, release group or artist;
- otherwise a search, accepted only when the artist's name and the album title
  (version words such as "Live" kept) match, and for an artist only when
  exactly one Deezer artist has that name.

Every failure answers None so the caller falls back to the existing sources.
"""

import asyncio
import json
import re
import time
from urllib.parse import urlsplit, urlunsplit

import httpx

from .catalogue import credit, mbid
from .library import normalise

API = "https://api.deezer.com/"
LINK = re.compile(
    r"^https?://(?:www\.)?deezer\.com/(?:[a-z]{2}/)?(album|artist)/(\d{1,20})/?$"
)
IMAGE = re.compile(
    r"^/images/(cover|artist)/[0-9a-f]{32}/\d{2,4}x\d{2,4}-[0-9a-z-]+\.jpg$"
)
ANSWER_LIMIT = 1_000_000
# Deezer allows 50 requests in 5 s per address; stay well under it.
SPACING = 0.15


class DeezerRefused(ValueError):
    """Deezer answered with an error other than "no data" (quota, outage)."""


def deezer_image(value):
    """A trusted Deezer CDN address for a real image (never the blank one)."""
    url = urlsplit(value)
    host = url.hostname or ""
    if (
        url.scheme != "https"
        or url.username
        or url.password
        or url.port not in {None, 443}
        or not (host == "dzcdn.net" or host.endswith(".dzcdn.net"))
        or not IMAGE.match(url.path)
    ):
        raise ValueError("Untrusted artwork address")
    return urlunsplit(("https", url.netloc, url.path, "", ""))


def picture(item, field):
    """The 1000 px image address, or None for Deezer's blank placeholder."""
    try:
        return deezer_image(str((item or {}).get(field) or ""))
    except ValueError:
        return None


def deezer_links(entity, kind):
    """Deezer ids MusicBrainz links this entity to, in its order."""
    found = []
    for relation in entity.get("relations") or []:
        if not isinstance(relation, dict):
            continue
        resource = str((relation.get("url") or {}).get("resource") or "")
        match = LINK.match(resource)
        if match and match[1] == kind and match[2] not in found:
            found.append(match[2])
    return found


class DeezerArt:
    def __init__(self, catalogue):
        self.catalogue = catalogue
        self.turn = asyncio.Lock()
        self.last = 0.0

    async def api(self, client, path, **params):
        """One Deezer answer as a dict; raises on any failure."""
        async with self.turn:
            wait = self.last + SPACING - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self.last = time.monotonic()
        async with client.stream(
            "GET",
            API + path,
            params=params,
            follow_redirects=False,
            headers={"Accept": "application/json"},
            timeout=10,
        ) as response:
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > ANSWER_LIMIT:
                    raise ValueError("Deezer answer too large")
        data = json.loads(bytes(body))
        if not isinstance(data, dict):
            raise ValueError("Unexpected Deezer answer")
        if "error" in data:
            # Code 800 is "no data"; anything else (quota, outage) is a failure.
            if (data.get("error") or {}).get("code") == 800:
                return {}
            raise DeezerRefused("Deezer refused the request")
        return data

    async def cover(self, client, kind, item_id):
        """Address of the Deezer cover for a MusicBrainz release or group."""
        from .artwork import artwork_title

        item_id = mbid(item_id)
        if kind == "release":
            entity = await self.catalogue.get(
                client, "release/" + item_id, inc="artist-credits+url-rels"
            )
            barcode = str(entity.get("barcode") or "")
            if re.fullmatch(r"\d{8,14}", barcode):
                found = picture(
                    await self.api(client, "album/upc:" + barcode), "cover_xl"
                )
                if found:
                    return found
        elif kind == "release-group":
            entity = await self.catalogue.get(
                client, "release-group/" + item_id, inc="artist-credits+url-rels"
            )
        else:
            return None
        for album_id in deezer_links(entity, "album")[:2]:
            found = picture(await self.api(client, "album/" + album_id), "cover_xl")
            if found:
                return found
        artist = credit(entity.get("artist-credit") or [])
        title = entity.get("title") or ""
        if not artwork_title(title) or not normalise(artist):
            return None
        data = await self.api(
            client,
            "search/album",
            q=f'artist:"{artist}" album:"{title}"',
            limit=10,
        )
        for album in data.get("data") or []:
            if (
                isinstance(album, dict)
                and normalise((album.get("artist") or {}).get("name", ""))
                == normalise(artist)
                and artwork_title(album.get("title") or "") == artwork_title(title)
            ):
                found = picture(album, "cover_xl")
                if found:
                    return found
        return None

    async def photo(self, client, name, artist_id=None):
        """Address of the Deezer photo for an artist, or None."""
        if artist_id:
            entity = await self.catalogue.get(
                client, "artist/" + mbid(artist_id), inc="url-rels"
            )
            links = deezer_links(entity, "artist")
            if links:
                # MusicBrainz names the artist exactly: no name guess after it.
                return picture(
                    await self.api(client, "artist/" + links[0]), "picture_xl"
                )
            name = entity.get("name") or name
        wanted = normalise(name)
        if not wanted:
            return None
        data = await self.api(client, "search/artist", q=name, limit=100)
        found = data.get("data") or []
        if data.get("next") or int(data.get("total") or 0) > len(found):
            return None  # A namesake may be on a later page.
        named = [
            artist
            for artist in found
            if isinstance(artist, dict) and normalise(artist.get("name", "")) == wanted
        ]
        # Namesakes are common (three "New Order"s): only a sole match counts.
        if len(named) != 1:
            return None
        return picture(named[0], "picture_xl")
