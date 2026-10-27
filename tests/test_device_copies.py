import asyncio
import hashlib
import json
import shutil
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from server.device_copies import DeviceCopies, checksum
from server.store import Store
from test_personal_libraries import household


def test_original_copy_checks_membership_and_supports_ranges(tmp_path, monkeypatch):
    app, owner, member, token, track = household(tmp_path, monkeypatch)
    root = tmp_path / "nas"
    (root / "private").mkdir(parents=True)
    payload = b"original-media-unchanged"
    (root / "private/song.mp3").write_bytes(payload)
    monkeypatch.setenv("PIG_MUSIC_LIBRARY", str(root))
    with TestClient(
        app,
        base_url="https://private.ts.net",
        headers={"Authorization": "Bearer " + token},
    ) as client:
        assert client.get("/api/v1/tracks/song/copy").status_code == 404
        assert client.get("/api/v1/tracks/song/copy/file").status_code == 404
        app.state.store.user_libraries.add(member, ["song"])
        response = client.post("/api/v1/tracks/song/copy")
        assert response.status_code == 200, response.text
        response = response.json()
        assert response["state"] == "ready"
        assert response["sha256"] == hashlib.sha256(payload).hexdigest()
        assert response["bytes"] == len(payload)
        assert "private" not in json.dumps(response)
        response = client.get(
            "/api/v1/tracks/song/copy/file", headers={"Range": "bytes=0-7"}
        )
        assert response.status_code == 206 and response.content == payload[:8]
        assert not app.state.device_copies.leases
        with app.state.store.connect() as conn:
            conn.execute(
                "DELETE FROM library_members WHERE user_id=? AND track_id=?",
                (member, "song"),
            )
        assert client.get("/api/v1/tracks/song/copy/file").status_code == 404


def audio_fixture(tmp_path, monkeypatch):
    root = tmp_path / "nas"
    root.mkdir()
    source = root / "song.wav"
    with wave.open(str(source), "wb") as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(44100)
        stream.writeframes(b"\0" * 44100 * 4)
    monkeypatch.setenv("PIG_MUSIC_LIBRARY", str(root))
    (tmp_path / "library-snapshot.json").write_text(
        json.dumps(
            {
                "tracks": [
                    {
                        "id": "song",
                        "title": "Song",
                        "artist": "Artist",
                        "relative_path": "song.wav",
                    }
                ]
            }
        )
    )
    return source, DeviceCopies(Store(tmp_path))


def test_aac_is_deduplicated_validated_and_original_is_unchanged(tmp_path, monkeypatch):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is required for AAC validation")
    source, copies = audio_fixture(tmp_path, monkeypatch)
    original = checksum(source)

    async def exercise():
        first = copies.manifest("song", "aac", True)
        assert first["key"] == copies.manifest("song", "aac", True)["key"]
        copies.kick()
        await copies.worker
        ready = copies.manifest("song", "aac")
        assert ready["state"] == "ready" and ready["bytes"] > 0
        output, receipt = copies.acquire("song", "aac")
        assert checksum(output) == ready["sha256"]
        copies.quota = 1
        with pytest.raises(ValueError, match="full"):
            copies.prune(1)
        assert output.exists()
        copies.release(receipt["key"])
        copies.prune(1)
        assert not output.exists()
        assert checksum(source) == original
        assert copies.manifest("song", "aac")["state"] == "not_requested"
        await copies.close()

    asyncio.run(exercise())


def test_restart_requeues_work_and_source_changes_invalidate_original_key(
    tmp_path, monkeypatch
):
    source, copies = audio_fixture(tmp_path, monkeypatch)
    first = copies.manifest("song", "original")
    job = {
        "id": "fixture",
        "state": "preparing",
        "source": str(source),
        "source_sha256": first["sha256"],
    }
    copies.save(job)
    (copies.root / "fixture.partial.m4a").write_bytes(b"incomplete")
    restarted = DeviceCopies(copies.store)
    assert restarted.read("fixture")["state"] == "queued"
    assert not (copies.root / "fixture.partial.m4a").exists()
    source.write_bytes(b"replaced-source")
    assert restarted.manifest("song", "original")["key"] != first["key"]


