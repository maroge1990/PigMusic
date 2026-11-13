"""Counters for the parity targets (#191)."""

import asyncio
import sqlite3
import time

from fastapi.testclient import TestClient

from server import performance as performance_module
from server.downloader import Downloader, Timings
from server.media import save_settings
from server.performance import Performance, report
from server.remote import RemoteAudio
from test_security import PASSWORD, protected


def events(path):
    with sqlite3.connect(path) as conn:
        return conn.execute(
            "SELECT metric,label,outcome,ms,detail FROM events ORDER BY rowid"
        ).fetchall()


def test_counters_are_written_in_one_batch_and_pruned_after_a_week(tmp_path):
    counters = Performance(tmp_path)
    now = time.time()
    counters.record("tap", "Remote · JioSaavn", "played", 812.4, at=now)
    counters.record("tap", "none", "no match", 30, at=now - 8 * 86400)
    assert not counters.path.exists()  # Nothing written until a flush.
    assert counters.flush(now) == 2
    # The week-old tap is pruned in the same flush that wrote it.
    assert events(counters.path) == [("tap", "Remote · JioSaavn", "played", 812, None)]
    # An idle flush between prunes touches nothing.
    assert counters.flush(now + 60) == 0


def test_an_idle_server_never_wakes_the_writer(tmp_path):
    async def scenario():
        counters = Performance(tmp_path)
        flushes = []
        counters.flush = lambda now=None: flushes.append(now) or 0
        performance_module.FLUSH_SECONDS, saved = 0, performance_module.FLUSH_SECONDS
        try:
            counters.start()
            await asyncio.sleep(0.05)
            assert flushes == []  # No event, no wake, no work.
            counters.record("first-sound", "library", "heard", 400)
            await asyncio.sleep(0.05)
            assert len(flushes) == 1
            await asyncio.sleep(0.05)
            assert len(flushes) == 1
        finally:
            performance_module.FLUSH_SECONDS = saved
            await counters.close()

    asyncio.run(scenario())


def test_a_failed_write_keeps_the_batch_for_the_next(tmp_path):
    counters = Performance(tmp_path / "missing-folder")
    counters.record("tap", "none", "unavailable", 10)
    try:
        counters.flush()
    except sqlite3.OperationalError:
        pass
    else:
        raise AssertionError("The write should fail without its folder")
    assert len(counters.pending) == 1


def test_report_measures_each_target(tmp_path):
    counters = Performance(tmp_path)
    now = time.time()
    record = lambda *args, **kw: counters.record(*args, at=now - 60, **kw)
    for ms in (900, 1100, 1300):
        record("tap", "Remote · JioSaavn", "played", ms)
    record("tap", "none", "no match", 2000)
    record("tap", "none", "unavailable", 9000)
    record("remote-source", "jiosaavn", "found", 700)
    record("remote-source", "jiosaavn", "no match", 500)
    for ms in (800, 1000, 1200, 1400, 6000):
        record("first-sound", "remote", "heard", ms, "play")
    record("download", "mix", "completed", 50_000, "soulseek/mp3_320")
    record("download", "mix", "failed", 120_000)
    record("download", "mix", "cancelled", 1000)
    record("download", "library", "completed", 40_000, "soulseek/lossless")
    # One cover served after two misses, one never served: one placeholder.
    record("artwork", "release", "missing", detail="a")
    record("artwork", "release", "missing", detail="a")
    record("artwork", "release", "served", detail="a")
    record("artwork", "release", "missing", detail="b")
    record("artwork", "library-albums", "served", detail="c")
    counters.record("tap", "none", "no match", at=now - 9 * 86400)
    result = counters.report(artists=(19, 20))

    instant = result["instant"]
    assert instant["taps"] == 5 and instant["played"] == 3
    assert instant["played_share"] == 0.6
    assert instant["server_answer"]["median_ms"] == 1100
    assert instant["sources"]["jiosaavn"]["outcomes"] == {"found": 1, "no match": 1}
    assert result["first_sound"]["median_ms"] == 1200
    assert result["first_sound"]["p90_ms"] == 6000
    assert result["first_sound"]["by_mode"]["remote"]["count"] == 5
    mix = result["downloads"]["mix"]
    assert (mix["completed"], mix["failed"], mix["cancelled"]) == (1, 1, 1)
    assert mix["completion"] == 0.5  # Cancelled downloads are not counted.
    assert mix["by_source"] == {"soulseek/mp3_320": 1}
    assert result["downloads"]["library"]["completion"] == 1.0
    art = result["artwork"]
    assert (art["distinct"], art["placeholders"]) == (3, 1)
    assert art["kinds"]["release"]["missing"] == 3
    assert result["artists"] == {"with_photo": 19, "total": 20, "share": 0.95}
    assert result["targets"]["taps_played"] == 0.9


