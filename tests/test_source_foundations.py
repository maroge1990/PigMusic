"""Real-file and recovery checks for the source-parity foundations."""

import json
import importlib
import sqlite3
import shutil
import subprocess
import sys
import time

import pytest
from mutagen import File

from server.downloader import Cancelled, Downloader, matches
from server.household_policy import HouseholdPolicy
from server.providers import REGISTRY, supports
from server.providers.process import run_checked
from server.quality import actual_profile, satisfies
from server.scanner import inspect_file
from server.soulseek import candidate_for
from server.store import Store
from server.audio import probe_file, remux_opus
from test_personal_removal import household


@pytest.mark.parametrize(
    "codec,floor", [("mp3", 120), ("aac", 112), ("opus", 96), ("vorbis", 128)]
)
def test_native_floors_do_not_make_codecs_interchangeable(codec, floor):
    assert satisfies("native_standard", {"codec": codec, "bitrate": floor})
    assert not satisfies("native_standard", {"codec": codec, "bitrate": floor - 1})
    assert not satisfies("native_standard", {"codec": codec, "bitrate": float("nan")})
    assert not satisfies("native_standard", {"codec": codec, "bitrate": True})
    if codec != "mp3":
        assert not satisfies("mp3_320", {"codec": codec, "bitrate": 320})
    if codec != "aac":
        assert not satisfies("aac_256", {"codec": codec, "bitrate": 320})


def test_unknown_existing_quality_remains_protected_and_native_requires_provenance():
    track = {"codec": "aac", "bitrate": 128}
    assert actual_profile(track) == "unknown"
    assert (
        actual_profile({**track, "acquisition": {"quality_profile": "native_standard"}})
        == "native_standard"
    )
    assert actual_profile({"codec": "mp3", "bitrate": 192}) == "unknown"
    assert not satisfies("native_standard", {"codec": "alac", "bitrate": 1000})


def test_registry_settings_preserve_defaults_order_and_reject_unknown_sources(tmp_path):
    store = Store(tmp_path)
    before = store.download_settings()
    assert before["sources"] == ["freemp3cloud", "zvu4no"]
    assert not before["enabled"]
    assert set(REGISTRY) == {
        "soulseek",
        "freemp3cloud",
        "zvu4no",
        "jiosaavn",
        "soundcloud",
        "monochrome",
    }
    assert supports("jiosaavn", "native_standard")
    assert not supports("jiosaavn", "mp3_320")
    store.save_download_settings(
        {
            "enabled": True,
            "profile": "native_standard",
            "sources": ["jiosaavn", "freemp3cloud", "jiosaavn"],
        }
    )
    settings = Store(tmp_path).download_settings()
    assert settings["sources"] == ["jiosaavn", "freemp3cloud"]
    with pytest.raises(ValueError, match="supported download sources"):
        store.save_download_settings({"sources": ["unknown"]})
    with pytest.raises(ValueError, match="MP3 source"):
        store.save_download_settings({"profile": "mp3_320", "sources": ["jiosaavn"]})
    with pytest.raises(ValueError, match="container"):
        REGISTRY["jiosaavn"].extension({"extension": "../../escape.mp3"})


def test_settings_saved_with_youtube_keep_working_without_it(tmp_path):
    """YouTube was removed as a source (D-60); settings saved before still
    load and save, never search it, and its stored session is deleted."""
    from server.providers.runtime import Runtime

    store = Store(tmp_path)
    saved = {
        **store.download_settings(),
        "enabled": True,
        "profile": "native_standard",
        "sources": ["youtube", "zvu4no"],
    }
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO documents VALUES ('downloads',?) ON CONFLICT(name) DO UPDATE SET payload=excluded.payload",
            (json.dumps(saved),),
        )
    cookies = tmp_path / "youtube-cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n")
    assert store.download_settings()["sources"] == ["zvu4no"]
    store.save_download_settings({"concurrent_downloads": 2})
    assert Store(tmp_path).download_settings()["sources"] == ["zvu4no"]
    runtime = Runtime(store)
    assert not cookies.exists()
    assert "youtube" not in {status["id"] for status in runtime.statuses()}


def test_known_identity_conflicts_cannot_pass_equal_names():
    target = {
        "artist": "Artist",
        "title": "Song",
        "album": "Studio",
        "duration": 3,
        "musicbrainz_recording_id": "recording-a",
        "isrcs": ["AA1234567890"],
    }
    candidate = {"channel": "Artist", "title": "Song", "album": "Studio", "duration": 3}
    assert matches(target, candidate)
    assert not matches(target, {**candidate, "musicbrainz_recording_id": "recording-b"})
    assert not matches(target, {**candidate, "isrc": "BB1234567890"})
    assert matches(
        target, {**candidate, "isrc": "aa1234567890", "provider_recording_id": "123"}
    )


