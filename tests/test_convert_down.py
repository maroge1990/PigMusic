"""#196 (D-92, D-98): a verified lossless original converted down."""

import json
import shutil
import subprocess

import pytest

from server.downloader import Downloader
from server.providers.runtime import Runtime
from server.store import Store
from tests.test_downloader import queued

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs FFmpeg")


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *map(str, args)], check=True)


def flac(folder, name, channels=2, fake=False):
    """3 s of pink noise as FLAC; `fake` makes it from a 320 kbps MP3."""
    path = folder / name
    source = "anoisesrc=color=pink:amplitude=0.3:duration=3:sample_rate=44100"
    if fake:
        mp3 = folder / (name + ".mp3")
        ffmpeg("-f", "lavfi", "-i", source, "-ac", "2", "-b:a", "320k", mp3)
        ffmpeg("-i", mp3, path)
    else:
        ffmpeg("-f", "lavfi", "-i", source, "-ac", str(channels), path)
    return path


def worker_offering(tmp_path, original):
    """A downloader whose only source, Monochrome, offers `original`."""
    store = Store(tmp_path / "data")
    job = queued(store)
    store.save_download_settings(
        # FreeMp3Cloud (no provider here) stands for the MP3 sources that
        # found nothing; Monochrome offers only lossless.
        {
            "enabled": True,
            "profile": "mp3_320",
            "sources": ["freemp3cloud", "monochrome"],
        }
    )
    runtime = Runtime(store)
    runtime.adapters["monochrome"] = object()
    asked = []

    def search(source, target, context):
        asked.append(context.profile)
        if context.profile != "lossless":
            return []
        return [
            {
                "channel": "Test Artist",
                "title": "Test Track",
                "album": "Test Album",
                "duration": 3,
                "extension": ".flac",
            }
        ]

    def transfer(source, candidate, path, context):
        shutil.copyfile(original, path)
        return {}

    runtime.search, runtime.transfer = search, transfer
    return store, job, Downloader(store, providers={}, runtime=runtime), asked


def test_a_verified_flac_becomes_an_mp3_320_when_no_mp3_is_found(tmp_path):
    store, job, worker, asked = worker_offering(tmp_path, flac(tmp_path, "g.flac"))
    worker.process(job)
    # Monochrome is asked only for the lossless original.
    assert asked == ["lossless"]
    item = store.queue()["items"][0]
    assert item["status"] == "completed", item.get("error")
    files = list(worker.library_root.rglob("*.mp3"))
    assert len(files) == 1
    from server.audio import probe_file

    probe = probe_file(files[0])
    assert probe["codec"] == "mp3" and probe["bitrate_kbps"] >= 300
    assert not list(worker.staging.iterdir())


@pytest.mark.parametrize("kind", ["fake", "5.1"])
def test_a_fake_or_multichannel_original_is_never_converted(tmp_path, kind):
    original = (
        flac(tmp_path, "f.flac", fake=True)
        if kind == "fake"
        else flac(tmp_path, "s.flac", channels=6)
    )
    store, job, worker, _ = worker_offering(tmp_path, original)
    worker.process(job)
    item = store.queue()["items"][0]
    assert item["status"] == "failed"
    assert (
        "looks like lossy audio" in item["error"]
        if kind == "fake"
        else "more than two channels" in item["error"]
    )
    assert not list(worker.library_root.rglob("*.mp3"))


def test_converting_up_or_to_another_profile_is_refused(tmp_path):
    store, job, worker, _ = worker_offering(tmp_path, flac(tmp_path, "g.flac"))
    path = worker.staging / "x.flac"
    shutil.copyfile(tmp_path / "g.flac", path)
    for profile in ("lossless", "mp3_128", "native_standard"):
        with pytest.raises(ValueError, match="Only MP3 320 and AAC 256"):
            worker.convert_down(job, path, profile)


def test_each_download_reads_as_a_story_without_peers(tmp_path):
    """#197: a converted download's log reads in order, names no peer, and
    goes when the job is cleared."""
    store, job, worker, _ = worker_offering(tmp_path, flac(tmp_path, "g.flac"))
    worker.process(job)
    lines = [line["text"] for line in store.request_log(job["id"])["lines"]]
    order = [
        "Searched Monochrome for Lossless (FLAC): 1 matching copy",
        "Downloaded a copy from Monochrome",
        "Checked the lossless original: passed",
        "Converted the lossless original to MP3 320 kbps",
        "Checked the file: passed",
        "Wrote the song's details and cover into the file",
        "Played the whole file through: no errors",
        "Filed it in the library under",
        "Done",
    ]
    found = [
        next(i for i, text in enumerate(lines) if text.startswith(o)) for o in order
    ]
    assert found == sorted(found), lines
    assert "FLAC" in lines[found[0]]
    payload = json.dumps(store.request_log(job["id"]))
    assert "claim_token" not in payload and "username" not in payload
    store.clear_queue(finished_only=True)
    with pytest.raises(LookupError):
        store.request_log(job["id"])


def test_a_failed_download_says_why_and_a_member_sees_only_their_own(tmp_path):
    from server.user_library import UserLibrary

    store, job, worker, _ = worker_offering(
        tmp_path, flac(tmp_path, "f.flac", fake=True)
    )
    worker.process(job)
    lines = store.request_log(job["id"])["lines"]
    assert any(
        "refused, Lossless download looks like lossy" in l["text"] for l in lines
    )
    assert lines[-1]["ok"] is False and "looks like lossy audio" in lines[-1]["text"]
    with store.connect() as conn:
        payload = json.loads(
            conn.execute(
                "SELECT payload FROM queue WHERE id=?", (job["id"],)
            ).fetchone()[0]
        )
        payload["requested_by"] = ["someone"]
        conn.execute(
            "UPDATE queue SET payload=? WHERE id=?", (json.dumps(payload), job["id"])
        )
    users = UserLibrary(store)
    assert users.request_log("someone", job["id"])["lines"] == lines
    with pytest.raises(LookupError):
        users.request_log("another person", job["id"])
