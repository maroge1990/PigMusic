import asyncio
import io
import json
import threading
import time
import traceback

import httpx
from fastapi.testclient import TestClient
from PIL import Image

from server.app import create_app
from server.enrichment import Enrichment
from server.media import settings, save_settings
from server.store import Store


def seed(root, count=10):
    (root / "media-settings.json").write_text(json.dumps({"portraits": False}))
    (root / "library-snapshot.json").write_text(
        json.dumps(
            {
                "schema_version": 3,
                "tracks": [
                    {
                        "id": str(i),
                        "artist": "Artist",
                        "album": f"Album {i}",
                        "title": f"Song {i}",
                        "musicbrainz_release_id": "33333333-3333-4333-8333-333333333333",
                    }
                    for i in range(count)
                ],
            }
        )
    )


def test_cold_images_release_http_connections_while_provider_is_blocked(tmp_path):
    seed(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    calls = []

    async def provider(request):
        calls.append(request)
        entered.set()
        await asyncio.to_thread(release.wait, 5)
        return httpx.Response(404)

    app = create_app(tmp_path)
    with TestClient(app) as client:
        original = app.state.http
        app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(provider))
        try:
            library = client.get("/api/v1/admin/library").json()
            start = time.monotonic()
            for album in library["albums"]:
                assert client.get(album["cover_url"]).status_code == 404
            assert entered.wait(1)
            assert not release.is_set()
            assert client.get("/api/v1/admin/queue").status_code == 200
            assert client.get("/api/v1/admin/library/scan").status_code == 200
            assert time.monotonic() - start < 2
            assert app.state.enrichment.queue.qsize() <= 128
        finally:
            release.set()
            client.portal.call(app.state.enrichment.queue.join)
            client.portal.call(app.state.http.aclose)
            app.state.http = original


def test_queue_bounds_deduplication_backoff_and_shutdown():
    async def run():
        changes = []
        work = Enrichment(lambda library: changes.append(library), capacity=2)
        gate = asyncio.Event()

        async def operation():
            await gate.wait()
            return None

        assert work.submit("one", operation)
        assert not work.submit("one", operation)
        assert work.submit("two", operation)
        assert not work.submit("three", operation)
        work.start()
        gate.set()
        await work.queue.join()
        assert not work.submit("one", operation)
        assert changes == []
        await work.close()
        assert work.tasks == []

    asyncio.run(run())


def test_hash_variants_and_alias_cache_policy(tmp_path):
    seed(tmp_path, 1)
    app = create_app(tmp_path)
    stream = io.BytesIO()
    Image.new("RGB", (1024, 768), "purple").save(stream, "PNG")
    sha = app.state.store.covers.save(stream.getvalue())
    with TestClient(app) as client:
        base = "/api/v1/covers/" + sha + ".jpg"
        original = client.get(base)
        assert (
            original.headers["cache-control"] == "private, max-age=31536000, immutable"
        )
        result = client.get(base + "?w=128")
        assert result.headers["content-type"] == "image/webp"
        with Image.open(io.BytesIO(result.content)) as image:
            assert image.size == (128, 96)
        assert (
            client.get(
                base + "?w=128", headers={"If-None-Match": result.headers["etag"]}
            ).status_code
            == 304
        )
        assert client.get(base + "?w=127").status_code == 400
        assert client.get(base + "?w=128&soft=true").status_code == 200
        assert client.get(base).content == original.content
        alias = tmp_path / "covers" / "legacy.jpg"
        alias.write_bytes(original.content)
        result = client.get("/api/v1/covers/legacy.jpg", follow_redirects=False)
        assert result.headers["cache-control"] == "private, no-cache"
        assert result.headers["location"].startswith("/api/v1/covers/")
        (tmp_path / "covers" / "escape.jpg").symlink_to(
            tmp_path / "library-snapshot.json"
        )
        assert client.get("/api/v1/covers/escape.jpg").status_code == 404


def test_library_compression_validator_and_playlist_invalidation(tmp_path):
    seed(tmp_path, 100)
    app = create_app(tmp_path)
    with TestClient(app) as client:
        result = client.get(
            "/api/v1/admin/library", headers={"Accept-Encoding": "gzip"}
        )
        assert result.headers["content-encoding"] == "gzip"
        assert result.headers["cache-control"] == "private, no-cache"
        etag = result.headers["etag"]
        assert (
            client.get(
                "/api/v1/admin/library",
                headers={"If-None-Match": etag.removeprefix("W/")},
            ).status_code
            == 304
        )
        assert (
            client.get(
                "/api/v1/admin/library", headers={"If-None-Match": '"other", ' + etag}
            ).status_code
            == 304
        )
        with app.state.store.connect() as conn:
            conn.execute("INSERT INTO playlists(id,payload) VALUES ('test','{}')")
        changed = client.get("/api/v1/admin/library", headers={"If-None-Match": etag})
        assert changed.status_code == 200
        assert changed.json()["playlist_count"] == 1
        assert changed.headers["etag"] != etag
        assert app.state.store.albums_by_id[result.json()["albums"][0]["id"]]["title"]


