"""#196 (D-92, D-98): a verified lossless original converted down."""

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
