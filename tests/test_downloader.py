import json
import hashlib
import plistlib
import shutil
import subprocess
from pathlib import Path

import pytest
from mutagen import File

from server.downloader import Cancelled, Downloader, matches
from server.store import Store


def queued(store):
    inventory = plistlib.dumps(
        {
            "Tracks": {
                "1": {
                    "Name": "Test Track",
                    "Artist": "Test Artist",
                    "Album": "Test Album",
                    "Total Time": 3000,
                }
            }
        }
    )
    source = store.import_apple(inventory)
    store.update_selection([source["albums"][0]["tracks"][0]["id"]], True)
    store.submit_migration()
    store.save_download_settings(
        {"enabled": True, "profile": "mp3_320", "sources": ["freemp3cloud"]}
    )
    return store.claim_request()


@pytest.fixture
def audio(tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg is needed for complete decoding tests")
    output = tmp_path / "fixture.mp3"
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            "-codec:a",
            "libmp3lame",
            "-b:a",
            "320k",
            str(output),
        ],
        check=True,
        timeout=20,
    )
    return output


def test_strict_identity_duration_and_recording_context():
    target = {"artist": "Artist", "title": "One", "album": "Studio", "duration": 200}
    candidate = {"channel": "Artist", "title": "One", "duration": "3:20"}
    assert matches(target, candidate)
    assert not matches(target, {**candidate, "channel": "Cover Artist"})
    assert not matches({**target, "album": "Live at Home"}, candidate)
    assert not matches(target, {**candidate, "title": "One (Remix)"})
    assert not matches(target, {**candidate, "duration": "4:00"})


def test_native_aac_pipeline_preserves_codec_and_rejects_mp3_profile(tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg is needed for AAC validation")
    audio = tmp_path / "fixture.m4a"
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "anoisesrc=duration=3:sample_rate=44100:seed=1",
            "-f",
            "lavfi",
            "-i",
            "anoisesrc=duration=3:sample_rate=44100:seed=2",
            "-filter_complex",
            "amerge=inputs=2",
            "-codec:a",
            "aac",
            "-b:a",
            "320k",
            str(audio),
        ],
        check=True,
        timeout=20,
    )
    store = Store(tmp_path / "aac-data")
    store.save_download_settings(
        {"enabled": True, "profile": "aac_256", "sources": ["freemp3cloud", "jiosaavn"]}
    )
    source = store.import_apple(
        plistlib.dumps(
            {
                "Tracks": {
                    "1": {
                        "Name": "Test Track",
                        "Artist": "Test Artist",
                        "Album": "Test Album",
                        "Total Time": 3000,
                    }
                }
            }
        )
    )
    store.update_selection([source["albums"][0]["tracks"][0]["id"]], True)
    store.submit_migration()
    job = store.claim_request()
    candidate = {"title": "Test Track", "channel": "Test Artist", "duration": "0:03"}
    called = []

    class FixtureDownloader(Downloader):
        def transfer(self, job_id, candidate, path):
            shutil.copyfile(audio, path)

    def search(*_):
        called.append("jiosaavn")
        return [candidate]

    worker = FixtureDownloader(
        store,
        {
            "jiosaavn": search,
            "freemp3cloud": lambda *_: pytest.fail(
                "An MP3 source cannot satisfy an AAC request"
            ),
        },
    )
    with pytest.raises(ValueError, match="quality profile"):
        worker.validate(audio, job, "mp3_320")
    low_quality = tmp_path / "low-quality.m4a"
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-i",
            str(audio),
            "-codec:a",
            "aac",
            "-b:a",
            "128k",
            str(low_quality),
        ],
        check=True,
        timeout=20,
    )
    with pytest.raises(ValueError, match="selected quality"):
        worker.validate(low_quality, job, "aac_256")
    with pytest.raises(ValueError, match="address"):
        Downloader(store).transfer(
            job["id"],
            {"source": "jiosaavn", "source_url": "https://127.0.0.1/private"},
            worker.staging / "unsafe.m4a",
        )
    worker.process(job)
    assert called == ["jiosaavn"]
    assert store.queue()["items"][0]["status"] == "completed", store.queue()["items"][
        0
    ].get("error")
    saved = Store(store.directory).library["albums"][0]["tracks"][0]
    path = worker.library_root / saved["relative_path"]
    assert path.suffix == ".m4a" and saved["codec"] == "aac" and saved["bitrate"] >= 240
    tags = File(path)
    assert tags["\xa9nam"] == ["Test Track"]
    assert tags["aART"] == ["Test Artist"]
    assert tags["trkn"] == [(0, 0)]
    assert not list(worker.staging.glob("*.m4a"))