def test_settings_cache_retains_external_updates_and_copy_isolation(
    tmp_path, monkeypatch
):
    store = Store(tmp_path)
    import server.media as media

    reads = []
    original = media.load_json

    def load(path):
        reads.append(path)
        return original(path)

    monkeypatch.setattr(media, "load_json", load)
    store._media_settings_cache = None
    value = settings(store)
    value["portraits"] = False
    assert settings(store)["portraits"]
    assert len(reads) == 1
    save_settings(store, False, True)
    assert not settings(store)["portraits"]
    (tmp_path / "media-settings.json").write_text(
        '{"portraits":true,"remote_playback":false}'
    )
    assert settings(store)["portraits"] and not settings(store)["remote_playback"]


def test_refresh_waits_for_cover_lock_before_taking_library_lock(tmp_path):
    store = Store(tmp_path)
    done = threading.Event()

    def refresh():
        store.refresh_library()
        done.set()

    with store.covers.lock:
        worker = threading.Thread(target=refresh, daemon=True)
        worker.start()
        # A scanner holding the cover lock must still be able to take library_lock.
        assert store.library_lock.acquire(timeout=1)
        store.library_lock.release()
        assert not done.is_set()
    worker.join(timeout=2)
    assert done.is_set()


def test_prune_only_removes_expired_owned_orphan_variants(tmp_path):
    import os
    from server.cover_store import WEEK
    from server.image_cache import variant

    store = Store(tmp_path)
    content = io.BytesIO()
    Image.new("RGB", (256, 256), "green").save(content, "PNG")
    sha = store.covers.save(content.getvalue())
    master = store.covers.path(sha)
    small = variant(master, 128)
    custom = small.parent / (sha + "-custom.webp")
    custom.write_bytes(b"preserve")
    now = time.time()
    for path in (master, small, custom):
        os.utime(path, (now - WEEK - 10, now - WEEK - 10))
    assert store.covers.prune({sha}, now) == 0
    assert small.exists()
    assert store.covers.prune(set(), now) == 1
    assert not small.exists() and custom.exists()


def test_stopped_state_verifier_allows_only_additive_validated_art_pointers(
    tmp_path, monkeypatch
):
    from scripts.verify_state_migration import main
    from server.library import identifier

    source = tmp_path / "source"
    source.mkdir()
    seed(source, 1)
    store = Store(source)
    album = store.library["albums"][0]
    content = io.BytesIO()
    Image.new("RGB", (256, 256), "red").save(content, "JPEG")
    (
        store.cover_dir / (identifier("catalogue-cover", album["id"]) + ".jpg")
    ).write_bytes(content.getvalue())
    store.write_document("user-preference", {"preserved": True})
    expected = tmp_path / "expected.json"
    expected.write_text(json.dumps({"tracks": 1, "album_ids": [album["id"]]}))
    monkeypatch.setattr(
        "sys.argv",
        [
            "verify",
            "--source",
            str(source),
            "--destination",
            str(tmp_path / "copy"),
            "--expected",
            str(expected),
        ],
    )
    main()
    copied = Store(tmp_path / "copy")
    assert copied.read_document("user-preference") == {"preserved": True}
    assert copied.read_document(
        "image-public:" + identifier("catalogue-cover", album["id"]) + ".jpg"
    )["sha"]


def test_corrupt_legacy_provider_cache_does_not_block_library_startup(tmp_path):
    from server.library import identifier

    seed(tmp_path, 1)
    store = Store(tmp_path)
    album = store.library["albums"][0]
    cached = store.cover_dir / (identifier("catalogue-cover", album["id"]) + ".jpg")
    cached.write_bytes(b"corrupt cached picture")
    restarted = Store(tmp_path)
    assert restarted.library["stats"]["tracks"] == 1
    assert restarted.library["albums"][0]["cover_url"].startswith(
        "/api/v1/library-artwork/"
    )
    assert cached.read_bytes() == b"corrupt cached picture"


def test_only_library_artwork_rebuilds_the_library(tmp_path):
    """#79: every finished artwork job (catalogue covers, backgrounds, logos,
    catalogue artists, cover metadata) rebuilt the whole library (1-2 s,
    holding the cover lock), though only library covers and portraits are
    part of it. The others only bump the artwork revision."""
    seed(tmp_path, 1)
    app = create_app(tmp_path)
    with TestClient(app) as client:
        store, work = app.state.store, app.state.enrichment
        rebuilds = []
        refresh = store.refresh_library

        def counted():
            # Only rebuilds an artwork job asks for; start-up recovery also
            # refreshes the library in the background.
            if any(f.name == "images_changed" for f in traceback.extract_stack()):
                rebuilds.append(True)
            return refresh()

        store.refresh_library = counted
        before = client.get("/api/v1/admin/queue").json()["artwork_revision"]

        async def found():
            return True

        assert work.submit("background:artist", found)
        assert work.submit("logo:artist", found)
        assert work.submit("metadata:cover", found)
        client.portal.call(work.queue.join)
        assert rebuilds == []
        assert (
            client.get("/api/v1/admin/queue").json()["artwork_revision"] == before + 3
        )
        assert work.submit("library:albums:one", found, library=True)
        client.portal.call(work.queue.join)
        assert rebuilds == [True]
        assert (
            client.get("/api/v1/admin/queue").json()["artwork_revision"] == before + 4
        )
        client.portal.call(app.state.http.aclose)