def test_disconnected_download_releases_cache_lease(tmp_path, monkeypatch):
    from server.routes.device_copies import LeasedFileResponse

    source, copies = audio_fixture(tmp_path, monkeypatch)
    path, value = copies.acquire("song", "original")
    response = LeasedFileResponse(path, release=lambda: copies.release(value["key"]))

    async def send(message):
        raise OSError("client disconnected")

    async def receive():
        return {"type": "http.disconnect"}

    async def exercise():
        with pytest.raises(OSError, match="disconnected"):
            await response(
                {"type": "http", "method": "GET", "headers": [], "extensions": {}},
                receive,
                send,
            )
        assert not copies.leases

    asyncio.run(exercise())


def test_compatible_aac_reuses_audio_packets_and_preserves_cover(tmp_path, monkeypatch):
    import subprocess

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg and ffprobe are required")
    source, copies = audio_fixture(tmp_path, monkeypatch)
    cover = source.parent / "cover.jpg"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=32x32",
            "-frames:v",
            "1",
            str(cover),
        ],
        check=True,
    )
    aac = source.with_suffix(".m4a")
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(source),
            "-i",
            str(cover),
            "-map",
            "0:a",
            "-map",
            "1:v",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-c:v",
            "copy",
            "-disposition:v:0",
            "attached_pic",
            str(aac),
        ],
        check=True,
    )
    copies.store.snapshot_tracks["song"]["relative_path"] = "song.m4a"
    original = checksum(aac)

    def packets(path):
        return subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(path),
                "-map",
                "0:a:0",
                "-c:a",
                "copy",
                "-f",
                "adts",
                "-",
            ],
            capture_output=True,
            check=True,
        ).stdout

    async def exercise():
        first = copies.manifest("song", "aac", True)
        copies.kick()
        await copies.worker
        job = copies.read(first["key"])
        assert job["state"] == "ready"
        assert job["reused_audio"] and job["embedded_artwork"]
        output = copies.root / (first["key"] + ".m4a")
        assert packets(output) == packets(aac)
        assert any(
            s.get("disposition", {}).get("attached_pic") for s in copies.probe(output)
        )
        assert checksum(aac) == original
        await copies.close()

    asyncio.run(exercise())


def test_hashing_ignores_access_time_only_change(tmp_path, monkeypatch):
    source, copies = audio_fixture(tmp_path, monkeypatch)
    actual_stat = Path.stat
    actual_checksum = checksum
    read = [False]

    def filesystem_stat(path, *args, **kwargs):
        result = actual_stat(path, *args, **kwargs)
        if path == source:
            values = {
                name: getattr(result, name)
                for name in dir(result)
                if name.startswith("st_")
            }
            if read[0]:
                values["st_atime"] += 1
                values["st_atime_ns"] += 1_000_000_000
            return SimpleNamespace(**values)
        return result

    def read_checksum(path):
        digest = actual_checksum(path)
        read[0] = True
        return digest

    monkeypatch.setattr(Path, "stat", filesystem_stat)
    monkeypatch.setattr("server.device_copies.checksum", read_checksum)
    ready = copies.manifest("song", "original", True)
    assert ready["state"] == "ready"
    assert ready["sha256"] == actual_checksum(source)


def test_hashing_rejects_content_changed_during_read(tmp_path, monkeypatch):
    source, copies = audio_fixture(tmp_path, monkeypatch)
    actual_checksum = checksum

    def changed_checksum(path):
        digest = actual_checksum(path)
        with path.open("ab") as stream:
            stream.write(b"changed after hash")
        return digest

    monkeypatch.setattr("server.device_copies.checksum", changed_checksum)
    with pytest.raises(ValueError, match="source changed"):
        copies.manifest("song", "original", True)
    assert not copies.hashes


