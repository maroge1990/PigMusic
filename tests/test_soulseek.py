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


def test_a_file_validation_would_reject_for_its_length_is_never_a_candidate():
    """#139: selection used 8 s, validation 5 s; a 7 s-off file was
    transferred in full and then rejected."""
    response = {"username": "peer"}
    job = {**song(), "duration": 200}
    assert candidate_for(job, response, {**file(), "length": 205})
    assert candidate_for(job, response, {**file(), "length": 207}) is None
    assert candidate_for(job, response, {**file(), "length": 193}) is None


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
    report = adapter.reports.current = {}
    with pytest.raises(ValueError, match="peer transfer failed"):
        adapter.transfer(candidate, tmp_path / "bad.mp3", lambda: None, lambda *_: None)
    assert deletes == ["/api/v0/transfers/downloads/peer/owned-id"]
    # The job's timings say what the peer did (#134).
    assert report == {"state": "completed, errored"}
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


def test_engine_imports_link_and_leave_other_engine_files_alone(tmp_path, monkeypatch):
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
    # Nothing else is deleted, however old: age alone never shows a transfer
    # is abandoned (a stalled one can resume), so leftovers wait for a sweep
    # that asks the engine (#96).
    assert all(path.exists() for path in leftovers)
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


def engine(tmp_path, monkeypatch, transfers, work=None):
    """A work root with the engine's two folders, the expected library mount,
    and an engine that lists `transfers`: {file name: [state, ...]}, each
    from the remote folder "Studio" (a list per name: several peers)."""
    library = tmp_path / "library"
    library.mkdir()
    (library / ".pig-music-library.json").write_text('{"library_id": "test"}')
    data = tmp_path / "data"
    data.mkdir()
    (data / "library-export.json").write_text('{"library_id": "test"}')
    work = work or tmp_path / "work"
    monkeypatch.setenv("PIG_MUSIC_LIBRARY", str(library))
    monkeypatch.setenv("PIG_MUSIC_DOWNLOAD_ROOT", str(work))
    store = configured(data)
    connection = data / "slskd-connection.json"
    config = json.loads(connection.read_text())
    connection.write_text(
        json.dumps({**config, "download_root": str(work / "engine-downloads")})
    )
    peers = max((len(s) for s in transfers.values()), default=0)
    listed = [
        {
            "username": f"peer{index}",
            "directories": [
                {
                    "directory": "Music\\Example\\Studio",
                    "files": [
                        {
                            "filename": f"Music\\Example\\Studio\\{name}",
                            "size": 10000,
                            "state": states[index],
                        }
                        for name, states in transfers.items()
                        if index < len(states)
                    ],
                }
            ],
        }
        for index in range(peers)
    ]
    adapter = Soulseek(store)
    adapter.client = lambda: httpx.Client(
        base_url="http://engine/api/v0/",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=listed)),
    )
    return adapter


def partial(folder, name):
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    old = time.time() - 30 * 86400  # However old: age never decides.
    os.utime(path, (old, old))
    return path


def test_sweep_deletes_only_partials_of_transfers_the_engine_reports_failed(
    tmp_path, monkeypatch
):
    """#96: cancelled or failed transfers left their partial files in the
    engine's folder for good. Only those go: a stalled transfer can resume,
    and a file no transfer is listed for may be anything."""
    adapter = engine(
        tmp_path,
        monkeypatch,
        {
            "failed.mp3": ["Completed, Errored"],
            "cancelled.mp3": ["Completed, Cancelled"],
            "timed out.mp3": ["Completed, TimedOut"],
            "resumable.mp3": ["InProgress"],
            "queued.mp3": ["Queued, Remotely"],
            "succeeded.mp3": ["Completed, Succeeded"],
            # One peer failed, another is still sending the same file.
            "retrying.mp3": ["Completed, Errored", "Initializing"],
        },
    )
    work = tmp_path / "work"
    incomplete, downloads = work / "engine-incomplete", work / "engine-downloads"
    studio = incomplete / "Studio"
    gone = [
        partial(studio, n) for n in ("failed.mp3", "cancelled.mp3", "timed out.mp3")
    ]
    kept = [
        partial(studio, n)
        for n in ("resumable.mp3", "queued.mp3", "succeeded.mp3", "retrying.mp3")
    ]
    kept += [
        partial(studio, "unlisted.mp3"),
        partial(incomplete / "Other", "failed.mp3"),  # Another remote folder.
        partial(incomplete, "failed.mp3"),  # Not where the engine keeps it.
        partial(downloads / "Studio", "failed.mp3"),  # A finished file.
    ]
    (incomplete / "Linked").symlink_to(studio, target_is_directory=True)
    assert adapter.sweep() == 3
    assert not any(path.exists() for path in gone)
    assert all(path.exists() for path in kept)
    # At most hourly.
    again = partial(studio, "failed.mp3")
    assert adapter.sweep() == 0 and again.exists()


