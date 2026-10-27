"""LOCK1: lifecycle_lock is held only to publish, never to hash or transfer."""

import json
import logging
import os
from pathlib import Path
import threading
import time

import pytest

from server import exporter as exporter_module
from server.exporter import Exporter
from server.lifecycle import TimedRLock
from test_exporter import acquired, download, nas_with
from test_downloader import audio  # noqa: F401  Shared real MP3 fixture.


def settle(*paths):
    """Make files look written a minute ago, as published songs are."""
    old = time.time() - 60
    for path in paths:
        os.utime(path, (old, old))


def counting(monkeypatch, store):
    calls = []
    original = exporter_module.digest

    def digest(path):
        calls.append((str(path), store.lifecycle_lock.owned()))
        return original(path)

    monkeypatch.setattr(exporter_module, "digest", digest)
    return calls


def test_an_unchanged_export_pass_reads_no_audio(tmp_path, monkeypatch):
    store, root = nas_with(tmp_path, monkeypatch, [])
    for name in ("one", "two", "three"):
        download(store, name, album="Album " + name)
    exporter = Exporter(store, root)
    exporter.process()
    assert store.read_document("library-export-status")["status"] == "completed"
    published = [root / t["export_relative_path"] for t in exporter.records()]
    settle(*published)
    exporter.process()  # Reads each published song once more, then trusts it.
    calls = counting(monkeypatch, store)
    started = time.monotonic()
    exporter.process()
    assert calls == []
    assert time.monotonic() - started < 1
    assert store.read_document("library-export-status") == {
        "status": "completed",
        "files": 3,
    }


def test_export_hashes_before_taking_lifecycle_lock(tmp_path, monkeypatch):
    store, root = nas_with(tmp_path, monkeypatch, [])
    download(store, "new")
    exporter = Exporter(store, root)
    calls = counting(monkeypatch, store)
    exporter.process()
    assert calls, "the download was verified"
    assert [path for path, held in calls if held] == []
    (song,) = Exporter(store, root).records()
    assert (root / song["export_relative_path"]).is_file()


def test_a_changed_published_song_is_read_again_and_refused(tmp_path, monkeypatch):
    store, root = nas_with(tmp_path, monkeypatch, [])
    download(store, "song")
    exporter = Exporter(store, root)
    exporter.process()
    (song,) = exporter.records()
    target = root / song["export_relative_path"]
    settle(target)
    exporter.process()
    # Same size, new content: its modification time changed.
    target.write_bytes(b"gnos")
    exporter.process()  # Written moments ago: checked on the next pass.
    assert store.read_document("library-export-status")["status"] == "completed"
    settle(target)
    exporter.process()
    status = store.read_document("library-export-status")
    assert status["status"] == "partial"
    assert status["needs_review"][0]["id"] == "song"


def test_a_just_written_file_is_never_trusted_from_memory(tmp_path, monkeypatch):
    store, root = nas_with(tmp_path, monkeypatch, [])
    path = root / "fresh.mp3"
    path.write_bytes(b"first")
    exporter = Exporter(store, root)
    first = exporter.digest(path)
    stamp = os.stat(path).st_mtime_ns
    path.write_bytes(b"other")
    os.utime(path, ns=(stamp, stamp))  # Same size and timestamp tick.
    assert exporter.digest(path) != first


def test_a_song_removed_during_the_pass_is_not_published(tmp_path, monkeypatch):
    store, root = nas_with(tmp_path, monkeypatch, [])
    track = download(store, "song")
    exporter = Exporter(store, root)
    with store.connect() as conn:
        conn.execute("DELETE FROM acquired WHERE id='song'")
    assert exporter.publish(track) is False
    assert not any(root.rglob("*.mp3"))


def test_long_holds_are_logged_with_their_holder(caplog):
    lock = TimedRLock("lifecycle_lock", warn_after=0.05)
    caplog.set_level(logging.WARNING, "pig_music.timing")

    def slow_publication():
        with lock:
            with lock:  # Re-entry: one hold, logged once.
                time.sleep(0.1)

    slow_publication()
    with lock:
        pass
    (record,) = caplog.records
    assert "lifecycle_lock held for 0.1 s by" in record.message
    assert "test_lock_holds.test_long_holds_are_logged_with_their_holder" in (
        record.message
    )
    assert lock.holder is None and not lock.owned()
    blocked = threading.Thread(target=lambda: lock.acquire(timeout=0.01))
    with lock:
        blocked.start()
        blocked.join()
    assert not lock.owned()


