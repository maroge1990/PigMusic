"""#195: a FLAC made from an MP3 is told apart from a genuine one."""

import shutil
import subprocess

import pytest

from server.lossless_check import lossy_cutoff

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs FFmpeg")


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *map(str, args)], check=True)


@pytest.fixture(scope="module")
def made(tmp_path_factory):
    folder = tmp_path_factory.mktemp("lossless")

    def noise(name, amplitude=0.3, rate=44100, filters=None):
        path = folder / name
        source = (
            f"anoisesrc=color=pink:amplitude={amplitude}:duration=20:sample_rate={rate}"
        )
        ffmpeg(
            "-f",
            "lavfi",
            "-i",
            source,
            *(["-af", filters] if filters else []),
            "-ac",
            "2",
            path,
        )
        return path

    files = {"genuine": noise("genuine.flac")}
    for bitrate in (128, 320):
        mp3 = folder / f"from-{bitrate}.mp3"
        ffmpeg("-i", files["genuine"], "-b:a", f"{bitrate}k", mp3)
        files[f"mp3 {bitrate}"] = folder / f"from-{bitrate}.flac"
        ffmpeg("-i", mp3, files[f"mp3 {bitrate}"])
    files["band-limited"] = noise("soft.flac", filters="lowpass=f=9000")
    files["quiet"] = noise("quiet.flac", amplitude=0.003)
    files["48 kHz"] = noise("48k.flac", rate=48000)
    files["tone"] = folder / "tone.flac"
    ffmpeg("-f", "lavfi", "-i", "sine=f=440:duration=20", "-ac", "2", files["tone"])
    return files


def cutoff(path, rate=44100):
    return lossy_cutoff(path, rate, 20)


@pytest.mark.parametrize("name", ["mp3 128", "mp3 320"])
def test_a_flac_made_from_an_mp3_shows_its_cutoff(made, name):
    found = cutoff(made[name])
    assert found and 16_000 <= found <= 21_000


@pytest.mark.parametrize("name", ["genuine", "band-limited", "quiet", "tone"])
def test_genuine_flacs_are_not_refused(made, name):
    assert cutoff(made[name]) is None


def test_other_sample_rates(made):
    assert cutoff(made["48 kHz"], 48000) is None
    # Below 44.1 kHz there is no room above the cutoffs: not judged.
    assert lossy_cutoff(made["mp3 128"], 32000, 20) is None


def test_the_downloader_refuses_a_fake_flac_and_keeps_a_genuine_one(tmp_path):
    from server.downloader import Downloader
    from server.store import Store
    from tests.test_downloader import queued

    store = Store(tmp_path / "data")
    job = queued(store)  # A 3 s song.
    genuine, mp3, fake = (tmp_path / n for n in ("g.flac", "f.mp3", "f.flac"))
    source = "anoisesrc=color=pink:amplitude=0.3:duration=3:sample_rate=44100"
    ffmpeg("-f", "lavfi", "-i", source, "-ac", "2", genuine)
    ffmpeg("-i", genuine, "-b:a", "320k", mp3)
    ffmpeg("-i", mp3, fake)
    worker = Downloader(store)
    assert worker.validate(genuine, job, "lossless")["codec"] == "flac"
    with pytest.raises(ValueError, match="looks like lossy audio"):
        worker.validate(fake, job, "lossless")