def test_sweep_never_follows_a_linked_partial(tmp_path, monkeypatch):
    adapter = engine(tmp_path, monkeypatch, {"failed.mp3": ["Completed, Failed"]})
    (tmp_path / "work" / "engine-downloads").mkdir(parents=True)
    studio = tmp_path / "work" / "engine-incomplete" / "Studio"
    studio.mkdir(parents=True)
    outside = partial(tmp_path, "outside.mp3")
    (studio / "failed.mp3").symlink_to(outside)
    adapter.sweep()
    assert outside.exists()


def test_sweep_never_deletes_the_file_a_transfer_is_importing(tmp_path, monkeypatch):
    adapter = engine(tmp_path, monkeypatch, {"01 Song.mp3": ["Completed, Cancelled"]})
    (tmp_path / "work" / "engine-downloads").mkdir(parents=True)
    path = partial(tmp_path / "work" / "engine-incomplete" / "Studio", "01 Song.mp3")
    swept = []

    def importing(candidate, destination, check, progress):
        swept.append(adapter.sweep())
        assert path.exists()

    adapter._transfer = importing
    candidate = candidate_for(song(), {"username": "peer"}, file())
    adapter.transfer(candidate, tmp_path / "staged.mp3", lambda: None, None)
    assert swept == [0] and path.exists()
    adapter.swept_at = None  # An hour later, nothing is importing it.
    assert adapter.sweep() == 1 and not path.exists()


@pytest.mark.parametrize(
    "linked", [None, "work", "engine-incomplete", "engine-downloads"]
)
def test_sweep_deletes_nothing_behind_a_linked_work_root_or_engine_folder(
    tmp_path, monkeypatch, linked
):
    """A link could lead the sweep into the library (Codex, 5 Nov)."""
    real = tmp_path / "real"
    elsewhere = tmp_path / "elsewhere"
    for name in ("engine-downloads", "engine-incomplete"):
        (real / name).mkdir(parents=True)
        (elsewhere / name).mkdir(parents=True)
    work = tmp_path / "work"
    if linked is None:
        work = real
    elif linked == "work":
        work.symlink_to(real, target_is_directory=True)
    else:
        work.mkdir()
        for name in ("engine-downloads", "engine-incomplete"):
            if name == linked:
                (work / name).symlink_to(elsewhere / name, target_is_directory=True)
            else:
                (work / name).mkdir()
    folder = "engine-incomplete"
    owner = elsewhere if linked == "engine-incomplete" else work
    path = partial(owner / folder / "Studio", "failed.mp3")
    adapter = engine(
        tmp_path, monkeypatch, {"failed.mp3": ["Completed, Errored"]}, work=work
    )
    assert adapter.sweep() == (1 if linked is None else 0)
    assert path.exists() == (linked is not None)