def test_a_download_written_moments_ago_waits_for_the_next_pass(tmp_path, monkeypatch):
    store, root = nas_with(tmp_path, monkeypatch, [])
    _, source = acquired(store, b"fresh", "fresh")
    exporter = Exporter(store, root)
    exporter.process()
    assert not any(root.rglob("*.mp3")) and source.is_file()
    assert store.read_document("library-export-status")["status"] == "completed"
    settle(source)
    exporter.process()
    assert len(list(root.rglob("*.mp3"))) == 1 and not source.exists()


def test_library_add_copies_a_mix_cache_file_without_lifecycle_lock(
    tmp_path, monkeypatch, audio
):
    import shutil

    from server import mix_cache
    from server.downloader import Downloader
    from test_mixes import cached, protected, track

    app, owner = protected(tmp_path, monkeypatch)
    store = app.state.store
    store.save_download_settings({"enabled": True, "sources": ["freemp3cloud"]})
    song = track()
    cached(store.mixes, song).write_bytes(audio.read_bytes())
    store.submit_catalogue([{**song, "status": "missing"}], user_id=owner)
    copies = []
    copy = shutil.copyfile

    def copyfile(source, target, **options):
        if Path(source).parent == store.mix_cache.directory:
            copies.append(store.lifecycle_lock.owned())
        return copy(source, target, **options)

    monkeypatch.setattr(mix_cache.shutil, "copyfile", copyfile)
    Downloader(store, providers={}).process(store.claim_request())
    assert copies == [False]
    assert store.queue()["items"][0]["status"] == "completed"


def edit_keeping_size_and_time(path, content):
    info = os.stat(path)
    path.write_bytes(content)
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
    assert os.stat(path).st_size == info.st_size


def copy_at_destination(store, root, exporter, track):
    target = root / exporter.relative(track)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(track["id"].encode())
    settle(target)
    return target


def test_an_edit_that_restores_the_time_never_lets_a_pass_delete_the_source(
    tmp_path, monkeypatch
):
    store, root = nas_with(tmp_path, monkeypatch, [])
    track = download(store, "song")
    (source,) = (store.directory / "downloads" / "library").rglob("song.mp3")
    exporter = Exporter(store, root)
    target = copy_at_destination(store, root, exporter, track)
    exporter.digest(target)  # Remembered by an earlier pass.
    edit_keeping_size_and_time(target, b"gnos")
    exporter.process()
    status = store.read_document("library-export-status")
    assert status["status"] == "partial"
    assert "different content" in status["needs_review"][0]["error"]
    assert source.read_bytes() == b"song"
    assert Exporter(store, root).records()[0].get("export_relative_path") is None


def test_publication_reads_a_separate_copy_before_deleting_the_source(
    tmp_path, monkeypatch
):
    store, root = nas_with(tmp_path, monkeypatch, [])
    track = download(store, "song")
    (source,) = (store.directory / "downloads" / "library").rglob("song.mp3")
    exporter = Exporter(store, root)
    target = copy_at_destination(store, root, exporter, track)
    exporter.digest(target, this_pass=True)  # Read in this very pass...
    edit_keeping_size_and_time(target, b"gnos")  # ...then edited.
    with pytest.raises(ValueError, match="verification failed"):
        exporter.publish(track)
    assert source.read_bytes() == b"song"


def test_an_edited_source_is_kept_beside_a_separate_valid_copy(tmp_path, monkeypatch):
    store, root = nas_with(tmp_path, monkeypatch, [])
    track = download(store, "song")
    (source,) = (store.directory / "downloads" / "library").rglob("song.mp3")
    exporter = Exporter(store, root)
    copy_at_destination(store, root, exporter, track)  # The expected bytes.
    exporter.pass_id += 1
    assert exporter.warm(track)  # This pass's reads...
    edit_keeping_size_and_time(source, b"gnos")  # ...then the source changes.
    with pytest.raises(ValueError, match="download changed"):
        exporter.publish(track)
    assert source.read_bytes() == b"gnos"
    assert Exporter(store, root).records()[0].get("export_relative_path") is None