@pytest.fixture(scope="module")
def native_audio(tmp_path_factory):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg required for real native-format checks")
    root = tmp_path_factory.mktemp("native-audio")
    paths = {}
    for codec, extension, args in [
        ("mp3", ".mp3", ["-c:a", "libmp3lame", "-b:a", "128k"]),
        ("aac", ".m4a", ["-c:a", "aac", "-b:a", "192k"]),
        ("opus", ".opus", ["-c:a", "libopus", "-b:a", "160k", "-vbr", "off"]),
        (
            "vorbis",
            ".ogg",
            ["-c:a", "vorbis", "-strict", "experimental", "-b:a", "192k"],
        ),
        ("flac", ".flac", ["-c:a", "flac"]),
    ]:
        path = root / (codec + extension)
        subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "anoisesrc=duration=3:sample_rate=48000:seed=1",
                "-ac",
                "2",
                *args,
                str(path),
            ],
            check=True,
            timeout=20,
        )
        paths[codec] = path
    return paths


@pytest.mark.parametrize("codec", ["mp3", "aac", "opus", "vorbis", "flac"])
def test_native_pipeline_tags_provenance_scans_and_survives_restart(
    tmp_path, native_audio, codec
):
    store = Store(tmp_path / "data")
    (store.directory / "slskd-connection.json").write_text("{}")
    store.save_download_settings(
        {"enabled": True, "profile": "native_standard", "sources": ["soulseek"]}
    )
    track = {
        "title": "Song",
        "artist": "Artist",
        "album": "Studio",
        "album_artist": "Artist",
        "year": "2020",
        "disc_number": 1,
        "track_number": 1,
        "duration": 3,
        "status": "missing",
        "musicbrainz_recording_id": "recording-a",
    }
    store.submit_catalogue([track])
    job = store.claim_request()
    audio = native_audio[codec]

    class FixtureDownloader(Downloader):
        def transfer(self, job_id, candidate, path):
            shutil.copyfile(audio, path)

    worker = FixtureDownloader(store)
    worker.soulseek.search = lambda *_: [
        {
            "title": "Song",
            "channel": "Artist",
            "album": "Studio",
            "duration": 3,
            "extension": audio.suffix,
        }
    ]
    worker.process(job)
    assert store.queue()["items"][0]["status"] == "completed", store.queue()["items"][
        0
    ].get("error")
    restarted = Store(store.directory)
    saved = restarted.library["albums"][0]["tracks"][0]
    path = worker.library_root / saved["relative_path"]
    probe = probe_file(path)
    assert probe["codec"] == codec and probe["source"] == "soulseek"
    assert satisfies("native_standard", probe)
    scanned, _ = inspect_file(path, saved["relative_path"])
    assert scanned["codec"] == codec
    assert (
        scanned["title"] == "Song"
        and scanned["musicbrainz_recording_id"] == "recording-a"
    )
    assert File(path) is not None
    with restarted.connect() as conn:
        acquired = json.loads(
            conn.execute("SELECT payload FROM acquired").fetchone()[0]
        )
        assert acquired["acquisition"]["quality_profile"] == "native_standard"
        assert acquired["acquisition"]["provider"] == "soulseek"
        assert "source_url" not in acquired["acquisition"]
        assert not restarted._enqueue_quality(
            conn, acquired, {"preferred": "native_standard"}, automatic=True
        )
    assert not list(worker.staging.iterdir())


def test_native_codec_and_container_rejection(tmp_path, native_audio):
    worker = Downloader(Store(tmp_path / "data"))
    target = {"artist": "Artist", "title": "Song", "album": "Studio", "duration": 3}
    with pytest.raises(ValueError, match="readable MP3"):
        worker.validate(native_audio["opus"], target, "mp3_320")
    wrong = tmp_path / "renamed.mp3"
    shutil.copyfile(native_audio["opus"], wrong)
    with pytest.raises(ValueError, match="container"):
        worker.validate(wrong, target, "native_standard")