@pytest.mark.parametrize("answer", [500, {"unexpected": True}, [{"directories": 1}]])
def test_sweep_deletes_nothing_when_the_engine_list_is_unreadable(
    tmp_path, monkeypatch, answer
):
    adapter = engine(tmp_path, monkeypatch, {})
    (tmp_path / "work" / "engine-downloads").mkdir(parents=True)
    path = partial(tmp_path / "work" / "engine-incomplete" / "Studio", "failed.mp3")

    def reply(request):
        if isinstance(answer, int):
            return httpx.Response(answer)
        return httpx.Response(200, json=answer)

    adapter.client = lambda: httpx.Client(
        base_url="http://engine/api/v0/", transport=httpx.MockTransport(reply)
    )
    assert adapter.sweep() == 0 and path.exists()


class FakeSearchEngine:
    """An engine whose search gains `hits` at `hit_tick` and completes at
    `complete_tick` (counted in status reads)."""

    def __init__(self, hits, hit_tick, complete_tick):
        self.hits, self.hit_tick, self.complete_tick = hits, hit_tick, complete_tick
        self.ticks = 0
        self.log = []

    def handler(self, request):
        path = request.url.path
        if request.method == "POST":
            self.log.append(("POST", self.ticks))
            return httpx.Response(200, json={"id": "s1"})
        if request.method == "DELETE":
            self.log.append(("DELETE", self.ticks))
            return httpx.Response(204)
        if path.endswith("/responses"):
            return httpx.Response(
                200, json=self.hits if self.ticks >= self.hit_tick else []
            )
        self.ticks += 1
        return httpx.Response(
            200, json={"isComplete": self.ticks >= self.complete_tick}
        )

    def attach(self, adapter):
        adapter.client = lambda: httpx.Client(
            base_url="http://engine/api/v0/",
            transport=httpx.MockTransport(self.handler),
        )


def test_a_search_ends_once_enough_files_would_do_and_is_deleted_only_when_complete(
    tmp_path, monkeypatch
):
    """#131: the search used to wait for the engine's isComplete (median 17 s)
    and was deleted while the engine was still storing it."""
    import server.soulseek as soulseek

    monkeypatch.setattr(soulseek, "SEARCH_POLL", 0.01, raising=False)
    store = configured(tmp_path)
    store.submit_catalogue([song()])
    hits = [
        {"username": f"peer{i}", "queueLength": 0, "files": [file()]} for i in range(3)
    ]
    engine = FakeSearchEngine(hits, hit_tick=2, complete_tick=18)
    adapter = Soulseek(store)
    engine.attach(adapter)
    candidates = adapter.search(song(), lambda: None)
    assert len(candidates) == 3
    assert engine.ticks == 2  # Returned at the tick the hits arrived.
    assert not any(kind == "DELETE" for kind, _ in engine.log)
    # Later: still not complete, so still not deleted.
    with adapter.client() as client:
        adapter.tidy(client, now=time.monotonic() + soulseek.DELETE_AFTER + 1)
    assert not any(kind == "DELETE" for kind, _ in engine.log)
    # Once the engine completes it, it is deleted after its settling time.
    engine.ticks = engine.complete_tick
    with adapter.client() as client:
        later = time.monotonic() + soulseek.DELETE_AFTER + 1
        adapter.tidy(client, now=later)
        assert not any(kind == "DELETE" for kind, _ in engine.log)
        adapter.tidy(client, now=later + soulseek.DELETE_AFTER + 1)
    deletes = [tick for kind, tick in engine.log if kind == "DELETE"]
    assert deletes and all(tick >= engine.complete_tick for tick in deletes)
    assert adapter.finished == {}


def test_a_fallback_search_is_shorter_than_the_first(tmp_path, monkeypatch):
    import server.soulseek as soulseek

    monkeypatch.setattr(soulseek, "SEARCH_POLL", 0.01)
    monkeypatch.setattr(soulseek, "FIRST_SEARCH", 0.3)
    monkeypatch.setattr(soulseek, "LATER_SEARCH", 0.1)
    adapter = Soulseek(configured(tmp_path))
    engine = FakeSearchEngine([], hit_tick=0, complete_tick=10_000)
    engine.attach(adapter)
    began = time.monotonic()
    adapter._run_search("first", lambda: None)
    first = time.monotonic() - began
    began = time.monotonic()
    adapter._run_search("later", lambda: None, limit=soulseek.LATER_SEARCH)
    later = time.monotonic() - began
    assert 0.25 < first < 1 and later < 0.2


