import asyncio
import hashlib
import json
import shutil
import subprocess
import threading
import time

import pytest

from server.catalogue import edition_rank
from server.downloader import Downloader
from server.library import build_library
from server.store import Store


@pytest.fixture
def source_audio(tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg is needed to validate source downloads")
    paths = {}
    for profile, extension, arguments in [
        ("mp3_320", ".mp3", ["-codec:a", "libmp3lame", "-b:a", "320k"]),
        ("mp3_128", ".mp3", ["-codec:a", "libmp3lame", "-b:a", "128k"]),
        ("lossless", ".flac", ["-codec:a", "flac"]),
    ]:
        path = tmp_path / (profile + extension)
        subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=3",
                *arguments,
                str(path),
            ],
            check=True,
            timeout=20,
        )
        paths[profile] = path
    return paths


def requested(store, title="Song", album="Album"):
    return {
        "id": title + album,
        "title": title,
        "artist": "Artist",
        "album_artist": "Artist",
        "album": album,
        "duration": 3,
        "year": 2020,
        "disc_number": 1,
        "track_number": 1,
        "musicbrainz_release_id": album,
        "musicbrainz_recording_id": title,
        "status": "missing",
    }


def enabled(store):
    (store.directory / "slskd-connection.json").write_text("{}")
    store.save_download_settings(
        {"enabled": True, "sources": ["soulseek", "freemp3cloud"]}
    )


def fixture_worker(store, files, calls):
    class Worker(Downloader):
        def transfer(self, job_id, candidate, path):
            calls.append(candidate["fixture_profile"])
            shutil.copyfile(files[candidate["fixture_profile"]], path)

    candidate = {"title": "Song", "channel": "Artist", "album": "Album", "duration": 3}
    worker = Worker(
        store,
        {
            "freemp3cloud": lambda *_: [
                {**candidate, "fixture_profile": "mp3_320", "quality": "320kbps"}
            ]
        },
    )
    worker.soulseek.search = lambda job, check: [
        {
            **candidate,
            "fixture_profile": job["quality_profile"],
            "extension": files[job["quality_profile"]].suffix,
        }
    ]
    return worker


def acquire(store, worker):
    store.submit_catalogue([requested(store)])
    worker.process(store.claim_request())
    assert store.queue()["items"][0]["status"] == "completed"
    return store.library["albums"][0]["tracks"][0]


def test_upgrade_and_downgrade_download_sources_keep_stable_identity_and_remove_old(
    tmp_path, source_audio, monkeypatch
):
    store = Store(tmp_path / "data")
    enabled(store)
    calls = []
    worker = fixture_worker(store, source_audio, calls)
    track = acquire(store, worker)
    track_id = track["id"]
    old = worker.library_root / track["relative_path"]
    original_run = subprocess.Popen

    def validation_only(command, *args, **kwargs):
        # Runtime FFmpeg may decode for validation, but must never encode a replacement.
        assert "-codec:a" not in command and "-b:a" not in command
        return original_run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", validation_only)
    album_id = store.library["albums"][0]["id"]
    assert store.quality_override("album", album_id, "lossless")["created"] == 1
    assert old.exists()
    worker.process(store.claim_request())
    upgraded = store.library["albums"][0]["tracks"][0]
    assert upgraded["id"] == track_id and upgraded["codec"] == "flac"
    assert not old.exists()
    flac = worker.library_root / upgraded["relative_path"]
    assert store.quality_override("album", album_id, "mp3_320")["created"] == 1
    assert flac.exists()
    worker.process(store.claim_request())
    downgraded = Store(store.directory).library["albums"][0]["tracks"][0]
    assert downgraded["id"] == track_id and downgraded["codec"] == "mp3"
    assert downgraded["bitrate"] >= 300 and not flac.exists()
    assert calls == ["mp3_320", "lossless", "mp3_320"]
    trash = worker.library_root / ".pig-music-trash"
    files = [p for p in worker.library_root.rglob("*.*") if p.is_file()]
    assert len([p for p in files if not p.is_relative_to(trash)]) == 1
    # Both replaced files (the first MP3, then the FLAC) wait in Trash.
    assert sorted(p.suffix for p in trash.rglob("*") if p.is_file()) == [
        "",
        ".flac",
        ".mp3",
    ]
    assert store.library["stats"]["tracks"] == 1