def test_native_fallback_retains_lossless_goal_and_upgrades_same_file_identity(
    tmp_path, native_audio
):
    store = Store(tmp_path / "data")
    (store.directory / "slskd-connection.json").write_text("{}")
    store.save_download_settings(
        {
            "enabled": True,
            "profile": "lossless",
            "fallback_profile": "native_standard",
            "sources": ["soulseek", "jiosaavn"],
        }
    )
    song = {
        "title": "Song",
        "artist": "Artist",
        "album": "Studio",
        "album_artist": "Artist",
        "year": "2020",
        "disc_number": 1,
        "track_number": 1,
        "duration": 3,
        "status": "missing",
    }
    candidate = {"title": "Song", "channel": "Artist", "album": "Studio", "duration": 3}
    ready = [False]

    class Worker(Downloader):
        def transfer(self, job_id, result, path):
            shutil.copyfile(
                native_audio["flac" if result["source"] == "soulseek" else "aac"], path
            )

    worker = Worker(store, {"jiosaavn": lambda *_: [candidate]})
    worker.soulseek.search = lambda *_: (
        [{**candidate, "extension": ".flac"}] if ready[0] else []
    )
    store.submit_catalogue([song])
    worker.process(store.claim_request())
    assert store.queue()["items"][0]["status"] == "completed"
    track = store.library["albums"][0]["tracks"][0]
    original = worker.library_root / track["relative_path"]
    assert original.exists() and track["codec"] == "aac"
    assert track["quality_policy"]["preferred"] == "lossless"
    assert track["acquisition"]["quality_profile"] == "native_standard"
    ready[0] = True
    with store.connect() as conn:
        assert store._enqueue_quality(
            conn, track, store.quality_policy(track, conn=conn), automatic=True
        )
    worker.process(store.claim_request())
    upgraded = Store(store.directory).library["albums"][0]["tracks"][0]
    assert upgraded["id"] == track["id"] and upgraded["codec"] == "flac"
    assert not original.exists()
    assert store.library["stats"]["tracks"] == 1


def test_soulseek_native_candidate_floors_and_context():
    job = {
        "artist": "Artist",
        "album": "Studio",
        "title": "Song",
        "duration": 3,
        "quality_profile": "native_standard",
    }
    response = {"username": "peer"}
    file = {
        "filename": "Artist\\Studio\\01 Song.opus",
        "size": 10000,
        "length": 3,
        "bitRate": 160,
    }
    assert candidate_for(job, response, file)
    assert not candidate_for(job, response, {**file, "bitRate": 64})
    assert not candidate_for(
        job, response, {**file, "filename": "Artist\\Studio\\01 Song (Live).opus"}
    )


@pytest.mark.parametrize("strict", ["mp3_128", "mp3_320", "aac_256", "lossless"])
def test_shared_native_preference_does_not_override_stricter_member(
    tmp_path, monkeypatch, strict
):
    store, _, _, track, owner, member, _ = household(tmp_path, monkeypatch)
    store.user_libraries.add(member, ["saved"])
    policy = HouseholdPolicy(store)
    policy.set_quality(owner, "system", "*", "native_standard")
    policy.set_quality(member, "system", "*", strict)
    assert store.quality_policy(track)["preferred"] == strict
    assert Store(store.directory).quality_policy(track)["preferred"] == strict


def test_decode_process_cancellation_kills_descendants(tmp_path):
    marker = tmp_path / "surviving-child"
    child = f"import time,pathlib;time.sleep(0.8);pathlib.Path({str(marker)!r}).write_text('escaped')"
    parent = f"import subprocess,sys,time;subprocess.Popen([sys.executable,'-c',{child!r}]);time.sleep(20)"
    started = time.monotonic()

    def check():
        if time.monotonic() - started > 0.2:
            raise Cancelled()

    with pytest.raises(Cancelled):
        run_checked([sys.executable, "-c", parent], check, timeout=10)
    assert time.monotonic() - started < 2
    time.sleep(1)
    assert not marker.exists()


def test_decode_process_timeout_and_output_are_bounded():
    with pytest.raises(TimeoutError):
        run_checked(
            [sys.executable, "-c", "import time;time.sleep(20)"],
            lambda: None,
            timeout=0.1,
        )
    with pytest.raises(ValueError, match="output limit"):
        run_checked(
            [sys.executable, "-c", "print('x'*100000)"],
            lambda: None,
            timeout=2,
            max_output_bytes=1024,
        )


def test_completed_parent_does_not_leave_background_child(tmp_path):
    marker = tmp_path / "orphan-output"
    child = f"import time,pathlib;time.sleep(0.8);pathlib.Path({str(marker)!r}).write_text('escaped')"
    parent = f"import subprocess,sys;subprocess.Popen([sys.executable,'-c',{child!r}])"
    result = run_checked([sys.executable, "-c", parent], lambda: None, timeout=2)
    assert result.returncode == 0
    time.sleep(1)
    assert not marker.exists()


