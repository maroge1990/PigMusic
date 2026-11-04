import json
import os
import shutil
import subprocess
import time

import httpx
import pytest
from mutagen.flac import FLAC

from server.downloader import Cancelled, Downloader
from server.soulseek import Soulseek, candidate_for
from server.store import Store


def song(title="Song", number=1):
    return {
        "title": title,
        "artist": "Example",
        "album": "Studio",
        "album_artist": "Example",
        "year": 2020,
        "disc_number": 1,
        "track_number": number,
        "duration": 3,
        "quality_profile": "mp3_320",
        "status": "missing",
    }


def file(title="Song", codec="mp3"):
    return {
        "filename": f"Music\\Example\\Studio\\01 {title}.{codec}",
        "size": 10000,
        "length": 3,
        "bitRate": 320,
    }


def configured(tmp_path):
    store = Store(tmp_path)
    (tmp_path / "slskd-connection.json").write_text(
        json.dumps(
            {
                "url": "http://localhost:5031",
                "api_key": "test-only",
                "download_root": str(tmp_path / "engine"),
            }
        )
    )
    return store


def test_filename_recording_album_and_quality_gates():
    response = {"username": "peer"}
    assert candidate_for(song(), response, file())
    for bad in (
        file("Song Two"),
        file("Song (Live)"),
        {**file(), "bitRate": 128},
        {**file(), "length": 60},
        {**file(), "filename": "Other\\Other Album\\Song.mp3"},
        file(codec="flac"),
    ):
        assert candidate_for(song(), response, bad) is None
    assert candidate_for(
        {**song(), "quality_profile": "lossless"}, response, file(codec="flac")
    )


def test_album_search_prefers_folder_coverage_and_reuses_results(tmp_path):
    store = configured(tmp_path)
    store.submit_catalogue([song(), song("Second", 2)])
    calls = []
    responses = [
        {"username": "partial", "queueLength": 0, "files": [file()]},
        {"username": "complete", "queueLength": 1, "files": [file(), file("Second")]},
    ]

    def handler(request):
        calls.append(request)
        if request.method == "POST":
            assert json.loads(request.content)["searchText"] == "Example Studio"
            return httpx.Response(200, json={"id": "search-id"})
        if request.url.path.endswith("responses"):
            return httpx.Response(200, json=responses)
        return httpx.Response(200, json={"isComplete": True})

    adapter = Soulseek(store)
    adapter.client = lambda: httpx.Client(
        base_url="http://engine/api/v0/", transport=httpx.MockTransport(handler)
    )
    candidates = adapter.search(song(), lambda: None)
    assert (
        candidates[0]["username"] == "complete" and candidates[0]["album_coverage"] == 2
    )
    assert adapter.search(song("Second", 2), lambda: None)[0]["username"] == "complete"
    assert sum(r.method == "POST" for r in calls) == 1


def test_peer_failure_and_cancellation_do_not_read_uncompleted_files(tmp_path):
    adapter = Soulseek(configured(tmp_path))
    deletes = []

    def handler(request):
        if request.method == "DELETE":
            deletes.append(request.url.path)
            return httpx.Response(204)
        return httpx.Response(
            200,
            json={
                "directories": [
                    {
                        "files": [
                            {
                                **file(),
                                "id": "owned-id",
                                "stateDescription": "Completed, Errored",
                            }
                        ]
                    }
                ]
            },
        )

    adapter.client = lambda: httpx.Client(
        base_url="http://engine/api/v0/", transport=httpx.MockTransport(handler)
    )
    adapter.copy_completed = lambda *_: pytest.fail(
        "Failed peer data must not be imported"
    )
    candidate = candidate_for(song(), {"username": "peer"}, file())
    with pytest.raises(ValueError, match="peer transfer failed"):
        adapter.transfer(candidate, tmp_path / "bad.mp3", lambda: None, lambda *_: None)
    assert deletes == ["/api/v0/transfers/downloads/peer/owned-id"]
    with pytest.raises(Cancelled):
        adapter.transfer(
            candidate,
            tmp_path / "bad.mp3",
            lambda: (_ for _ in ()).throw(Cancelled()),
            lambda *_: None,
        )