def queued_engine(adapter, states):
    """Transfers whose state for each file name is `states[name]` (bytes
    sent: 0 unless the state says Succeeded)."""
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.method in {"POST", "DELETE"}:
            return httpx.Response(204)
        return httpx.Response(
            200,
            json={
                "directories": [
                    {
                        "files": [
                            {
                                **file(name),
                                "id": f"id-{name}",
                                "stateDescription": state,
                                "bytesTransferred": 0,
                            }
                            for name, state in states.items()
                        ]
                    }
                ]
            },
        )

    adapter.client = lambda: httpx.Client(
        base_url="http://engine/api/v0/", transport=httpx.MockTransport(handler)
    )
    return calls


def test_a_peer_that_keeps_the_file_queued_is_given_up_at_the_soft_limit(
    tmp_path, monkeypatch
):
    """#132: a queued peer held the song for the full 240 s."""
    import server.soulseek as soulseek

    monkeypatch.setattr(soulseek, "TRANSFER_POLL", 0.01, raising=False)
    monkeypatch.setattr(soulseek, "QUEUED_LIMIT", 0.2, raising=False)
    adapter = Soulseek(configured(tmp_path))
    calls = queued_engine(adapter, {"Song": "Queued, Remotely"})
    candidate = candidate_for(song(), {"username": "peer"}, file())
    began = time.monotonic()
    with pytest.raises(ValueError):
        adapter.transfer(candidate, tmp_path / "x.mp3", lambda: None, lambda *_: None)
    assert time.monotonic() - began < 5
    # Its queued transfer is withdrawn, as for any other failure.
    assert ("DELETE", "/api/v0/transfers/downloads/peer/id-Song") in calls


def test_a_busy_peer_ranks_after_a_free_one_whatever_album_share_it_has(tmp_path):
    store = configured(tmp_path)
    store.submit_catalogue([song(), song("Second", 2)])
    responses = [
        {"username": "busy", "queueLength": 50, "files": [file(), file("Second")]},
        {"username": "free", "queueLength": 0, "files": [file()]},
        {"username": "short", "queueLength": 2, "files": [file(), file("Second")]},
    ]

    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"id": "s"})
        if request.url.path.endswith("responses"):
            return httpx.Response(200, json=responses)
        return httpx.Response(200, json={"isComplete": True})

    adapter = Soulseek(store)
    adapter.client = lambda: httpx.Client(
        base_url="http://engine/api/v0/", transport=httpx.MockTransport(handler)
    )
    order = [c["username"] for c in adapter.search(song(), lambda: None)]
    assert order == ["short", "free", "busy"]


def test_soulseek_waits_honour_the_per_source_time_limit(tmp_path, monkeypatch):
    """#132: the Soulseek search and transfer ignored source_deadline, so a
    stuck engine held the job past the source's limit."""
    store = configured(tmp_path / "data")
    store.save_download_settings(
        {"enabled": True, "profile": "mp3_320", "sources": ["soulseek"]}
    )
    store.submit_catalogue([song()])
    job = store.claim_request()
    worker = Downloader(store)
    waited = []

    def stuck_search(target, check):
        while len(waited) < 500:  # An engine that never answers.
            check()
            waited.append(1)
            time.sleep(0.01)
        pytest.fail("the search was never given up")

    worker.soulseek.search = stuck_search
    real = time.monotonic
    began = real()
    # The source's 240 s and the search's 90 s pass in a fraction of a second.
    monkeypatch.setattr(
        "server.downloader.time.monotonic", lambda: began + (real() - began) * 1000
    )
    errors = []
    assert worker.attempt(job, "mp3_320", errors) is False
    assert errors == ["soulseek: time limit reached"]
    assert worker.runtime.health.snapshot("soulseek")["reason"] is None