def test_downloads_are_counted_apart_for_mixes_and_library(tmp_path):
    recorded = []

    class Store:
        class performance:
            record = staticmethod(lambda *args: recorded.append(args))

    downloader = Downloader.__new__(Downloader)
    downloader.store = Store()
    timings = Timings()
    timings.won = "soulseek/lossless"
    downloader.count(timings, "completed")
    downloader.count(Timings(), "failed")
    downloader.count(Timings(), "interrupted")
    downloader.count(Timings(), "completed from the mix cache")

    class Mix(Downloader):
        pass

    mix = Mix.__new__(Mix)
    mix.store = Store()
    mix.count(Timings(), "cancelled")
    assert [(r[0], r[1], r[2], r[4]) for r in recorded] == [
        ("download", "library", "completed", "soulseek/lossless"),
        ("download", "library", "failed", None),
        ("download", "library", "completed", "mix cache"),
        ("download", "mix", "cancelled", None),
    ]


def owner_client(app):
    token = app.state.auth.login("mark", PASSWORD, "device", "phone", "ios", "x")
    return TestClient(
        app,
        base_url="https://private.ts.net",
        headers={"Authorization": "Bearer " + token["token"]},
    )


def test_the_app_counts_taps_artwork_and_first_sound(tmp_path, monkeypatch):
    app, _ = protected(tmp_path, monkeypatch)
    store = app.state.store
    save_settings(store, portraits=False, remote_playback=True)

    async def no_match(self, client, track, user_id):
        raise ValueError("No source has this recording.")

    monkeypatch.setattr(RemoteAudio, "stream", no_match)
    song = {
        "title": "Song",
        "artist": "Artist",
        "album": "Album",
        "musicbrainz_release_id": "6f1e0a62-3c5b-4a8e-9c1e-1b2d3c4d5e6f",
    }
    release = "6f1e0a62-3c5b-4a8e-9c1e-1b2d3c4d5e6f"
    with owner_client(app) as client:
        assert client.post("/api/v1/remote-playback", json=song).status_code == 404
        cover = client.get(
            "/api/v1/catalogue/artwork/release/" + release, follow_redirects=False
        )
        assert cover.status_code == 404
        heard = client.post(
            "/api/v1/performance/first-sound",
            json={"ms": 1234, "source": "remote", "action": "skip"},
        )
        assert heard.status_code == 204, heard.text
        assert (
            client.post(
                "/api/v1/performance/first-sound",
                json={"ms": -1, "source": "remote"},
            ).status_code
            == 422
        )
        result = client.get("/api/v1/admin/performance").json()
    assert result["instant"]["no match"] == 1
    assert result["artwork"]["kinds"]["release"]["missing"] == 1
    assert result["first_sound"]["median_ms"] == 1234
    assert result["artists"]["total"] == 0
    rows = events(store.directory / "performance.sqlite3")
    assert ("first-sound", "remote", "heard", 1234, "skip") in rows


def test_report_reads_a_copied_events_database(tmp_path):
    counters = Performance(tmp_path)
    counters.record("tap", "Prepared mix", "played", 80, at=1000)
    counters.flush(now=1000)
    with sqlite3.connect(counters.path) as conn:
        result = report(conn, since=0, until=2000)
    assert result["instant"]["by_source"] == {"Prepared mix": 1}


def test_report_script_prints_the_targets_from_a_copy(tmp_path, capsys):
    from scripts.performance_report import main

    counters = Performance(tmp_path)
    now = time.time()
    counters.record("tap", "Remote · JioSaavn", "played", 900, at=now - 60)
    counters.record("tap", "none", "no match", 2000, at=now - 60)
    counters.record("download", "mix", "completed", 1, "soulseek/mp3_320", at=now - 60)
    counters.flush(now)
    main([str(counters.path), "--artists", "19,20"])
    text = capsys.readouterr().out
    assert "1 of 2 taps played (50.0%, target 90.0%)  below target" in text
    assert "Downloads (mix): 1 completed, 0 failed" in text
    assert "19 of 20 (95.0%, target 95.0%)  meets target" in text
    main([str(counters.path), "--json"])
    assert '"played_share": 0.5' in capsys.readouterr().out