def test_flac_complete_import_and_lossy_rejection(tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg required")
    fixture = tmp_path / "fixture.flac"
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=duration=3",
            "-codec:a",
            "flac",
            str(fixture),
        ],
        check=True,
        timeout=20,
    )
    store = configured(tmp_path / "data")
    store.save_download_settings(
        {"enabled": True, "profile": "lossless", "sources": ["soulseek"]}
    )
    store.submit_catalogue([song()])
    job = store.claim_request()
    worker = Downloader(store)
    candidate = {
        "title": "Song",
        "channel": "Example",
        "album": "Studio",
        "duration": 3,
        "extension": ".flac",
    }
    worker.soulseek.search = lambda *_: [candidate]
    worker.soulseek.transfer = lambda candidate, path, *_: shutil.copyfile(
        fixture, path
    )
    worker.process(job)
    assert store.queue()["items"][0]["status"] == "completed"
    track = Store(store.directory).library["albums"][0]["tracks"][0]
    path = worker.library_root / track["relative_path"]
    assert track["codec"] == "flac" and path.suffix == ".flac"
    assert FLAC(path)["albumartist"] == ["Example"]
    with pytest.raises(ValueError, match="quality profile"):
        worker.validate(path, job, "mp3_320")


def test_completed_file_lookup_rejects_ambiguity_and_outside_files(tmp_path):
    adapter = Soulseek(configured(tmp_path / "data"))
    root = tmp_path / "data" / "engine"
    (root / "Studio").mkdir(parents=True)
    (root / "Studio" / "01 Song.mp3").write_bytes(b"1" * 10000)
    candidate = candidate_for(song(), {"username": "peer"}, file())
    adapter.copy_completed(candidate, tmp_path / "copy.mp3")
    assert (tmp_path / "copy.mp3").read_bytes() == b"1" * 10000
    # The engine saved a second file of that name beside it: ambiguous.
    (root / "Studio" / "01 Song_638650000000000000.mp3").write_bytes(b"2" * 10000)
    with pytest.raises(ValueError, match="uniquely"):
        adapter.copy_completed(candidate, tmp_path / "copy.mp3")
    (root / "Studio" / "01 Song_638650000000000000.mp3").unlink()
    # A link out of the download folder is never read.
    outside = tmp_path / "outside.mp3"
    outside.write_bytes(b"3" * 10000)
    (root / "Studio" / "01 Song.mp3").unlink()
    (root / "Studio" / "01 Song.mp3").symlink_to(outside)
    with pytest.raises(ValueError, match="uniquely"):
        adapter.copy_completed(candidate, tmp_path / "copy.mp3")
    # A remote name climbing out of the folder is refused.
    climbing = {**candidate, "filename": "Music\\..\\01 Song.mp3"}
    with pytest.raises(ValueError):
        adapter.copy_completed(climbing, tmp_path / "copy.mp3")


def test_completed_files_are_found_without_walking_and_streamed(tmp_path, monkeypatch):
    """#96: the import walked the engine's whole download tree and read the
    file (up to 256 MB, six at once) into memory."""
    import pathlib
    import tracemalloc

    adapter = Soulseek(configured(tmp_path / "data"))
    root = tmp_path / "data" / "engine"
    (root / "Studio").mkdir(parents=True)
    size = 24_000_000
    with (root / "Studio" / "01 Song.mp3").open("wb") as handle:
        handle.truncate(size)
    candidate = {
        **candidate_for(song(), {"username": "peer"}, file()),
        "size": size,
    }
    monkeypatch.setattr(
        pathlib.Path, "rglob", lambda *_: pytest.fail("walked the whole tree")
    )
    monkeypatch.setattr(os, "walk", lambda *_: pytest.fail("walked the tree"))
    tracemalloc.start()
    try:
        adapter.copy_completed(candidate, tmp_path / "copy.mp3")
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert (tmp_path / "copy.mp3").stat().st_size == size
    assert peak < 8_000_000, peak
    # Outside the engine's own folder nothing is deleted.
    assert (root / "Studio" / "01 Song.mp3").exists()