@pytest.mark.parametrize("explicit", [False, True])
def test_an_album_search_ended_early_is_searched_again_for_a_song_it_lacked(
    tmp_path, monkeypatch, explicit
):
    """Codex round 1 on #131: the album search ended once the first song had
    enough files and was cached; the second song, whose files came later,
    found nothing (an explicit edition then failed outright)."""
    import server.soulseek as soulseek

    monkeypatch.setattr(soulseek, "SEARCH_POLL", 0.01)
    store = configured(tmp_path)
    first, second = song("First", 1), song("Second", 2)
    if explicit:
        first, second = (
            {**first, "explicit_edition": True},
            {**second, "explicit_edition": True},
        )
    store.submit_catalogue([first, second])
    posts = []
    early = [
        {"username": f"peer{i}", "queueLength": 0, "files": [file("First")]}
        for i in range(3)
    ]
    later = early + [{"username": "late", "queueLength": 0, "files": [file("Second")]}]

    def handler(request):
        if request.method == "POST":
            posts.append(json.loads(request.content)["searchText"])
            return httpx.Response(200, json={"id": f"s{len(posts)}"})
        if request.url.path.endswith("responses"):
            return httpx.Response(200, json=early if len(posts) == 1 else later)
        return httpx.Response(200, json={"isComplete": len(posts) > 1})

    adapter = Soulseek(store)
    adapter.client = lambda: httpx.Client(
        base_url="http://engine/api/v0/", transport=httpx.MockTransport(handler)
    )
    assert adapter.search(first, lambda: None)
    found = adapter.search(second, lambda: None)
    assert [c["username"] for c in found] == ["late"]
    assert posts == ["Example Studio", "Example Studio"]
    # Complete now: a third song of the album reuses it without searching.
    adapter.search(first, lambda: None)
    assert len(posts) == 2


def test_the_health_check_keeps_one_engine_client(tmp_path):
    """#142: every 15 s health check opened (and closed) a new client."""
    adapter = Soulseek(configured(tmp_path))
    made = []

    def handler(request):
        return httpx.Response(200, json={"isConnected": True, "isLoggedIn": True})

    def client():
        made.append(1)
        return httpx.Client(
            base_url="http://engine/api/v0/", transport=httpx.MockTransport(handler)
        )

    adapter.client = client
    for _ in range(3):
        assert adapter.health() == {"configured": True, "connected": True}
    assert len(made) == 1
    # New connection settings get a new client.
    (tmp_path / "slskd-connection.json").write_text(
        json.dumps({"url": "http://other:5031", "api_key": "test-only-2"})
    )
    adapter.health()
    assert len(made) == 2


def test_a_completed_file_the_engine_has_not_moved_yet_is_waited_for(
    tmp_path, monkeypatch
):
    """#167: the engine reports 'Completed, Succeeded' about a second before
    it moves the file out of its incomplete folder; 7 of 64 finished
    transfers were thrown away overnight as 'could not be uniquely located'."""
    import threading

    import server.soulseek as module

    adapter = Soulseek(configured(tmp_path / "data"))
    root = tmp_path / "data" / "engine"
    (root / "Studio").mkdir(parents=True)
    candidate = candidate_for(song(), {"username": "peer"}, file())

    def move_in():
        time.sleep(0.4)
        (root / "Studio" / "01 Song.mp3").write_bytes(b"1" * 10000)

    mover = threading.Thread(target=move_in)
    mover.start()
    try:
        adapter.copy_completed(candidate, tmp_path / "copy.mp3")
    finally:
        mover.join()
    assert (tmp_path / "copy.mp3").read_bytes() == b"1" * 10000

    # A file that never arrives fails once the wait is over, not forever.
    monkeypatch.setattr(module, "MOVE_WAIT", 0.3)
    gone = {**candidate, "filename": "Music\\Studio\\02 Other.mp3"}
    began = time.monotonic()
    with pytest.raises(ValueError, match="uniquely"):
        adapter.copy_completed(gone, tmp_path / "other.mp3")
    assert time.monotonic() - began < 2
