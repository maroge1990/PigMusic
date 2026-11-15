"""Library artwork projection and safe maintenance, separate from durable state."""

import json
import time
from .library import normalise, identifier, load_json
from .image_cache import public_image
from .media import settings as media_settings
from .cover_store import HASH


def cache_covers(store, snapshot_tracks, library=None):
    # `library` is a build not yet published (Store.refresh_library).
    library = store.library if library is None else library
    # One directory listing and two queries answer every cover, portrait and
    # catalogue-cover check below. They were two stats per album and, per
    # portrait, resolves, stats and a connection of their own (#144).
    covers = store.covers
    names = covers.listing()

    def public(targets):
        """`public_image` for files in the cover directory, answered from the
        listing and one query: {target: file name or None}."""
        listed = {t for t in targets if t.name in names}
        named = {t for t in listed if not HASH.fullmatch(t.stem)}
        saved = store.read_documents(["image-public:" + t.name for t in named])
        found = {}
        for target in targets:
            if target not in listed:
                found[target] = None
            elif target not in named:
                found[target] = covers.listed(target.stem, names)
            else:
                sha = saved.get("image-public:" + target.name, {}).get("sha")
                found[target] = covers.listed(sha, names)
                if not found[target]:
                    # Not yet registered: the full check saves it once.
                    image = public_image(store, target)
                    found[target] = image.name if image else None
                    if image:
                        names.add(image.name)
        return found

    # Quality replacements can supersede every scanned track in an album.
    # Retain its display artwork without moving the old file membership.
    scanned_art = {}
    for track in snapshot_tracks:
        if track.get("cover_sha"):
            key = (
                normalise(
                    track.get("album_artist", track.get("artist", "Unknown artist"))
                ),
                normalise(track.get("album", "Singles")),
            )
            scanned_art.setdefault(key, []).append(track)
    for album in library["albums"]:
        if not album.get("cover_sha"):
            candidates = scanned_art.get(
                (normalise(album["artist"]), normalise(album["title"])), []
            )
            release = album.get("musicbrainz_release_id")
            hashes = {
                t["cover_sha"]
                for t in candidates
                if not release or t.get("musicbrainz_release_id") == release
            }
            if len(hashes) == 1:
                album["cover_sha"] = next(iter(hashes))
        target = covers.listed(album.get("cover_sha"), names)
        album["cover_url"] = "/api/v1/covers/" + target if target else None
    albums = {a["id"]: a for a in library["albums"]}
    # Prefer any available own-album cover, rather than only the first album.
    for artist in library["artists"]:
        candidates = artist["album_ids"] or [artist["cover_album_id"]]
        artist["cover_url"] = next(
            (
                albums[a]["cover_url"]
                for a in candidates
                if a in albums and albums[a].get("cover_url")
            ),
            None,
        )
    uncovered = {
        album["id"]: store.cover_dir
        / (identifier("catalogue-cover", album["id"]) + ".jpg")
        for album in library["albums"]
        if not album["cover_url"]
    }
    catalogue = public(list(uncovered.values()))
    for album in library["albums"]:
        if not album["cover_url"]:
            cached = catalogue[uncovered[album["id"]]]
            album["cover_url"] = (
                "/api/v1/covers/" + cached
                if cached
                else "/api/v1/library-artwork/albums/" + album["id"]
            )
    config = media_settings(store)
    from .portraits import portrait_target

    # One read for every artist's saved identity, not one each (#144).
    saved = store.read_documents(
        ["library-artist:" + artist["id"] for artist in library["artists"]]
    )
    targets = (
        {
            artist["id"]: portrait_target(
                store,
                artist["name"],
                (saved.get("library-artist:" + artist["id"]) or {}).get("id"),
            )
            for artist in library["artists"]
        }
        if config["portraits"]
        else {}
    )
    portraits = public(list(targets.values()))
    for artist in library["artists"]:
        if not artist["cover_url"]:
            artist["cover_url"] = next(
                (
                    albums[a]["cover_url"]
                    for a in (artist["album_ids"] or [artist["cover_album_id"]])
                    if a in albums
                    and (albums[a].get("cover_url") or "").startswith("/api/v1/covers/")
                ),
                None,
            )
        portrait = portraits[targets[artist["id"]]] if config["portraits"] else None
        artist["fallback_cover_url"] = artist["cover_url"]
        if portrait:
            artist["cover_url"] = "/api/v1/covers/" + portrait
        elif config["portraits"] or not artist["cover_url"]:
            artist["cover_url"] = (
                "/api/v1/library-artwork/artists/" + artist["id"] + "?portraits=1"
            )
    # Optional image metadata is read from a small sidecar, never decoded here.
    for item in [*library["albums"], *library["artists"]]:
        url = item.get("cover_url", "") or ""
        if url.startswith("/api/v1/covers/"):
            sha = url.removeprefix("/api/v1/covers/").removesuffix(".jpg")
            metadata = covers.presentation(sha, names)
            if metadata:
                item["artwork_metadata"] = metadata


def portraits_waiting(store):
    """Whether a library artist still shows its fallback although its portrait
    has since been fetched (by the warmer, which does not refresh the library)."""
    from .portraits import portrait_target

    if not media_settings(store)["portraits"]:
        return False
    for artist in list(store.library["artists"]):
        if (artist.get("cover_url") or "").endswith("?portraits=1"):
            known = store.read_document("library-artist:" + artist["id"])
            if portrait_target(store, artist["name"], known.get("id")).is_file():
                return True
    return False


def prune_artwork(store, now=None):
    now = time.time() if now is None else now
    with store.covers.lock, store.library_lock:
        snapshot = load_json(store.directory / "library-snapshot.json")
        references = {t.get("cover_sha") for t in snapshot.get("tracks", [])}
        references.update(a.get("cover_sha") for a in store.library["albums"])
        with store.connect() as conn:
            references.update(
                json.loads(payload).get("sha")
                for (payload,) in conn.execute(
                    "SELECT payload FROM documents WHERE name >= 'image-public:' AND name < 'image-public;'"
                )
            )
        with store.connect() as conn:
            for (payload,) in conn.execute("SELECT payload FROM acquired"):
                references.add(json.loads(payload).get("cover_sha"))
            expired = []
            for name, payload in conn.execute(
                "SELECT name,payload FROM documents WHERE name >= 'missing-artwork:' AND name < 'missing-artwork;' OR name >= 'catalogue:' AND name < 'catalogue;'"
            ):
                value = json.loads(payload)
                expiry = (
                    value.get("retry_after")
                    if name.startswith("missing-artwork:")
                    else value.get("time", now) + 86400
                )
                if isinstance(expiry, (int, float)) and expiry < now:
                    expired.append((name, payload))
            # Compare payload as well so a concurrently refreshed cache wins.
            conn.executemany(
                "DELETE FROM documents WHERE name=? AND payload=?", expired
            )
        return {
            "covers_removed": store.covers.prune(references, now),
            "documents_removed": len(expired),
        }