def test_engine_imports_link_and_old_leftovers_are_swept_from_its_own_folders(
    tmp_path, monkeypatch
):
    work = tmp_path / "work"
    downloads, incomplete = work / "engine-downloads", work / "engine-incomplete"
    monkeypatch.setenv("PIG_MUSIC_DOWNLOAD_ROOT", str(work))
    store = configured(tmp_path / "data")
    connection = tmp_path / "data" / "slskd-connection.json"
    config = json.loads(connection.read_text())
    connection.write_text(json.dumps({**config, "download_root": str(downloads)}))
    adapter = Soulseek(store)
    old = time.time() - 2 * 86400
    leftovers = [downloads / "Gone" / "cancelled.mp3", incomplete / "x" / "part.mp3"]
    for path in leftovers:
        path.parent.mkdir(parents=True)
        path.write_bytes(b"x")
        os.utime(path, (old, old))
    fresh = downloads / "Other" / "transferring.mp3"
    fresh.parent.mkdir(parents=True)
    fresh.write_bytes(b"y")
    outside = tmp_path / "keep.mp3"
    outside.write_bytes(b"z")
    os.utime(outside, (old, old))
    (downloads / "Other" / "link.mp3").symlink_to(outside)
    (downloads / "Studio").mkdir()
    (downloads / "Studio" / "01 Song.mp3").write_bytes(b"1" * 10000)
    candidate = candidate_for(song(), {"username": "peer"}, file())
    destination = work / "import.mp3"
    adapter.copy_completed(candidate, destination)
    # Imported by a hard link (no copy), and the engine's file is gone.
    assert destination.read_bytes() == b"1" * 10000
    assert not (downloads / "Studio" / "01 Song.mp3").exists()
    assert not any(path.exists() for path in leftovers)
    assert not (downloads / "Gone").exists()
    assert fresh.exists() and outside.exists()
    assert (downloads / "Other" / "link.mp3").is_symlink()


def fallback_adapter(tmp_path, album_only_responses, primary_responses):
    store = configured(tmp_path)
    store.submit_catalogue([song()])
    posts = []

    def handler(request):
        if request.method == "POST":
            text = json.loads(request.content)["searchText"]
            posts.append(text)
            return httpx.Response(200, json={"id": text.replace(" ", "-")})
        if request.url.path.endswith("responses"):
            primary = "Example-Studio" in request.url.path
            return httpx.Response(
                200, json=primary_responses if primary else album_only_responses
            )
        return httpx.Response(200, json={"isComplete": True})

    adapter = Soulseek(store)
    adapter.client = lambda: httpx.Client(
        base_url="http://engine/api/v0/", transport=httpx.MockTransport(handler)
    )
    return adapter, posts


def test_album_only_search_runs_when_artist_search_is_empty(tmp_path):
    real = [{"username": "peer", "queueLength": 0, "files": [file()]}]
    adapter, posts = fallback_adapter(tmp_path, real, [])
    candidates = adapter.search(song(), lambda: None)
    assert candidates and candidates[0]["username"] == "peer"
    assert posts == ["Example Studio", "Studio"]


def test_album_only_search_skipped_when_artist_search_has_responses(tmp_path):
    real = [{"username": "peer", "queueLength": 0, "files": [file()]}]
    adapter, posts = fallback_adapter(tmp_path, [], real)
    assert adapter.search(song(), lambda: None)
    assert posts == ["Example Studio"]


def test_any_edition_requests_use_the_edition_free_title_and_lead_artist():
    response = {"username": "peer"}
    remaster = {**song("Song - 2004 Remaster"), "artist": "Example feat. Guest"}
    assert candidate_for(remaster, response, file())
    assert candidate_for(remaster, response, file("Song (2004 Remaster)"))
    for other in (file("Song (Live)"), file("Song Two"), file("Song (Remix)")):
        assert candidate_for(remaster, response, other) is None
    live = {**song(), "recording_kind": ["live"]}
    assert candidate_for(live, response, file()) is None
    # An explicit edition still needs its exact title.
    assert (
        candidate_for({**remaster, "explicit_edition": True}, response, file()) is None
    )