def test_complete_pipeline_tags_indexes_and_survives_restart(tmp_path, audio):
    store = Store(tmp_path / "data")
    job = queued(store)

    class FixtureDownloader(Downloader):
        def transfer(self, job_id, candidate, path):
            shutil.copyfile(audio, path)

    candidate = {
        "title": "Test Track",
        "channel": "Test Artist",
        "duration": "0:03",
        "source_url": "fixture",
        "quality": "320kbps",
    }
    worker = FixtureDownloader(store, {"freemp3cloud": lambda *_: [candidate]})
    worker.process(job)
    assert store.queue()["items"][0]["status"] == "completed"
    restarted = Store(store.directory)
    assert restarted.library["stats"]["tracks"] == 1
    track = restarted.library["albums"][0]["tracks"][0]
    path = store.directory / "downloads" / "library" / track["relative_path"]
    tags = File(path, easy=True)
    assert tags["title"] == ["Test Track"]
    assert tags["album"] == ["Test Album"]
    assert track["bitrate"] >= 300
    assert list(worker.staging.glob("*.mp3")) == []


def test_corrupt_audio_falls_back_and_failure_is_not_indexed(tmp_path):
    store = Store(tmp_path)
    job = queued(store)
    store.save_download_settings(
        {"enabled": True, "profile": "mp3_320", "sources": ["freemp3cloud", "zvu4no"]}
    )
    called = []
    candidate = {"title": "Test Track", "channel": "Test Artist", "duration": "0:03"}

    class CorruptDownloader(Downloader):
        def transfer(self, job_id, candidate, path):
            path.write_bytes(b"this is an HTML error page, not audio")

    def search(name):
        def run(*_):
            called.append(name)
            return [candidate]

        return run

    worker = CorruptDownloader(
        store, {name: search(name) for name in ["freemp3cloud", "zvu4no"]}
    )
    worker.process(job)
    assert called == ["freemp3cloud", "zvu4no"]
    assert store.queue()["items"][0]["status"] == "failed"
    assert store.library["stats"]["tracks"] == 0
    assert list(worker.library_root.rglob("*.mp3")) == []


def test_cancelled_transfer_is_not_published_and_interrupted_jobs_recover(tmp_path):
    store = Store(tmp_path)
    job = queued(store)
    store.cancel_request(job["id"])
    store.request_state(job["id"], "completed")
    assert store.queue()["items"][0]["status"] == "cancelled"
    store.retry_request(job["id"])
    assert store.claim_request()
    store.recover_requests()
    assert store.queue()["items"][0]["status"] == "waiting_for_sources"


def test_quality_and_source_address_checks(tmp_path, audio):
    store = Store(tmp_path / "data")
    job = queued(store)
    worker = Downloader(store)
    with pytest.raises(ValueError, match="quality"):
        worker.validate(audio, job, "lossless")
    with pytest.raises(ValueError, match="duration"):
        worker.validate(audio, {**job, "duration": 100}, "mp3_320")
    with pytest.raises(ValueError, match="address"):
        worker.transfer(
            job["id"],
            {"source": "freemp3cloud", "source_url": "http://127.0.0.1/private"},
            worker.staging / "unsafe.mp3",
        )
    worker.tag(audio, {**job, "title": "Wrong recording"}, "fixture")
    with pytest.raises(ValueError, match="metadata"):
        worker.validate(audio, job, "mp3_320")


