from server.organisation import Organisation
from server.recovery import Recovery
from test_recovery import setup


def test_review_has_no_mutations_and_applied_rename_can_be_undone(
    tmp_path, monkeypatch
):
    store, root, path, track = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "server.organisation.inspect_file",
        lambda p, r: (
            {
                **track,
                "relative_path": r,
                "size_bytes": p.stat().st_size,
                "mtime_ns": p.stat().st_mtime_ns,
            },
            None,
        ),
    )
    manager = Organisation(store, Recovery(store))
    plan = manager.plan()
    assert path.exists() and plan["items"][0]["rename"]
    result = manager.apply(["saved"])
    assert result["applied"] == 1 and not path.exists()
    manager.undo(result["operations"][0])
    assert path.read_bytes() == b"original music"


import json, shutil, subprocess
from pathlib import Path
import pytest
from server.scanner import inspect_file
from server.store import Store


@pytest.mark.parametrize("tags", [False, True])
def test_real_audio_organisation_and_undo_preserve_original(
    tmp_path, monkeypatch, tags
):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg required for real audio integration")
    root = tmp_path / "music"
    root.mkdir()
    monkeypatch.setenv("PIG_MUSIC_LIBRARY", str(root))
    path = root / "old.mp3"
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=duration=3",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "320k",
            "-metadata",
            "artist=Artist",
            "-metadata",
            "album=Album",
            "-metadata",
            "title=Song",
            str(path),
        ],
        check=True,
        timeout=20,
    )
    original = path.read_bytes()
    track, _ = inspect_file(path, "old.mp3")
    data = tmp_path / "data"
    data.mkdir()
    (data / "library-snapshot.json").write_text(json.dumps({"tracks": [track]}))
    store = Store(data)
    manager = Organisation(store, Recovery(store))
    result = manager.apply([track["id"]], tags)
    assert result["applied"] == 1 and not path.exists()
    target = root / manager.history()[0]["destination"]
    checked, _ = inspect_file(target, target.name)
    assert (
        checked["title"] == "Song"
        and checked["codec"] == "mp3"
        and checked["bitrate"] >= 319
    )
    manager.undo(result["operations"][0])
    assert path.read_bytes() == original and not target.exists()


def two_albums(tmp_path, monkeypatch, durations=None):
    """Three songs over two albums, each a file whose name the plan changes."""
    root = tmp_path / "music"
    monkeypatch.setenv("PIG_MUSIC_LIBRARY", str(root))
    tracks = []
    for identity, album, number in (
        ("a1", "First", 1),
        ("a2", "First", 2),
        ("b1", "Second", 1),
    ):
        relative = f"Artist/{album}/{identity}.mp3"
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_bytes(b"music " + identity.encode())
        tracks.append(
            {
                "id": identity,
                "title": "Song " + identity,
                "artist": "Artist",
                "album_artist": "Artist",
                "album": album,
                "relative_path": relative,
                "duration": 100,
                "year": 2020,
                "disc_number": 1,
                "track_number": number,
            }
        )
    data = tmp_path / "data"
    data.mkdir()
    (data / "library-snapshot.json").write_text(json.dumps({"tracks": tracks}))
    store = Store(data)
    durations = durations or {}

    def inspect(path, relative):
        # Every file (original, stage or published copy) ends with its song's ID.
        track = next(t for t in tracks if path.read_bytes().endswith(t["id"].encode()))
        return (
            {
                **track,
                "relative_path": relative,
                "duration": durations.get(track["id"], track["duration"]),
                "size_bytes": path.stat().st_size,
                "mtime_ns": path.stat().st_mtime_ns,
            },
            None,
        )

    monkeypatch.setattr("server.organisation.inspect_file", inspect)
    return store, root, tracks


def test_apply_runs_in_the_background_one_album_at_a_time(tmp_path, monkeypatch):
    store, root, tracks = two_albums(tmp_path, monkeypatch)
    manager = Organisation(store, Recovery(store))
    turns = []

    def gate(stopped):
        # Between albums nothing holds the lifecycle lock, so downloads go on.
        assert store.lifecycle_lock.acquire(blocking=False)
        store.lifecycle_lock.release()
        turns.append(manager.status()["done"])
        return lambda: None

    job = manager.start(["a1", "a2", "b1"], False, gate)
    assert job["status"] == "running" and job["total"] == 3
    manager.worker.join(10)
    assert manager.status() == {
        "status": "completed",
        "total": 3,
        "done": 3,
        "message": None,
    }
    assert turns == [0, 2]
    assert not any((root / t["relative_path"]).exists() for t in tracks)


def test_the_first_problem_stops_the_job_and_later_albums_stay_untouched(
    tmp_path, monkeypatch
):
    store, root, tracks = two_albums(tmp_path, monkeypatch, durations={"a2": 50})
    manager = Organisation(store, Recovery(store))
    manager.start(["a1", "a2", "b1"], False, lambda stopped: (lambda: None))
    manager.worker.join(10)
    status = manager.status()
    assert status["status"] == "stopped" and status["done"] == 0
    assert "original is retained" in status["message"]
    assert (root / "Artist/Second/b1.mp3").read_bytes() == b"music b1"
    assert (root / "Artist/First/a2.mp3").read_bytes() == b"music a2"


def test_stop_ends_the_job_between_albums(tmp_path, monkeypatch):
    store, root, tracks = two_albums(tmp_path, monkeypatch)
    manager = Organisation(store, Recovery(store))
    calls = []

    def gate(stopped):
        calls.append(1)
        if len(calls) == 2:
            manager.stop()
            return None if stopped() else (lambda: None)
        return lambda: None

    manager.start(["a1", "a2", "b1"], False, gate)
    manager.worker.join(10)
    assert manager.status()["status"] == "stopped" and manager.status()["done"] == 2
    assert (root / "Artist/Second/b1.mp3").read_bytes() == b"music b1"


def test_a_selection_needing_review_is_refused_before_anything_changes(
    tmp_path, monkeypatch
):
    store, root, tracks = two_albums(tmp_path, monkeypatch)
    manager = Organisation(store, Recovery(store))
    with pytest.raises(ValueError):
        manager.start(["a1", "unknown"], False, lambda stopped: (lambda: None))
    assert manager.worker is None and manager.status() == {"status": "idle"}
    assert (root / "Artist/First/a1.mp3").exists()
