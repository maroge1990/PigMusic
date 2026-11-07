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


def test_a_lost_claim_leaves_no_publication_journal(tmp_path, audio):
    """#96: the staging journal was written before the claim was checked, so
    a request cancelled meanwhile left a journal recovery never removed."""
    store = Store(tmp_path / "data")
    job = queued(store)
    worker = Downloader(store)
    store.request_state(job["id"], "validating")
    store.cancel_request(job["id"])
    staged = worker.staging / "staged.mp3"
    shutil.copyfile(audio, staged)
    probe = {"codec": "mp3", "bitrate_kbps": 320, "duration": 3}
    target = worker.library_root / "song.mp3"
    assert not store.commit_request(job, staged, target, probe, "sha")
    journals = store.directory / "downloads" / "staging"
    assert list(journals.glob("*.json")) == [] and not target.exists()


def test_recovery_removes_orphan_journals_and_unclaimed_staged_audio(tmp_path, audio):
    """#96: a journal whose song never reached the library, or a torn one,
    stayed in staging for good; so did audio a restart's interrupted
    transfer staged (its request waits for a fresh claim)."""
    store = Store(tmp_path / "data")
    job = queued(store)
    worker = Downloader(store)
    store.request_state(job["id"], "downloading")
    orphan = store.directory / "downloads" / "staging" / "gone-token.json"
    orphan.write_text(json.dumps({**job, "relative_path": "never.mp3", "sha256": "x"}))
    torn = store.directory / "downloads" / "staging" / "torn-token.json"
    torn.write_text('{"title": "half wri')
    staged = worker.staging / f"{job['id']}-{job['claim_token']}.mp3"
    shutil.copyfile(audio, staged)
    # Not a transfer's staged audio: a mix promotion's file and other names.
    others = [
        worker.staging / f"{job['id']}-mix.mp3",
        worker.staging / f"{job['id']}-legacy.mp3",
        worker.staging / "notes.txt",
    ]
    for path in others:
        path.write_bytes(b"x")
    store.recover_requests()
    assert not orphan.exists() and not torn.exists() and not staged.exists()
    assert all(path.exists() for path in others)
    assert store.queue()["items"][0]["status"] == "waiting_for_sources"


def test_staged_audio_of_a_held_claim_is_kept(tmp_path, audio):
    store = Store(tmp_path / "data")
    job = queued(store)
    worker = Downloader(store)
    store.request_state(job["id"], "validating")
    held = worker.staging / f"{job['id']}-{job['claim_token']}.mp3"
    shutil.copyfile(audio, held)
    stale = worker.staging / f"{job['id']}-{'0' * 32}.mp3"
    shutil.copyfile(audio, stale)
    unknown = worker.staging / f"no-such-request-{'1' * 32}.flac"
    shutil.copyfile(audio, unknown)
    assert store.sweep_staged() == 2
    assert held.exists() and not stale.exists() and not unknown.exists()


def test_a_linked_staged_file_is_never_followed(tmp_path, audio):
    store = Store(tmp_path / "data")
    job = queued(store)
    worker = Downloader(store)
    outside = tmp_path / "outside.mp3"
    shutil.copyfile(audio, outside)
    link = worker.staging / f"{job['id']}-{'0' * 32}.mp3"
    link.symlink_to(outside)
    folder = worker.staging / f"{job['id']}-{'2' * 32}.mp3"
    folder.mkdir()
    (folder / "inside.mp3").write_bytes(b"x")
    store.sweep_staged()
    assert outside.exists() and (folder / "inside.mp3").exists()


