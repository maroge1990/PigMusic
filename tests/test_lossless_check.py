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
    for bitrate in (128, 192, 320):
        mp3 = folder / f"from-{bitrate}.mp3"
        ffmpeg("-i", files["genuine"], "-b:a", f"{bitrate}k", mp3)
        files[f"mp3 {bitrate}"] = folder / f"from-{bitrate}.flac"
        ffmpeg("-i", mp3, files[f"mp3 {bitrate}"])
    files["band-limited"] = noise("soft.flac", filters="lowpass=f=9000")
    files["quiet"] = noise("quiet.flac", amplitude=0.003)
    files["48 kHz"] = noise("48k.flac", rate=48000)
    # A steep 20 kHz low-pass applied while mastering, no lossy encoder
    # (Codex round 1, #195): the same shelf as an MP3 320, but genuine.
    # Codex's confirmation round: 19 and 19.5 kHz masters too.
    for rate, edge in ((44100, 20000), (48000, 20000), (96000, 20000)) + (
        (44100, 19000),
        (44100, 19500),
    ):
        path = files[f"mastered {rate} {edge}"] = folder / f"m-{rate}-{edge}.flac"
        source = f"anoisesrc=color=pink:amplitude=0.3:duration=20:sample_rate={rate}"
        ffmpeg(
            "-f",
            "lavfi",
            "-i",
            source,
            "-f",
            "lavfi",
            "-i",
            f"sinc=lp={edge}:r={rate}",
            "-filter_complex",
            "[0][1]afir",
            "-ac",
            "2",
            path,
        )
    files["tone"] = folder / "tone.flac"
    ffmpeg("-f", "lavfi", "-i", "sine=f=440:duration=20", "-ac", "2", files["tone"])
    return files


def cutoff(path, rate=44100):
    return lossy_cutoff(path, rate, 20)


@pytest.mark.parametrize(
    "name, rate",
    [("mp3 128", 44100), ("mp3 192", 44100), ("mp3 320", 44100)]
    + [("mastered 44100 20000", 44100), ("mastered 48000 20000", 48000)]
    + [("mastered 96000 20000", 96000), ("mastered 44100 19000", 44100)]
    + [("mastered 44100 19500", 44100)],
)
def test_a_lossy_source_and_a_steep_master_both_show_a_cutoff(made, name, rate):
    """The spectrum cannot tell an MP3 from a master filtered at 19-20 kHz,
    so a cutoff is a suspicion, never proof (Codex rounds on #195)."""
    assert 16_000 <= cutoff(made[name], rate) <= 21_000


@pytest.mark.parametrize("name", ["genuine", "band-limited", "quiet", "tone"])
def test_genuine_flacs_show_no_cutoff(made, name):
    assert cutoff(made[name]) is None


def test_other_sample_rates(made):
    assert cutoff(made["48 kHz"], 48000) is None
    # Below 44.1 kHz there is no room above the cutoffs: not judged.
    assert lossy_cutoff(made["mp3 128"], 32000, 20) is None


def test_the_downloader_marks_a_suspect_flac_and_passes_a_genuine_one(tmp_path):
    from server.downloader import Downloader
    from server.store import Store
    from tests.test_downloader import queued

    store = Store(tmp_path / "data")
    job = queued(store)  # A 3 s song.
    genuine, mp3, fake = (tmp_path / n for n in ("g.flac", "f.mp3", "f.flac"))
    source = "anoisesrc=color=pink:amplitude=0.3:duration=3:sample_rate=44100"
    ffmpeg("-f", "lavfi", "-i", source, "-ac", "2", genuine)
    ffmpeg("-i", genuine, "-b:a", "192k", mp3)
    ffmpeg("-i", mp3, fake)
    worker = Downloader(store)
    probe = worker.validate(genuine, job, "lossless")
    assert probe["codec"] == "flac" and probe["lossless_check"] == "passed"
    # Never refused on the spectrum alone: kept, and marked in its record.
    assert worker.validate(fake, job, "lossless")["lossless_check"].startswith(
        "suspect"
    )