def test_failed_replacement_retains_original_and_128_rejects_320_source(
    tmp_path, source_audio
):
    store = Store(tmp_path / "data")
    enabled(store)
    worker = fixture_worker(store, source_audio, [])
    track = acquire(store, worker)
    path = worker.library_root / track["relative_path"]
    content = path.read_bytes()
    store.quality_override("album", store.library["albums"][0]["id"], "mp3_128")
    worker.soulseek.search = lambda *_: []
    worker.process(store.claim_request())
    assert (
        next(i for i in store.queue()["items"] if i.get("library_track_id"))["status"]
        == "failed"
    )
    assert path.read_bytes() == content and store.library["stats"]["tracks"] == 1
    # A server advertising 128 but actually sending 320 must also be rejected.
    files = {**source_audio, "mp3_128": source_audio["mp3_320"]}
    worker = fixture_worker(store, files, [])
    store.quality_override("album", store.library["albums"][0]["id"], "mp3_128")
    worker.process(store.claim_request())
    assert path.read_bytes() == content
    assert store.library["albums"][0]["tracks"][0]["bitrate"] >= 300


def test_preference_hierarchy_and_reset_preserve_favourite_album(tmp_path):
    snapshot = {
        "tracks": [
            {**requested(None, album=album), "codec": "mp3", "bitrate": 320}
            for album in ["Favourite", "Other"]
        ]
    }
    (tmp_path / "library-snapshot.json").write_text(json.dumps(snapshot))
    store = Store(tmp_path)
    artist = store.library["artists"][0]
    favourite = next(a for a in store.library["albums"] if a["title"] == "Favourite")
    other = next(a for a in store.library["albums"] if a["title"] == "Other")
    store.quality_override("album", favourite["id"], "lossless")
    store.quality_override("artist", artist["id"], "mp3_128")
    assert (
        store.quality_status("album", favourite["id"])["effective_profile"]
        == "lossless"
    )
    assert store.quality_status("album", other["id"])["effective_profile"] == "mp3_128"
    store.quality_override("artist", artist["id"], None)
    assert (
        store.quality_status("album", favourite["id"])["effective_profile"]
        == "lossless"
    )
    assert store.quality_status("album", other["id"])["effective_profile"] == "mp3_320"
    store.quality_override("album", favourite["id"], None)
    assert (
        store.quality_status("album", favourite["id"])["effective_profile"] == "mp3_320"
    )
    assert all(i["status"] == "cancelled" for i in store.queue()["items"])


def test_fallback_retains_lossless_preference_and_daily_retry_never_replaces_with_fallback(
    tmp_path, source_audio
):
    store = Store(tmp_path / "data")
    enabled(store)
    store.save_download_settings({"profile": "lossless"})
    calls = []
    worker = fixture_worker(store, source_audio, calls)
    worker.soulseek.search = lambda *_: []
    track = acquire(store, worker)
    assert track["codec"] == "mp3"
    assert track["quality_policy"]["preferred"] == "lossless"
    with store.connect() as conn:
        value = store.read_document("quality-policy:" + track["id"])
        value["next_check"] = 0
        conn.execute(
            "UPDATE documents SET payload=? WHERE name=?",
            (json.dumps(value), "quality-policy:" + track["id"]),
        )
    store.schedule_upgrades()
    retry = store.claim_request()
    assert retry["upgrade_only"] and retry["quality_profile"] == "lossless"
    path = worker.library_root / track["relative_path"]
    worker.process(retry)
    assert calls == ["mp3_320"] and path.exists()
    assert store.library["stats"]["tracks"] == 1


def test_cleanup_requires_both_recorded_hashes_and_contained_paths(tmp_path):
    store = Store(tmp_path)
    root = tmp_path / "downloads" / "library"
    root.mkdir(parents=True)
    old = root / "old.mp3"
    new = root / "new.flac"
    old.write_bytes(b"old")
    new.write_bytes(b"new")
    old_metadata = {
        "relative_path": old.name,
        "sha256": hashlib.sha256(b"old").hexdigest(),
    }
    new_metadata = {
        "relative_path": new.name,
        "sha256": hashlib.sha256(b"new").hexdigest(),
    }
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO acquired VALUES (?,?)", ("track", json.dumps(new_metadata))
        )
        conn.execute(
            "INSERT INTO quality_cleanup VALUES (?,?,?)",
            ("request", "track", json.dumps(old_metadata)),
        )
    new.write_bytes(b"corrupt")
    store.cleanup_replacements()
    assert old.exists()
    new.write_bytes(b"new")
    old.write_bytes(b"changed outside app")
    store.cleanup_replacements()
    assert old.exists()
    old.write_bytes(b"old")
    store.cleanup_replacements()
    assert not old.exists() and new.exists()
    # With export off the replaced file goes to Trash, not away for good (#96).
    trash = root / ".pig-music-trash" / "request" / "old.mp3"
    assert trash.read_bytes() == b"old"
    assert (root / ".pig-music-trash" / ".plexignore").read_text() == "*\n"
    with store.connect() as conn:
        assert not conn.execute("SELECT 1 FROM quality_cleanup").fetchall()