def test_validation_accepts_what_selection_accepted_unless_an_edition_was_asked(
    tmp_path, audio
):
    """#69: selection treats remaster words as edition noise and "feat."
    credits as the lead artist, but validation demanded the exact tags, so
    "Song - 2004 Remaster" failed after the whole transfer. A request for a
    particular edition stays exact; live, remix and demo stay distinct."""
    store = Store(tmp_path / "data")
    job = queued(store)
    worker = Downloader(store)

    def tagged(**tags):
        worker.tag(audio, {**job, **tags}, "fixture")
        return audio

    for tags in (
        {"title": job["title"] + " - 2004 Remaster"},
        {"title": job["title"] + " (Remastered 2011)"},
        {"artist": job["artist"] + " feat. Guest"},
    ):
        worker.validate(tagged(**tags), job, "mp3_320")
    for tags in (
        {"title": job["title"] + " (Live)"},
        {"title": job["title"] + " - Demo"},
        {"title": "Another Song"},
        {"artist": "Someone Else"},
    ):
        with pytest.raises(ValueError, match="does not match"):
            worker.validate(tagged(**tags), job, "mp3_320")
    edition = {**job, "explicit_edition": True}
    with pytest.raises(ValueError, match="does not match"):
        worker.validate(
            tagged(title=job["title"] + " - 2004 Remaster"), edition, "mp3_320"
        )


def test_publication_journal_recovers_rename_before_database_commit(tmp_path, audio):
    store = Store(tmp_path / "data")
    job = queued(store)
    worker = Downloader(store)
    store.request_state(job["id"], "validating")
    target = worker.library_root / "recovered.mp3"
    shutil.copyfile(audio, target)
    metadata = {
        **job,
        "codec": "mp3",
        "bitrate": 320,
        "relative_path": "recovered.mp3",
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
    }
    journal = worker.staging / (job["id"] + ".json")
    journal.write_text(json.dumps(metadata))
    store.recover_requests()
    assert store.queue()["items"][0]["status"] == "completed"
    assert store.library["stats"]["tracks"] == 1
    assert target.exists()
    assert not journal.exists()


def test_pausing_returns_active_request_to_waiting(tmp_path):
    store = Store(tmp_path)
    job = queued(store)
    store.save_download_settings(
        {"enabled": False, "profile": "mp3_320", "sources": ["freemp3cloud"]}
    )
    with pytest.raises(Cancelled):
        Downloader(store).check(job["id"])
    assert store.queue()["items"][0]["status"] == "waiting_for_sources"
    assert store.claim_request() is None


def paced_downloader(tmp_path, monkeypatch, results):
    import server.downloader as module

    monkeypatch.setattr(module, "FREEMP3CLOUD_GAP", 0.05)
    monkeypatch.setattr(module, "FREEMP3CLOUD_RETRY_PAUSE", 0.05)
    monkeypatch.setattr(module, "_freemp3cloud_last", [0.0])
    state = {"active": 0, "max_active": 0, "starts": []}

    def search(query, limit):
        import time

        state["starts"].append(time.monotonic())
        state["active"] += 1
        state["max_active"] = max(state["max_active"], state["active"])
        time.sleep(0.02)
        state["active"] -= 1
        return results.pop(0) if results else []

    downloader = Downloader(Store(tmp_path), {"freemp3cloud": search})
    downloader.check = lambda job_id: None
    return downloader, state


def test_freemp3cloud_searches_are_serialised_and_spaced(tmp_path, monkeypatch):
    import threading

    downloader, state = paced_downloader(tmp_path, monkeypatch, [["a"]] * 4)
    threads = [
        threading.Thread(target=downloader.paced_search, args=({"id": i}, "q"))
        for i in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    starts = sorted(state["starts"])
    assert len(starts) == 4 and state["max_active"] == 1
    assert all(b - a >= 0.04 for a, b in zip(starts, starts[1:]))


def test_freemp3cloud_empty_result_is_retried_exactly_once(tmp_path, monkeypatch):
    downloader, state = paced_downloader(tmp_path, monkeypatch, [[], ["hit"]])
    assert downloader.paced_search({"id": 1}, "q") == ["hit"]
    assert len(state["starts"]) == 2
    downloader, state = paced_downloader(tmp_path, monkeypatch, [])
    assert downloader.paced_search({"id": 1}, "q") == []
    assert len(state["starts"]) == 2