def test_library_readers_never_wait_for_a_refresh_build(tmp_path, monkeypatch):
    # Live 28 Oct: each rebuild held library_lock 1-2 s, about six times a
    # minute, and household search and sync waited behind it.
    store, _ = nas_with(tmp_path, monkeypatch, [])
    download(store, "song")
    building, release = threading.Event(), threading.Event()
    build = store._build_library

    def slow_build():
        building.set()
        release.wait(5)
        return build()

    monkeypatch.setattr(store, "_build_library", slow_build)
    refresh = threading.Thread(target=store.refresh_library)
    refresh.start()
    assert building.wait(5)
    started = time.monotonic()
    with store.library_lock:
        waited = time.monotonic() - started
    release.set()
    refresh.join(5)
    assert waited < 0.1
    assert "song" in {t["id"] for a in store.library["albums"] for t in a["tracks"]}


def test_a_refresh_builds_only_after_the_previous_one_saved_its_anchors(
    tmp_path, monkeypatch
):
    # Codex (LOCK1 round 1, reproduced): a second build that read the
    # edition anchors before the first saved them overwrote the first's
    # anchors, so an album's ID could move to another edition.
    store, _ = nas_with(tmp_path, monkeypatch, [])
    track = {**download(store, "song"), "musicbrainz_release_id": "release-a"}
    with store.connect() as conn:  # An edition the first build anchors.
        conn.execute(
            "UPDATE acquired SET payload=? WHERE id='song'", (json.dumps(track),)
        )
    publishing, release = threading.Event(), threading.Event()
    build, publish = store._build_library, store._publish_library
    read, published = [], []

    def recording_build():
        read.append(store.read_document("album-edition-anchors"))
        return build()

    def slow_first_publish(built):
        if not published:
            publishing.set()
            release.wait(5)
        publish(built)
        published.append(store.read_document("album-edition-anchors"))

    monkeypatch.setattr(store, "_build_library", recording_build)
    monkeypatch.setattr(store, "_publish_library", slow_first_publish)
    with store.connect() as conn:  # Anchors the first build will create.
        conn.execute("DELETE FROM documents WHERE name='album-edition-anchors'")
    first = threading.Thread(target=store.refresh_library)
    first.start()
    assert publishing.wait(5)
    second = threading.Thread(target=store.refresh_library)
    second.start()
    second.join(0.3)
    assert len(read) == 1, "the second build waited for the first to publish"
    release.set()
    first.join(5)
    second.join(5)
    assert list(published[0].values()) == ["release-a"]
    assert read == [{}, published[0]]


def test_an_unchanged_export_pass_does_not_rebuild_the_library(tmp_path, monkeypatch):
    store, root = nas_with(tmp_path, monkeypatch, [])
    download(store, "one")
    exporter = Exporter(store, root)
    refreshes = []
    refresh = store.refresh_library
    monkeypatch.setattr(
        store, "refresh_library", lambda: refreshes.append(1) or refresh()
    )
    exporter.process()
    assert refreshes == [1]  # Its new location is recorded.
    (song,) = exporter.records()
    settle(root / song["export_relative_path"])
    for _ in range(3):
        exporter.process()
    assert refreshes == [1]


def test_a_shared_helper_is_named_with_what_called_it(tmp_path, monkeypatch, caplog):
    store, root = nas_with(tmp_path, monkeypatch, [])
    download(store, "song")
    caplog.set_level(logging.WARNING, "pig_music.timing")
    store.library_lock.warn_after = 0
    Exporter(store, root).process()
    holds = [r.message for r in caplog.records if "library_lock held" in r.message]
    assert any(
        "store.Store.refresh_library via exporter.Exporter.process" in m for m in holds
    ), holds


def test_a_coordinated_method_is_named_even_from_a_worker_thread(tmp_path, caplog):
    from concurrent.futures import ThreadPoolExecutor

    from server.store import Store

    store = Store(tmp_path / "data")
    store.lifecycle_lock.warn_after = 0
    caplog.set_level(logging.WARNING, "pig_music.timing")
    with ThreadPoolExecutor(1) as pool:
        pool.submit(store.recover_requests).result()
    holds = [r.message for r in caplog.records if "lifecycle_lock held" in r.message]
    assert any("store.Store.recover_requests" in m for m in holds), holds