def several_sources(tmp_path, monkeypatch, count):
    root = tmp_path / "nas"
    root.mkdir()
    tracks = []
    for index in range(count):
        with wave.open(str(root / f"song{index}.wav"), "wb") as stream:
            stream.setnchannels(2)
            stream.setsampwidth(2)
            stream.setframerate(44100)
            stream.writeframes(bytes([index]) * 44100 * 4 * 3)
        tracks.append(
            {
                "id": f"song{index}",
                "title": f"Song {index}",
                "artist": "Artist",
                "relative_path": f"song{index}.wav",
            }
        )
    monkeypatch.setenv("PIG_MUSIC_LIBRARY", str(root))
    (tmp_path / "library-snapshot.json").write_text(json.dumps({"tracks": tracks}))
    return DeviceCopies(Store(tmp_path))


def test_two_copies_convert_at_once_and_none_twice(tmp_path, monkeypatch):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is required for AAC validation")
    copies = several_sources(tmp_path, monkeypatch, 4)
    running, peak, encoded = [0], [0], []
    command = DeviceCopies.command

    async def counted(self, *arguments):
        if "-c:a" in arguments:  # The conversion, not its check.
            encoded.append(arguments[-1])
        running[0] += 1
        peak[0] = max(peak[0], running[0])
        try:
            await asyncio.sleep(0.2)
            return await command(self, *arguments)
        finally:
            running[0] -= 1

    monkeypatch.setattr(DeviceCopies, "command", counted)

    async def exercise():
        for index in range(4):
            copies.manifest(f"song{index}", "aac", True)
        copies.kick()
        await copies.worker
        states = [copies.manifest(f"song{i}", "aac")["state"] for i in range(4)]
        await copies.close()
        return states

    assert asyncio.run(exercise()) == ["ready"] * 4
    assert peak[0] == 2
    assert len(encoded) == len(set(encoded)) == 4


@pytest.mark.parametrize("status,waits", [("downloading", False), ("validating", True)])
def test_only_a_download_being_published_holds_copies_up(
    tmp_path, monkeypatch, status, waits
):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is required for AAC validation")
    _, copies = audio_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(copies.store, "queue", lambda: {"items": [{"status": status}]})

    async def exercise():
        copies.manifest("song", "aac", True)
        copies.kick()
        try:
            await asyncio.wait_for(asyncio.shield(copies.worker), 1.5)
            finished = True
        except TimeoutError:
            finished = False
        await copies.close()
        return finished

    assert asyncio.run(exercise()) is not waits


def test_copies_finishing_together_stay_within_the_quota(tmp_path, monkeypatch):
    import threading

    copies = several_sources(tmp_path, monkeypatch, 2)
    copies.quota = 32 * 1024 * 1024
    jobs = []
    for index in range(2):
        temporary = copies.root / f"job{index}.partial.m4a"
        temporary.write_bytes(b"\0" * (24 * 1024 * 1024))
        job = {"id": f"job{index}", "state": "preparing", "touched": 0}
        copies.save(job)
        jobs.append((job, temporary, copies.root / f"job{index}.m4a"))
    results = []
    start = threading.Barrier(2)

    def finish(job, temporary, output):
        start.wait()
        try:
            copies.admit(job, temporary, output, 24 * 1024 * 1024, "sha")
            results.append("ready")
        except ValueError:
            results.append("full")

    threads = [threading.Thread(target=finish, args=job) for job in jobs]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    with copies.store.connect() as conn:
        ready = [
            json.loads(raw)
            for (raw,) in conn.execute("SELECT payload FROM device_copies")
            if json.loads(raw)["state"] == "ready"
        ]
    # The first evicts nothing and fits; the second evicts the first.
    assert sum(j["bytes"] for j in ready) <= copies.quota
    assert len(ready) == 1 and sorted(results) == ["ready", "ready"]
