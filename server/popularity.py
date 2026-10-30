"""Optional ListenBrainz popularity; keys and provider errors stay private."""

import asyncio, hashlib, json, time
import httpx, logging

logger = logging.getLogger(__name__)
from .catalogue import (
    RENEW,
    background_metadata,
    deferred_metadata,
    mbid,
    settled,
    warming,
)
from .media import settings
from .releases import feature_album


# A person waiting for an artist page gets an answer within about this long;
# album details not looked up by then finish in the background (#21).
ALBUM_SECONDS = 10
# A partial answer is kept this long while its background completion runs.
PARTIAL_TTL = 600


def margin():
    """While warming, renew a day-long answer a few hours before it lapses."""
    return RENEW if warming.get() else 0


class Popularity:
    # ListenBrainz rate limit: space requests at least a second apart.
    min_interval = 1.05

    def __init__(self, store, catalogue=None):
        self.store = store
        self.catalogue = catalogue
        self.lock = asyncio.Lock()
        self.artist_locks = {}
        self.completing = {}
        self.last_request = 0.0

    def complete_later(self, client, artist_id):
        """Finish a partial answer's album details at background priority."""
        if artist_id in self.completing:
            return

        async def complete():
            background_metadata.set(True)
            try:
                await self.get(client, artist_id, complete=True)
            except Exception as error:
                logger.warning(
                    "Popularity completion failed (%s)", type(error).__name__
                )
            finally:
                self.completing.pop(artist_id, None)

        self.completing[artist_id] = asyncio.create_task(complete())

    async def get(self, client, artist_id, include_albums=True, complete=False):
        token = settings(self.store).get("listenbrainz_token")
        if not token:
            return {"enabled": False, "songs": [], "albums": []}
        artist_id = mbid(artist_id)
        full_key = (
            "popularity:v5:"
            + hashlib.sha256(token.encode()).hexdigest()[:12]
            + ":"
            + artist_id
        )
        songs_key = full_key + ":songs"
        key = full_key if include_albums else songs_key

        def usable(cached):
            # A completion run looks past the partial answer it is replacing.
            return (
                cached
                and cached.get("retry_after", 0) - margin() > time.time()
                and not (complete and cached.get("partial"))
            )

        async def known():
            # The warmer only needs song covers. Reuse a complete answer when
            # present, but never let a songs-only cache hide the page's albums.
            # SQLite can wait on another writer, so caches are read off the loop.
            for name in (full_key,) if include_albums else (full_key, songs_key):
                cached = await asyncio.to_thread(self.store.read_document, name)
                if usable(cached):
                    return cached["data"]
            return None

        found = await known()
        if found is not None:
            return found
        # One lock per artist for both kinds of answer, so a songs-only and a
        # full request never fetch the same recordings at once (#44).
        async with self.artist_locks.setdefault(full_key, asyncio.Lock()):
            found = await known()
            if found is not None:
                return found
            songs = None
            if include_albums:
                cached = await asyncio.to_thread(self.store.read_document, songs_key)
                if usable(cached):
                    songs = cached["data"]["songs"]
            started = time.monotonic()
            waiting = not (warming.get() or background_metadata.get() or complete)
            partial = False
            result = {
                "enabled": True,
                "songs": [],
                "albums": [],
                "ranked_album_ids": [],
            }
            try:
                responses = []
                failed = False
                kinds = (
                    ("recordings", "release-groups")
                    if include_albums
                    else ("recordings",)
                )
                if songs is not None:
                    # A songs-only answer already has the recordings.
                    kinds = ("release-groups",)
                    responses.append([])
                for kind in kinds:
                    try:
                        async with self.lock:
                            await asyncio.sleep(
                                max(
                                    0,
                                    self.min_interval
                                    - (time.monotonic() - self.last_request),
                                )
                            )
                            self.last_request = time.monotonic()
                        async with client.stream(
                            "GET",
                            "https://api.listenbrainz.org/1/popularity/top-"
                            + kind
                            + "-for-artist/"
                            + artist_id,
                            headers={"Authorization": "Token " + token},
                            timeout=25,
                            follow_redirects=False,
                        ) as response:
                            response.raise_for_status()
                            content = bytearray()
                            async for chunk in response.aiter_bytes():
                                content.extend(chunk)
                                if len(content) > 16_000_000:
                                    raise ValueError("Popularity response too large")
                        data = json.loads(content)
                        if not isinstance(data, list):
                            raise ValueError("Invalid popularity response")
                        ranked = []
                        for value in data:
                            if not isinstance(value, dict):
                                continue
                            try:
                                count = int(value.get("total_listen_count") or 0)
                            except (ValueError, TypeError):
                                continue
                            ranked.append((count, value))
                        responses.append(
                            [
                                value
                                for _, value in sorted(
                                    ranked, key=lambda pair: pair[0], reverse=True
                                )
                            ]
                        )
                    except Exception as error:
                        logger.warning(
                            "Popularity %s request failed (%s)",
                            kind,
                            type(error).__name__,
                        )
                        responses.append([])
                        failed = True
                if not include_albums:
                    responses.append([])
                seen = set()
                for item in responses[0]:
                    try:
                        if artist_id not in item.get("artist_mbids", []):
                            continue
                        recording = mbid(item["recording_mbid"])
                        release = mbid(item["release_mbid"])
                        if recording in seen:
                            continue
                        seen.add(recording)
                        result["songs"].append(
                            {
                                "id": recording,
                                "title": item["recording_name"],
                                "artist": item.get("artist_name", ""),
                                "album": item.get("release_name", ""),
                                "release_id": release,
                            }
                        )
                    except (ValueError, KeyError, TypeError):
                        continue
                    if len(result["songs"]) == 20:
                        break
                if songs is not None:
                    result["songs"] = songs
                ranked_ids = []
                for item in responses[1]:
                    group = item.get("release_group")
                    if not isinstance(group, dict) or group.get("type") != "Album":
                        continue
                    try:
                        ranked_ids.append(mbid(item["release_group_mbid"]))
                    except (ValueError, KeyError, TypeError, AttributeError):
                        continue
                result["ranked_album_ids"] = list(dict.fromkeys(ranked_ids))[:10000]
                seen = set()
                album_lookups = 0
                for item in responses[1]:
                    try:
                        group = item.get("release_group") or {}
                        if not isinstance(group, dict):
                            continue
                        if group.get("type") != "Album":
                            continue
                        identity = mbid(item["release_group_mbid"])
                        if identity in seen:
                            continue
                        seen.add(identity)
                        if self.catalogue:
                            if album_lookups >= 40:
                                break
                            album_lookups += 1
                            path = "release-group/" + identity
                            if waiting and time.monotonic() - started > ALBUM_SECONDS:
                                # Past the page's budget: only details already
                                # known; the rest are looked up in the background.
                                detail = await self.catalogue.cached(path)
                                if detail is None:
                                    partial = True
                                    continue
                            else:
                                # Optional warming yields to covers. A person
                                # opening the page retains foreground priority.
                                priority_token = deferred_metadata.set(
                                    background_metadata.get()
                                )
                                try:
                                    detail = await self.catalogue.get(client, path)
                                finally:
                                    deferred_metadata.reset(priority_token)
                            if not feature_album(detail):
                                continue
                            group = {
                                **group,
                                "name": detail.get("title", group["name"]),
                                "date": detail.get(
                                    "first-release-date", group.get("date", "")
                                ),
                            }
                        result["albums"].append(
                            {
                                "id": identity,
                                "title": group["name"],
                                "date": group.get("date", ""),
                                "type": "Album",
                            }
                        )
                    except (ValueError, KeyError, TypeError, httpx.HTTPError):
                        failed = True
                        continue
                    if len(result["albums"]) == 10:
                        break
                ttl = 60 if failed else PARTIAL_TTL if partial else 86400
            except Exception as error:
                logger.warning("Popularity parsing failed (%s)", type(error).__name__)
                # Unavailable or invalid credentials never add an empty/error panel.
                result = {"enabled": True, "songs": [], "albums": []}
                ttl, partial = 60, False
            await settled(
                self.store.write_document,
                key,
                {"retry_after": time.time() + ttl, "data": result, "partial": partial},
            )
            if partial:
                self.complete_later(client, artist_id)
            return result
