"""LOCK1: lifecycle_lock is held only to publish, never to hash or transfer."""

import logging
import os
from pathlib import Path
import threading
import time

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