def test_recovery_deletes_nothing_behind_a_linked_staging_folder(tmp_path, audio):
    store = Store(tmp_path / "data")
    job = queued(store)
    stage = store.directory / "downloads" / "staging"
    shutil.rmtree(stage, ignore_errors=True)
    stage.parent.mkdir(parents=True, exist_ok=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    keep = [
        elsewhere / "song.mp3",
        elsewhere / "notes.json",
        elsewhere / "torn.json",
        elsewhere / f"{job['id']}-{'0' * 32}.mp3",
    ]
    shutil.copyfile(audio, keep[0])
    keep[1].write_text(json.dumps({"relative_path": "never.mp3", "sha256": "x"}))
    keep[2].write_text("{not json")
    shutil.copyfile(audio, keep[3])
    stage.symlink_to(elsewhere, target_is_directory=True)
    store.recover_requests()
    assert all(path.exists() for path in keep)


@pytest.mark.parametrize("linked", [None, "work", "staging"])
def test_staged_audio_behind_a_linked_work_root_is_kept(
    tmp_path, audio, monkeypatch, linked
):
    """With a configured work root, its staging folder and the root itself
    must be real folders: a link could lead into the library."""
    library = tmp_path / "library"
    library.mkdir()
    real = tmp_path / "real-work"
    (real / "staging").mkdir(parents=True)
    work = tmp_path / "work"
    if linked is None:
        work = real
    elif linked == "work":
        work.symlink_to(real, target_is_directory=True)
    else:
        work.mkdir()
        (work / "staging").symlink_to(real / "staging", target_is_directory=True)
    monkeypatch.setenv("PIG_MUSIC_LIBRARY", str(library))
    monkeypatch.setenv("PIG_MUSIC_DOWNLOAD_ROOT", str(work))
    (library / ".pig-music-library.json").write_text('{"library_id": "test"}')
    data = tmp_path / "data"
    data.mkdir()
    (data / "library-export.json").write_text('{"library_id": "test"}')
    store = Store(data)
    job = queued(store)
    staged = real / "staging" / f"{job['id']}-{'0' * 32}.mp3"
    shutil.copyfile(audio, staged)
    # Without a link the same file goes (the mount check passes).
    assert store.sweep_staged() == (1 if linked is None else 0)
    assert staged.exists() == (linked is not None)


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


def paced_search(monkeypatch, results):
    """freemp3cloud through the shared legacy search, its search process
    replaced by one answering `results` (one list per search)."""
    from server.providers import legacy

    for name, value in (
        ("FREEMP3CLOUD_GAP", 0.05),
        ("FREEMP3CLOUD_RETRY_PAUSE", 0.05),
        ("_freemp3cloud_last", [0.0]),
    ):
        monkeypatch.setattr(legacy, name, value, raising=False)
    state = {"active": 0, "max_active": 0, "starts": []}

    def run_checked(command, check, timeout, **_):
        import json
        import time
        import types

        state["starts"].append(time.monotonic())
        state["active"] += 1
        state["max_active"] = max(state["max_active"], state["active"])
        time.sleep(0.02)
        state["active"] -= 1
        found = results.pop(0) if results else []
        return types.SimpleNamespace(
            returncode=0, stdout=json.dumps({"candidates": found}).encode()
        )

    monkeypatch.setattr(legacy, "run_checked", run_checked)
    return legacy.search, state


def test_freemp3cloud_searches_are_serialised_and_spaced(tmp_path, monkeypatch):
    """#140: the pacing was dead code; every caller of the shared search
    (downloads, mixes, the relay) is now paced."""
    import threading

    from server.providers.contracts import Context

    search, state = paced_search(monkeypatch, [["a"]] * 4)
    threads = [
        threading.Thread(target=search, args=("freemp3cloud", "q", Context("mp3_320")))
        for i in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    starts = sorted(state["starts"])
    assert len(starts) == 4 and state["max_active"] == 1
    assert all(b - a >= 0.04 for a, b in zip(starts, starts[1:]))


def test_downloads_search_freemp3cloud_through_the_pacing(tmp_path, monkeypatch):
    from server.providers import REGISTRY

    search, state = paced_search(monkeypatch, [["a"]] * 4)
    store = Store(tmp_path / "data")
    job = queued(store)
    worker = Downloader(store, {"freemp3cloud": REGISTRY["freemp3cloud"].search})
    worker.attempt(job, "mp3_320", [])
    assert len(state["starts"]) == 1


def test_freemp3cloud_empty_result_is_retried_exactly_once(tmp_path, monkeypatch):
    from server.providers.contracts import Context

    search, state = paced_search(monkeypatch, [[], ["hit"]])
    assert search("freemp3cloud", "q", Context("mp3_320")) == ["hit"]
    assert len(state["starts"]) == 2
    search, state = paced_search(monkeypatch, [])
    assert search("freemp3cloud", "q", Context("mp3_320")) == []
    assert len(state["starts"]) == 2


def published_journal(store, worker, audio, name):
    """A valid journal for a song already in the library: recovery would
    complete it and then delete the journal."""
    job = queued(store)
    store.request_state(job["id"], "validating")
    target = worker.library_root / f"{name}.mp3"
    shutil.copyfile(audio, target)
    return job, {
        **job,
        "codec": "mp3",
        "bitrate": 320,
        "relative_path": f"{name}.mp3",
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
    }


def test_recovery_keeps_a_valid_journal_behind_a_linked_staging_folder(tmp_path, audio):
    """Codex confirmation round (D-71): the delete after a successful
    recovery was unguarded and followed a linked staging folder."""
    store = Store(tmp_path / "data")
    worker = Downloader(store)
    job, metadata = published_journal(store, worker, audio, "linked")
    stage = store.directory / "downloads" / "staging"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    shutil.rmtree(stage)
    stage.symlink_to(elsewhere, target_is_directory=True)
    journal = elsewhere / (job["id"] + ".json")
    journal.write_text(json.dumps(metadata))
    store.recover_requests()
    assert journal.exists()


def test_recovery_never_deletes_a_linked_journal(tmp_path, audio):
    store = Store(tmp_path / "data")
    worker = Downloader(store)
    job, metadata = published_journal(store, worker, audio, "pointed")
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(metadata))
    link = store.directory / "downloads" / "staging" / (job["id"] + ".json")
    link.symlink_to(outside)
    store.recover_requests()
    assert link.is_symlink() and outside.exists()


def test_publication_never_writes_or_deletes_journals_through_links(tmp_path, audio):
    """Codex confirmation round 2 (D-71): commit_request wrote its journal
    into a linked staging folder (or through a linked journal, truncating its
    target) and deleted it unconditionally on success or collision."""
    import pytest

    store = Store(tmp_path / "data")
    job = queued(store)
    worker = Downloader(store)
    store.request_state(job["id"], "validating")
    probe = {"codec": "mp3", "bitrate_kbps": 320, "duration": 3}
    sha = hashlib.sha256(audio.read_bytes()).hexdigest()

    # A journal file that is a link: its target keeps its contents.
    stage = store.directory / "downloads" / "staging"
    outside = tmp_path / "outside.json"
    outside.write_text("keep me")
    name = job["id"] + "-" + job.get("claim_token", "legacy") + ".json"
    (stage / name).symlink_to(outside)
    staged = worker.staging / "staged.mp3"
    shutil.copyfile(audio, staged)
    with pytest.raises(ValueError):
        store.commit_request(job, staged, worker.library_root / "a.mp3", probe, sha)
    assert outside.read_text() == "keep me" and (stage / name).is_symlink()
    (stage / name).unlink()

    # A linked staging folder: nothing is written or deleted there.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "theirs.json").write_text("theirs")
    shutil.rmtree(stage)
    stage.symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(ValueError):
        store.commit_request(job, staged, worker.library_root / "b.mp3", probe, sha)
    assert sorted(p.name for p in elsewhere.iterdir()) == ["theirs.json"]
    assert not (worker.library_root / "b.mp3").exists()


def test_each_job_logs_one_timing_line_and_keeps_timings_in_the_queue(
    tmp_path, audio, caplog
):
    """#134: where a download's time went is in the log and the payload."""
    store = Store(tmp_path / "data")
    job = queued(store)
    assert job["queued_seconds"] >= 0
    assert "queued_seconds" not in store.queue()["items"][0]

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
    with caplog.at_level("INFO", logger="pig_music.downloads"):
        worker.process(job)
    lines = [r.getMessage() for r in caplog.records if r.name == "pig_music.downloads"]
    assert len(lines) == 1
    assert lines[0].startswith("Download: Test Artist - Test Track completed in ")
    for part in ("queued ", "search freemp3cloud mp3_320 1 candidates ok"):
        assert part in lines[0]
    for step in ("transfer freemp3cloud", "validate ok", "decode ok", "commit ok"):
        assert step in lines[0]
    item = store.queue()["items"][0]
    assert item["status"] == "completed"
    steps = [s["step"] for s in item["timings"]["steps"]]
    assert steps == ["search", "transfer", "validate", "tag", "decode", "hash"]
    assert item["timings"]["steps"][1]["bytes"] == audio.stat().st_size
    assert item["timings"]["queued_seconds"] is not None


def test_a_failed_job_keeps_its_timings_and_says_why(tmp_path, caplog):
    store = Store(tmp_path / "data")
    job = queued(store)
    worker = Downloader(store, {"freemp3cloud": lambda *_: []})
    with caplog.at_level("INFO", logger="pig_music.downloads"):
        worker.process(job)
    item = store.queue()["items"][0]
    assert item["status"] == "failed"
    assert item["timings"]["steps"][0]["candidates"] == 0
    (line,) = [
        r.getMessage() for r in caplog.records if r.name == "pig_music.downloads"
    ]
    assert " failed in " in line and "0 candidates" in line


def test_source_breaker_opening_and_closing_is_logged(caplog):
    from server.providers.contracts import ProviderError
    from server.providers.health import Health

    now = [1000.0]
    health = Health(clock=lambda: now[0])
    with caplog.at_level("INFO", logger="pig_music.downloads"):
        for _ in range(3):
            health.outcome("soulseek", error=ProviderError("unavailable"))
        health.outcome("soulseek")
    messages = [r.getMessage() for r in caplog.records]
    assert messages[0].startswith("Source soulseek paused for 300 s")
    assert messages[-1] == "Source soulseek available again"
    assert len(messages) == 2


def test_job_checks_reuse_the_nas_check_and_settings_for_a_second(
    tmp_path, monkeypatch
):
    """#135: check() runs every 50 ms during a decode; each call read the NAS
    marker and the settings (about 1,200 marker reads in a 60 s decode)."""
    import server.storage as storage

    store = Store(tmp_path / "data")
    job = queued(store)
    monkeypatch.setenv("PIG_MUSIC_DOWNLOAD_ROOT", str(tmp_path / "work"))
    reads = {"marker": 0, "settings": 0}
    monkeypatch.setattr(
        storage,
        "require_library_mount",
        lambda *a, **k: reads.__setitem__("marker", reads["marker"] + 1),
    )
    worker = Downloader(store, {})
    worker.context.claim_token = job["claim_token"]
    settings = store.download_settings
    monkeypatch.setattr(
        store,
        "download_settings",
        lambda: reads.__setitem__("settings", reads["settings"] + 1) or settings(),
    )
    reads.update(marker=0, settings=0)
    monkeypatch.setattr(storage, "_MOUNT_CHECKED", {})
    for _ in range(200):  # A 10 s decode's checks, in well under a second.
        worker.check(job["id"])
    assert reads == {"marker": 1, "settings": 1}
    # Pausing downloads still stops the job within the second.
    store.save_download_settings({"enabled": False})
    worker.settings_cache = (float("-inf"), None)
    with pytest.raises(Cancelled):
        worker.check(job["id"])


def test_a_download_is_tagged_and_given_its_cover_in_one_save(
    tmp_path, audio, monkeypatch
):
    """#137: tags and the cover were two saves; an ID3 tag that grows
    rewrites the whole file each time."""
    import io

    from mutagen.id3 import ID3
    from PIL import Image

    store = Store(tmp_path / "data")
    job = queued(store)
    picture = io.BytesIO()
    Image.new("RGB", (600, 600), "red").save(picture, "JPEG")
    sha = store.covers.save(picture.getvalue())
    job = {**job, "cover_sha": sha}

    class FixtureDownloader(Downloader):
        def transfer(self, job_id, candidate, path):
            shutil.copyfile(audio, path)

    saves = []
    original = ID3.save
    monkeypatch.setattr(
        ID3, "save", lambda self, *a, **k: saves.append(1) or original(self, *a, **k)
    )
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
    assert len(saves) == 1
    track = Store(store.directory).library["albums"][0]["tracks"][0]
    path = store.directory / "downloads" / "library" / track["relative_path"]
    tags = ID3(path)
    assert tags.getall("APIC")[0].data == store.covers.path(sha).read_bytes()
    assert str(tags["TIT2"]) == "Test Track"


def slow_publication(tmp_path, audio, monkeypatch, during):
    """A claimed, validated download whose move into the library takes
    0.6 s, running `during()` from another thread meanwhile."""
    import threading
    import time

    import server.store as store_module

    store = Store(tmp_path / "data")
    job = queued(store)
    worker = Downloader(store)
    store.request_state(job["id"], "validating", _claim_token=job["claim_token"])
    staged = worker.staging / "staged.mp3"
    shutil.copyfile(audio, staged)
    real = store_module.os.replace
    moving = threading.Event()

    def slow_replace(source, target):
        if Path(source) == staged:
            moving.set()
            time.sleep(0.6)
        return real(source, target)

    monkeypatch.setattr(store_module.os, "replace", slow_replace)
    result = {}

    def other():
        moving.wait(5)
        began = time.monotonic()
        during(store, job)
        result["seconds"] = time.monotonic() - began

    thread = threading.Thread(target=other)
    thread.start()
    probe = {"codec": "mp3", "bitrate_kbps": 320, "duration": 3.0}
    target = worker.library_root / "Test Artist" / "Test Album" / "song.mp3"
    published = store.commit_request(
        job, staged, target, probe, hashlib.sha256(audio.read_bytes()).hexdigest()
    )
    thread.join()
    return store, job, staged, target, published, result


def test_a_slow_move_into_the_library_does_not_hold_the_database(
    tmp_path, audio, monkeypatch
):
    """#138: the rename over SMB ran inside BEGIN IMMEDIATE, so a request
    submitted meanwhile waited for it (and failed past 10 s)."""
    import plistlib

    def submit(store, job):
        inventory = plistlib.dumps(
            {"Tracks": {"9": {"Name": "Other", "Artist": "B", "Album": "C"}}}
        )
        source = store.import_apple(inventory)
        track = next(
            t
            for album in source["albums"]
            for t in album["tracks"]
            if t["title"] == "Other"
        )
        store.update_selection([track["id"]], True)
        store.submit_migration()

    store, job, staged, target, published, result = slow_publication(
        tmp_path, audio, monkeypatch, submit
    )
    assert published and target.exists()
    assert result["seconds"] < 0.4
    assert {i["status"] for i in store.queue()["items"]} == {
        "completed",
        "waiting_for_sources",
    }


def test_a_request_cancelled_while_its_file_moves_is_not_published(
    tmp_path, audio, monkeypatch
):
    def cancel(store, job):
        store.cancel_request(job["id"])

    store, job, staged, target, published, _ = slow_publication(
        tmp_path, audio, monkeypatch, cancel
    )
    assert not published
    assert not target.exists() and staged.exists()  # Back in staging.
    assert store.queue()["items"][0]["status"] == "cancelled"
    assert list(staged.parent.glob("*.json")) == []
    assert Store(store.directory).library["stats"]["tracks"] == 0


def test_an_albums_publications_share_one_rebuild_and_one_revision(
    tmp_path, monkeypatch
):
    """#136: each publication rebuilt the library (1-2 s live) and changed
    its revision, and the export of each rebuilt it again: a 12-song album
    gave 24+ rebuilds and as many "library changed" events per device."""
    import server.store as store_module

    monkeypatch.setattr(store_module, "REBUILD_DEBOUNCE", 0.3, raising=False)
    store = Store(tmp_path / "data")
    builds = []
    real = store.refresh_library

    def counted():
        builds.append(store.library_revision)
        real()

    monkeypatch.setattr(store, "refresh_library", counted)
    monkeypatch.setattr(store, "commit_request", lambda *a: True)
    exported = []
    store.__dict__.setdefault("publish_listeners", []).append(
        lambda: exported.append(1)
    )
    # What the exporter asks for once it has published the song.
    export_rebuild = getattr(store, "request_refresh", store.refresh_library)
    before = store.library_revision
    for i in range(12):
        store.publish_request({"id": str(i)}, None, None, None, None)
        export_rebuild()
    assert getattr(store, "settle_rebuilds", lambda: True)()
    assert len(builds) == 1
    assert len(exported) == 12  # The exporter is woken for each.
    revisions = {before, store.library_revision}
    assert len(revisions) <= 2


def test_recovery_undoes_the_move_of_a_request_cancelled_while_it_moved(
    tmp_path, audio, monkeypatch
):
    """Codex round 1 on #138: cancelled while its file moved in, then stopped
    before the move was undone (here: the move back fails); recovery found
    the file, saw the request cancelled and only dropped the journal,
    leaving the file in the library unrecorded."""
    import threading
    import time

    import server.store as store_module

    store = Store(tmp_path / "data")
    job = queued(store)
    worker = Downloader(store)
    store.request_state(job["id"], "validating", _claim_token=job["claim_token"])
    staged = worker.staging / "staged.mp3"
    shutil.copyfile(audio, staged)
    real = store_module.os.replace
    moving = threading.Event()

    def replace(source, target):
        if Path(source) == staged:
            moving.set()
            time.sleep(0.3)
            return real(source, target)
        raise OSError("stopped before the move was undone")

    monkeypatch.setattr(store_module.os, "replace", replace)
    canceller = threading.Thread(
        target=lambda: (moving.wait(5), store.cancel_request(job["id"]))
    )
    canceller.start()
    target = worker.library_root / "Test Artist" / "Test Album" / "song.mp3"
    probe = {"codec": "mp3", "bitrate_kbps": 320, "duration": 3.0}
    with pytest.raises(OSError):
        store.commit_request(
            job, staged, target, probe, hashlib.sha256(audio.read_bytes()).hexdigest()
        )
    canceller.join()
    monkeypatch.setattr(store_module.os, "replace", real)
    assert target.exists()  # The move was never undone.
    store.recover_requests()
    assert not target.exists()
    assert list((store.directory / "downloads" / "staging").glob("*.json")) == []
    assert store.queue()["items"][0]["status"] == "cancelled"


def test_recovery_never_deletes_a_file_it_did_not_move_in(tmp_path, audio):
    """A journal whose publication found the same bytes already in place
    (an earlier copy) leaves that file alone when its claim is gone."""
    store = Store(tmp_path / "data")
    job = queued(store)
    worker = Downloader(store)
    target = worker.library_root / "kept.mp3"
    shutil.copyfile(audio, target)
    metadata = {
        **job,
        "codec": "mp3",
        "bitrate": 320,
        "relative_path": "kept.mp3",
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "_request_id": job["id"],
        "_claim_token": "an-older-claim",
        "_moved": False,
    }
    (worker.staging.parent.parent / "downloads").mkdir(exist_ok=True)
    journal = store.directory / "downloads" / "staging" / (job["id"] + "-x.json")
    journal.write_text(json.dumps(metadata))
    store.recover_requests()
    assert target.exists() and not journal.exists()