def test_upgrade_scan_does_not_treat_unknown_quality_as_permission_to_downgrade(
    tmp_path,
):
    store = Store(tmp_path)
    enabled(store)
    track = {**requested(None), "codec": "mp3", "bitrate": 192}
    with store.connect() as conn:
        assert not store._enqueue_quality(
            conn, track, {"preferred": "mp3_128"}, automatic=True
        )
        assert store._enqueue_quality(
            conn, track, {"preferred": "mp3_128"}, automatic=False
        )


def test_workers_claim_distinct_jobs_and_respect_concurrency(tmp_path):
    store = Store(tmp_path)
    enabled(store)
    store.submit_catalogue([requested(store, title=f"Song {i}") for i in range(6)])
    lock = threading.Lock()
    seen = set()
    running = 0
    peak = 0

    class Worker(Downloader):
        def process(self, job):
            nonlocal running, peak
            with lock:
                assert job["id"] not in seen
                seen.add(job["id"])
                running += 1
                peak = max(peak, running)
            time.sleep(0.15)
            self.store.request_state(job["id"], "completed")
            with lock:
                running -= 1
                if len(seen) == 6 and running == 0:
                    self.stopping.set()

    worker = Worker(store)
    asyncio.run(asyncio.wait_for(worker.run(), timeout=5))
    assert len(seen) == 6 and peak == 3


def test_system_default_changes_only_managed_tracks_and_respects_album_override(
    tmp_path,
):
    nas = {**requested(None, album="NAS only"), "codec": "mp3", "bitrate": 320}
    (tmp_path / "library-snapshot.json").write_text(json.dumps({"tracks": [nas]}))
    store = Store(tmp_path)
    enabled(store)
    with store.connect() as conn:
        for album in ["Favourite", "Other"]:
            track = {**requested(None, album=album), "codec": "mp3", "bitrate": 320}
            conn.execute(
                "INSERT INTO acquired VALUES (?,?)", (track["id"], json.dumps(track))
            )
    store.refresh_library()
    favourite = next(a for a in store.library["albums"] if a["title"] == "Favourite")
    store.quality_override("album", favourite["id"], "lossless")
    store.save_download_settings({"profile": "mp3_128"})
    assert {(i["album"], i["quality_profile"]) for i in store.queue()["items"]} == {
        ("Favourite", "lossless"),
        ("Other", "mp3_128"),
    }
    assert (
        store.read_document("quality-policy:SongFavourite")["preferred"] == "lossless"
    )
    assert store.read_document("quality-policy:SongOther")["preferred"] == "mp3_128"


def test_replacement_journal_recovers_new_file_and_removes_superseded_file(
    tmp_path, source_audio
):
    store = Store(tmp_path / "data")
    enabled(store)
    worker = fixture_worker(store, source_audio, [])
    old = acquire(store, worker)
    old_path = worker.library_root / old["relative_path"]
    store.quality_override("album", store.library["albums"][0]["id"], "lossless")
    job = store.claim_request()
    store.request_state(job["id"], "validating")
    new_path = worker.library_root / "recovered.flac"
    shutil.copyfile(source_audio["lossless"], new_path)
    metadata = {
        **old,
        "codec": "flac",
        "relative_path": new_path.name,
        "sha256": hashlib.sha256(new_path.read_bytes()).hexdigest(),
        "_request_id": job["id"],
        "_claim_token": job["claim_token"],
        "quality_policy": job["quality_policy"],
    }
    journal = worker.staging / (job["id"] + ".json")
    journal.write_text(json.dumps(metadata), encoding="utf-8")
    restarted = Store(store.directory)
    restarted.recover_requests()
    assert restarted.library["stats"]["tracks"] == 1
    assert restarted.library["albums"][0]["tracks"][0]["id"] == old["id"]
    assert restarted.library["albums"][0]["tracks"][0]["codec"] == "flac"
    assert new_path.exists() and not old_path.exists() and not journal.exists()


def test_musicals_and_preferred_standard_editions():
    library = build_library(
        {
            "tracks": [
                {
                    **requested(None, album="Hamilton"),
                    "album_artist": "Original Broadway Cast",
                },
                {**requested(None, album="The Book of Mormon"), "album_artist": "Cast"},
                requested(None, album="Greatest Hits"),
            ]
        }
    )
    assert library["stats"]["musicals"] == 2 and library["stats"]["albums"] == 1
    standard = {
        "id": "standard",
        "status": "Official",
        "media": [{"format": "Digital Media"}],
        "date": "2020",
    }
    deluxe = {**standard, "id": "deluxe", "disambiguation": "Deluxe edition"}
    bootleg = {**standard, "id": "bootleg", "status": "Bootleg"}
    assert sorted([deluxe, bootleg, standard], key=edition_rank)[0] == standard