def test_native_preference_migration_preserves_rows_constraints_and_rollback(
    tmp_path, monkeypatch
):
    migrations = importlib.import_module("server.migrations")
    conn = sqlite3.connect(tmp_path / "legacy.sqlite3")
    conn.execute("PRAGMA foreign_keys=ON")
    monkeypatch.setattr(
        migrations,
        "MIGRATIONS",
        [entry for entry in migrations.MIGRATIONS if entry[0] < 7],
    )
    migrations.migrate(conn)
    conn.execute(
        "INSERT INTO users(id,username,display_name,role,created_at) VALUES ('owner','owner','Owner','owner',1)"
    )
    conn.execute(
        "INSERT INTO quality_preferences VALUES ('owner','system','*','lossless')"
    )
    conn.commit()
    before = list(conn.iterdump())
    monkeypatch.undo()
    upgrade = importlib.import_module("server.migrations.0007_native_audio")
    original = upgrade.up

    def fail_after_ddl(c):
        original(c)
        raise ValueError("interrupted fixture migration")

    monkeypatch.setattr(upgrade, "up", fail_after_ddl)
    with pytest.raises(ValueError, match="interrupted"):
        migrations.migrate(conn)
    assert list(conn.iterdump()) == before
    monkeypatch.setattr(upgrade, "up", original)
    migrations.migrate(conn)
    assert list(conn.execute("SELECT * FROM quality_preferences")) == [
        ("owner", "system", "*", "lossless")
    ]
    conn.execute(
        "INSERT INTO quality_preferences VALUES ('owner','artist','artist','native_standard')"
    )
    conn.commit()
    assert not list(conn.execute("PRAGMA foreign_key_check"))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO quality_preferences VALUES ('missing','system','*','native_standard')"
        )


@pytest.mark.parametrize("drop_terminal_padding", [False, True])
@pytest.mark.parametrize("omit_initial_skip", [False, True])
def test_opus_remux_preserves_audio_packets_and_cannot_overwrite(
    tmp_path, native_audio, monkeypatch, drop_terminal_padding, omit_initial_skip
):
    ffmpeg = shutil.which("ffmpeg")
    webm = tmp_path / "source.webm"
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-i",
            str(native_audio["opus"]),
            "-c:a",
            "copy",
            str(webm),
        ],
        check=True,
        timeout=20,
    )
    if drop_terminal_padding or omit_initial_skip:
        # Reproduce the old Linux muxer's lost DiscardPadding on every FFmpeg.
        from mutagen.ogg import OggPage
        import server.audio as audio_module

        original_run = audio_module.run_checked

        def legacy_muxer(argv, *args, **kwargs):
            result = original_run(argv, *args, **kwargs)
            if (
                drop_terminal_padding
                and argv[0] == ffmpeg
                and result.returncode == 0
                and argv[-2] == "opus"
            ):
                with open(argv[-1], "r+b") as handle:
                    first = OggPage(handle)
                    last = first
                    while not last.last:
                        last = OggPage(handle)
                    # The 3 s / 20 ms Opus fixture has 312 pre-skip samples;
                    # its terminal packet contains 648 samples of padding.
                    last.position = 3 * 48000 + 312 + 648
                    encoded = last.write()
                    assert len(encoded) == last.size
                    handle.seek(last.offset)
                    handle.write(encoded)
            if (
                omit_initial_skip
                and argv[0] == shutil.which("ffprobe")
                and str(argv[-1]) == str(webm)
                and "-show_data" in argv
            ):
                metadata = json.loads(result.stdout)
                for side in metadata["packets"][0].get("side_data_list", []):
                    side.pop("skip_samples", None)
                result.stdout = json.dumps(metadata).encode()
            return result

        monkeypatch.setattr(audio_module, "run_checked", legacy_muxer)
    output = remux_opus(webm, tmp_path / "copied.opus")

    def pcm(path):
        return subprocess.run(
            [ffmpeg, "-v", "error", "-i", str(path), "-f", "s16le", "-"],
            capture_output=True,
            check=True,
            timeout=20,
        ).stdout

    def packet_hashes(path):
        result = subprocess.run(
            [
                shutil.which("ffprobe"),
                "-v",
                "error",
                "-show_packets",
                "-show_data_hash",
                "sha256",
                "-show_entries",
                "packet=data_hash",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            check=True,
            timeout=20,
        )
        return [packet["data_hash"] for packet in json.loads(result.stdout)["packets"]]

    # Preserve both compressed packets and sample trimming across FFmpeg versions.
    assert packet_hashes(webm) == packet_hashes(output)
    assert pcm(webm) == pcm(output)
    before = output.read_bytes()
    with pytest.raises(ValueError, match="new Opus"):
        remux_opus(webm, output)
    assert output.read_bytes() == before
    with pytest.raises(ValueError, match="one audio stream"):
        remux_opus(native_audio["aac"], tmp_path / "wrong.opus")
    assert not (tmp_path / "wrong.opus").exists()