def test_rank_puts_aac_lowest_known_quality():
    from server.quality import rank

    order = ["unknown", "aac_256", "mp3_128", "mp3_320", "lossless"]
    assert [rank(p) for p in order] == [0, 1, 2, 3, 4]


def test_new_request_policies_are_marked_for_aac_fallback(tmp_path):
    store = Store(tmp_path / "data")
    from server.quality import policy

    assert "aac_fallback" not in store.download_settings()
    assert policy(store.download_settings())["aac_fallback"] is True


@pytest.fixture
def aac_audio(tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg is needed to validate source downloads")
    path = tmp_path / "aac.m4a"
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
            str(path),
        ],
        check=True,
        timeout=20,
    )
    return path


def aac_worker(store, aac_audio, calls, mp3=None):
    candidate = {"title": "Song", "channel": "Artist", "album": "Album", "duration": 3}

    class Worker(Downloader):
        def transfer(self, job_id, candidate, path):
            shutil.copyfile(aac_audio, path)

    def jio(*_):
        calls.append("jiosaavn")
        return [candidate]

    def mp3_search(*_):
        calls.append("mp3")
        return []

    worker = Worker(store, {"jiosaavn": jio, "freemp3cloud": mp3_search})
    worker.soulseek.search = lambda *_: []
    return worker


def failed_request(store, worker, legacy=False, **settings):
    (store.directory / "slskd-connection.json").write_text("{}")
    store.save_download_settings(
        {
            "enabled": True,
            "sources": ["soulseek", "freemp3cloud", "jiosaavn"],
            **settings,
        }
    )
    store.submit_catalogue([requested(store)])
    job = store.claim_request()
    if legacy:
        # Queued before AAC became the last resort: no marker in its policy.
        policy = {k: v for k, v in job["quality_policy"].items() if k != "aac_fallback"}
        job = {**job, "quality_policy": policy}
    worker.process(job)
    return store.queue()["items"][0]


@pytest.mark.parametrize(
    "settings",
    [
        {"profile": "lossless"},
        {"profile": "lossless", "fallback_profile": "mp3_128"},
        {"profile": "mp3_320"},
    ],
)
def test_aac_is_last_resort_when_jiosaavn_enabled(tmp_path, aac_audio, settings):
    store = Store(tmp_path / "data")
    calls = []
    worker = aac_worker(store, aac_audio, calls)
    item = failed_request(store, worker, **settings)
    assert item["status"] == "completed", item.get("error")
    assert calls[-1] == "jiosaavn" and "mp3" in calls
    saved = store.library["albums"][0]["tracks"][0]
    assert saved["codec"] == "aac"
    assert store.quality_policy(saved)["preferred"] == settings["profile"]


@pytest.mark.parametrize(
    "settings",
    [
        {"profile": "lossless", "fallback_profile": "none"},
        {"profile": "mp3_320", "sources": ["soulseek", "freemp3cloud"]},
        {"profile": "mp3_320", "legacy": True},
    ],
)
def test_aac_not_attempted_when_waiting_jiosaavn_off_or_queued_before(
    tmp_path, aac_audio, settings
):
    store = Store(tmp_path / "data")
    calls = []
    worker = aac_worker(store, aac_audio, calls)
    item = failed_request(store, worker, **settings)
    assert item["status"] == "failed" and "jiosaavn" not in calls


def test_aac_not_attempted_for_upgrade_jobs(tmp_path, aac_audio):
    store = Store(tmp_path / "data")
    calls = []
    worker = aac_worker(store, aac_audio, calls)
    store.save_download_settings(
        {
            "enabled": True,
            "sources": ["freemp3cloud", "jiosaavn"],
            "profile": "mp3_320",
        }
    )
    store.submit_catalogue([requested(store)])
    job = store.claim_request()
    worker.process({**job, "upgrade_only": True})
    assert "jiosaavn" not in calls


def test_aac_saved_track_is_upgraded_daily_without_fallback(tmp_path, aac_audio):
    from server.quality import actual_profile

    store = Store(tmp_path / "data")
    calls = []
    worker = aac_worker(store, aac_audio, calls)
    item = failed_request(store, worker, profile="mp3_320")
    assert item["status"] == "completed"
    track = store.library["albums"][0]["tracks"][0]
    assert actual_profile(track) == "aac_256"
    store.schedule_upgrades()
    assert store.claim_request() is None  # not due before next_check
    name = "quality-policy:" + track["id"]
    value = store.read_document(name)
    value["next_check"] = 0
    with store.connect() as conn:
        conn.execute(
            "UPDATE documents SET payload=? WHERE name=?", (json.dumps(value), name)
        )
    store.schedule_upgrades()
    retry = store.claim_request()
    assert retry["upgrade_only"] and retry["quality_profile"] == "mp3_320"
    calls.clear()
    worker.process(retry)
    assert "jiosaavn" not in calls
