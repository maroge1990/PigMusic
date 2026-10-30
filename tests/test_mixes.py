import asyncio
from datetime import timedelta
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from server.home import HomeDiscovery
from server.mix_cache import song_key, MixDownloader
from server import mixes as mix_module
from server.mixes import choose
from test_security import PASSWORD, protected
from test_downloader import audio  # Shared real MP3 fixture, not provider traffic.


@pytest.fixture(autouse=True)
def small_mixes(monkeypatch):
    # Most fixtures use a few songs; the minimum has its own tests below.
    monkeypatch.setattr(mix_module, "MIN_MIX", 1)


def track(n=1, **extra):
    return {
        "title": f"Song {n}",
        "artist": f"Artist {n}",
        "album": "Album",
        "album_artist": f"Artist {n}",
        "duration": 3,
        "track_number": 1,
        "disc_number": 1,
        "year": "",
        **extra,
    }


def cached(mixes, song, now=None):
    key = song_key(song)
    filename = key + ".mp3"
    path = mixes.cache.directory / filename
    path.write_bytes(b"fixture audio")
    with mixes.store.connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO mix_audio VALUES (?,?,?,?,?,?)",
            (
                key,
                filename,
                json.dumps({"codec": "mp3", "bitrate_kbps": 320}),
                path.stat().st_size,
                0,
                now or time.time() - 7200,
            ),
        )
    return path


def ready(mixes, user, songs, day=None, kind="daily"):
    day = day or mixes.day()
    identity = mixes.stage(
        user, kind, day, "Fixture blend", "Because you saved music", songs
    )
    mixes.publish(identity, songs, mixes.day())
    return identity


def member(app, owner):
    setup = app.state.auth.setup_code(owner, "member", "Member")
    app.state.auth.activate(setup["code"], PASSWORD, "fixture")
    token = app.state.auth.login(
        "member", PASSWORD, "device", "phone", "ios", "fixture"
    )["token"]
    with app.state.store.connect() as conn:
        user = conn.execute("SELECT id FROM users WHERE username='member'").fetchone()[
            0
        ]
    return user, token


def test_daily_selection_changes_without_losing_identity_or_artist_diversity():
    songs = [track(n, artist=f"Artist {n % 25}") for n in range(100)]
    left = choose(songs, 30, "today")
    assert left == choose(list(reversed(songs)), 30, "today")
    right = choose(songs, 30, "tomorrow")
    assert len({song_key(t) for t in left} - {song_key(t) for t in right}) >= 15
    assert all(a["artist"] != b["artist"] for a, b in zip(left, left[1:]))
    recent = {song_key(t): int(time.time()) for t in left}
    assert not {song_key(t) for t in choose(songs, 30, "today", recent)} & set(recent)
    a = track(musicbrainz_release_id="release", musicbrainz_recording_id="recording")
    assert song_key(a) == song_key({**a, "title": "Spelling difference"})
    assert song_key(a) != song_key({**a, "musicbrainz_release_id": "live-edition"})
    assert len(choose([a, {**a, "musicbrainz_release_id": "other"}], 5, "day")) == 1


def test_private_editions_signed_cache_playback_history_and_save(
    tmp_path, monkeypatch, audio
):
    app, owner = protected(tmp_path, monkeypatch)
    mixes = app.state.store.mixes
    user, token = member(app, owner)
    cached(mixes, track()).write_bytes(audio.read_bytes())
    identity = ready(mixes, user, [track()])
    other = ready(mixes, owner, [track(2)])
    with TestClient(
        app,
        base_url="https://private.ts.net",
        headers={"Authorization": "Bearer " + token},
    ) as client:
        feed = client.get("/api/v1/home/mixes")
        assert feed.status_code == 200, feed.text
        assert "relative_path" not in feed.text and "filename" not in feed.text
        song = feed.json()["mixes"][0]["tracks"][0]
        assert song["id"] == f"mix:{identity}:0"
        assert client.post(f"/api/v1/playback/mix:{other}:0/url").status_code == 404
        assert client.get("/api/v1/admin/mixes").status_code == 403
        assert client.put("/api/v1/admin/mixes", json={}).status_code == 403
        assert client.get(f"/api/v1/admin/mixes/people/{user}").status_code == 403
        playback = client.post(f"/api/v1/playback/{song['id']}/url")
        assert playback.status_code == 200, playback.text
        assert client.get(playback.json()["url"]).content == audio.read_bytes()
        partial = client.get(playback.json()["url"], headers={"Range": "bytes=10-99"})
        assert (
            partial.status_code == 206 and partial.content == audio.read_bytes()[10:100]
        )
        # The audio element can use the session-bound signature without its bearer.
        audio_client = TestClient(app, base_url="https://private.ts.net")
        assert audio_client.get(playback.json()["url"]).status_code == 200
        events = {
            "events": [
                {
                    "track_id": song["id"],
                    "seconds": 30,
                    "played_at": int(time.time()),
                    "event_id": "mix-listen",
                }
            ]
        }
        assert client.post("/api/v1/play-events", json=events).status_code == 200
        assert client.post("/api/v1/play-events", json=events).status_code == 200
        assert mixes.history(user, [])[song_key(track())] > 0
        with app.state.store.connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM mix_listens").fetchone()[0] == 1
        saved = client.post(f"/api/v1/mixes/{identity}/save")
        assert saved.status_code == 200, saved.text
        assert (
            client.post(f"/api/v1/mixes/{identity}/save").json()["id"]
            == saved.json()["id"]
        )
        assert app.state.store.queue()["items"] == []
        from server.media import save_settings

        save_settings(app.state.store, portraits=True, remote_playback=False)
        # Saved editions reuse the prepared file even when experimental streams are off.
        reused = client.post("/api/v1/remote-playback", json=track())
        assert reused.status_code == 200 and reused.json()["source"] == "Prepared mix"
        assert not app.state.store.user_libraries.contains(user, song["id"])
        assert (
            client.post("/api/v1/mixes/hide", json={"track_id": song["id"]}).status_code
            == 200
        )
        assert client.get("/api/v1/home/mixes").json()["mixes"] == []


def test_rotation_respects_shared_references_queue_leases_and_restart(
    tmp_path, monkeypatch
):
    app, owner = protected(tmp_path, monkeypatch)
    mixes = app.state.store.mixes
    user, _ = member(app, owner)
    song = track()
    path = cached(mixes, song)
    today = mixes.day()
    first = ready(mixes, owner, [song])
    second = ready(mixes, user, [song])
    mixes.playback(owner, f"mix:{first}:0")  # Protect the complete playing edition.
    ready(mixes, owner, [track(2)], day=today + timedelta(days=1))
    mixes.activate(today + timedelta(days=1))
    mixes.cache.cleanup(time.time() + 7200)
    assert path.exists()  # Other person's active edition still needs the file.
    ready(mixes, user, [track(3)], day=today + timedelta(days=1))
    mixes.activate(today + timedelta(days=1))
    mixes.cache.cleanup(time.time() + 7200)
    assert path.exists()  # No active mix, but the durable playback lease remains.
    mixes.cache.cleanup(time.time() + 7 * 3600)
    assert not path.exists()
    # Playlist metadata, including delayed-listen identity, outlives audio retention.
    assert mixes.entry(owner, f"mix:{first}:0")["title"] == song["title"]
    with pytest.raises(LookupError):
        mixes.entry(owner, f"mix:{second}:0")


def test_mix_playback_does_not_wait_for_library_work(tmp_path, monkeypatch):
    """A library sync, scan or export holds the lifecycle lock, for seconds or
    minutes; playing a mix or radio song (each address and each Range request)
    must not queue behind it."""
    import threading

    app, owner = protected(tmp_path, monkeypatch)
    mixes = app.state.store.mixes
    path = cached(mixes, track())
    identity = ready(mixes, owner, [track()])
    held, release = threading.Event(), threading.Event()

    def library_work():
        with mixes.store.lifecycle_lock:
            held.set()
            release.wait(10)

    worker = threading.Thread(target=library_work)
    worker.start()
    try:
        assert held.wait(5)
        found = []
        play = threading.Thread(
            target=lambda: found.append(mixes.playback(owner, f"mix:{identity}:0"))
        )
        play.start()
        play.join(2)
        assert found == [path]
    finally:
        release.set()
        worker.join()
    # The playback lease still protects the file from cleanup.
    with mixes.store.connect() as conn:
        lease = conn.execute(
            "SELECT lease_until FROM mix_audio WHERE key=?", (song_key(track()),)
        ).fetchone()[0]
    assert lease > time.time() + 5 * 3600


def test_future_editions_do_not_replace_today_until_rotation(tmp_path, monkeypatch):
    app, owner = protected(tmp_path, monkeypatch)
    mixes = app.state.store.mixes
    for song in (track(), track(2)):
        cached(mixes, song)
    first = ready(mixes, owner, [track()])
    tomorrow = mixes.day() + timedelta(days=1)
    second = ready(mixes, owner, [track(2)], day=tomorrow)
    assert mixes.list(owner)["mixes"][0]["id"] == first
    mixes.activate(tomorrow)
    assert mixes.list(owner)["mixes"][0]["id"] == second


def test_failed_preparation_retains_last_ready_and_publishes_shorter_complete_mix(
    tmp_path, monkeypatch
):
    app, owner = protected(tmp_path, monkeypatch)
    mixes = app.state.store.mixes
    cached(mixes, track())
    first = ready(mixes, owner, [track()], day=mixes.day() - timedelta(days=1))
    second = mixes.stage(
        owner, "daily", mixes.day(), "New", "Reason", [track(), track(2)]
    )
    asyncio.run(mixes.prepare_staged(owner, mixes.day(), mixes.day()))
    assert mixes.list(owner)["mixes"][0]["id"] == first
    with mixes.store.connect() as conn:
        payload = json.loads(
            conn.execute(
                "SELECT payload FROM mix_editions WHERE id=?", (second,)
            ).fetchone()[0]
        )
    payload.update(attempts=2, retry_at=0)
    mixes.update(second, payload)
    mixes.store.save_download_settings({"enabled": True, "sources": ["freemp3cloud"]})

    def fail(downloader, song):
        downloader.result = "failed"
        downloader.error = "No matching candidate"
        return False

    monkeypatch.setattr(MixDownloader, "fetch", fail)
    asyncio.run(mixes.prepare_staged(owner, mixes.day(), mixes.day()))
    feed = mixes.list(owner)["mixes"][0]
    assert feed["id"] == second and len(feed["tracks"]) == 1
    # Admin still sees both intended songs and why the second was left out.
    detail = mixes.inspect(owner)
    edition = next(e for e in detail["editions"] if e["id"] == second)
    assert edition["state"] == "ready" and edition["published"] == 1
    assert [(s["title"], s["status"], s["detail"]) for s in edition["songs"]] == [
        ("Song 1", "cached", None),
        ("Song 2", "failed", "No matching candidate"),
    ]
    status = mixes.cache.status()
    assert status["people"] == [
        {"id": owner, "name": "mark", "ready": 1, "preparing": 0}
    ]


def test_owned_mix_rows_keep_library_actions_separate_from_private_playback(
    tmp_path, monkeypatch
):
    nas = tmp_path / "nas"
    nas.mkdir()
    (nas / "song.mp3").write_bytes(b"fixture audio")
    song = track(id="owned", relative_path="song.mp3")
    (tmp_path / "library-snapshot.json").write_text(json.dumps({"tracks": [song]}))
    app, owner = protected(tmp_path, monkeypatch)
    mixes = app.state.store.mixes
    identity = ready(mixes, owner, [song])
    row = mixes.list(owner)["mixes"][0]["tracks"][0]
    assert row.id == f"mix:{identity}:0"
    assert row.library_track_id == "owned" and row.server_state == "in_library"
    assert mixes.playback(owner, row.id).read_bytes() == b"fixture audio"


def test_yesterdays_mix_queue_still_plays_after_rotation(tmp_path, monkeypatch):
    """26 Oct: a daily-mix queue paused at 22:08 and resumed at 08:35. The
    edition rotated overnight and its play lease ran out, so every song
    answered 404 and the app passed over the rest of the queue. A song the
    household has plays from its file, one today's edition also holds plays
    from there; only a song neither has is unavailable."""
    nas = tmp_path / "nas"
    nas.mkdir()
    (nas / "song.mp3").write_bytes(b"household audio")
    owned = track(1, id="owned", relative_path="song.mp3")
    (tmp_path / "library-snapshot.json").write_text(json.dumps({"tracks": [owned]}))
    app, owner = protected(tmp_path, monkeypatch)
    mixes = app.state.store.mixes
    user, token = member(app, owner)
    discovery, gone = track(2), track(3)
    cached(mixes, discovery).write_bytes(b"discovery audio")
    cached(mixes, gone)
    yesterday = ready(
        mixes, user, [owned, discovery, gone], day=mixes.day() - timedelta(days=1)
    )
    for i in range(3):
        mixes.playback(user, f"mix:{yesterday}:{i}")  # Played last night.
    today = ready(mixes, user, [discovery])
    with mixes.store.connect() as conn:
        conn.execute(
            "UPDATE mix_editions SET active=0,retired_at=?,lease_until=? WHERE id=?",
            (time.time() - 10 * 3600, time.time() - 4 * 3600, yesterday),
        )
    stranger = ready(mixes, owner, [owned], day=mixes.day() - timedelta(days=1))
    with mixes.store.connect() as conn:
        conn.execute(
            "UPDATE mix_editions SET active=0,retired_at=?,lease_until=? WHERE id=?",
            (time.time() - 10 * 3600, time.time() - 4 * 3600, stranger),
        )
    with TestClient(
        app,
        base_url="https://private.ts.net",
        headers={"Authorization": "Bearer " + token},
    ) as client:

        def play(track_id):
            return client.post(f"/api/v1/playback/{track_id}/url")

        place = {
            "Song 1": f"mix:{yesterday}:0",
            "Song 2": f"mix:{yesterday}:1",
            "Song 3": f"mix:{yesterday}:2",
        }
        household = play(place["Song 1"])
        assert household.status_code == 200, household.text
        assert "/api/v1/playback/files/owned?" in household.json()["url"]
        assert client.get(household.json()["url"]).content == b"household audio"
        rotated = play(place["Song 2"])
        assert rotated.status_code == 200, rotated.text
        assert f"/api/v1/playback/files/mix:{today}:0?" in rotated.json()["url"]
        assert client.get(rotated.json()["url"]).content == b"discovery audio"
        assert play(place["Song 3"]).status_code == 404
        # Another person's old edition still grants nothing.
        assert play(f"mix:{stranger}:0").status_code == 404
    # Nothing joined the member's library by playing.
    assert not app.state.store.user_libraries.contains(user, "owned")


def test_a_rotated_mix_song_stands_in_from_any_edition_or_todays_radio(
    tmp_path, monkeypatch
):
    """The same recording on another album in today's mix stands in, as does
    today's radio (never "active" on Home, and unleased until it plays). A
    live recording never stands in for the studio one. The retired edition's
    own files stay closed, the stand-in's audio is leased, and listens still
    count under the queued identity."""
    app, owner = protected(tmp_path, monkeypatch)
    mixes = app.state.store.mixes
    user, token = member(app, owner)
    original = track(4, album="Hot Fuss", musicbrainz_release_id="release-a")
    reissue = track(4, album="Greatest Hits", musicbrainz_release_id="release-b")
    studio, live = track(5), track(5, album="Live at Leeds")
    on_radio = track(6)
    for song in (original, reissue, studio, live, on_radio):
        cached(mixes, song)
    yesterday = ready(
        mixes, user, [original, studio, on_radio], day=mixes.day() - timedelta(days=1)
    )
    for i in range(3):
        mixes.playback(user, f"mix:{yesterday}:{i}")
    today = ready(mixes, user, [reissue, live])
    radio = ready(mixes, user, [on_radio], kind="radio")
    with mixes.store.connect() as conn:
        conn.execute(
            "UPDATE mix_editions SET active=0,retired_at=?,lease_until=? WHERE id=?",
            (time.time() - 10 * 3600, time.time() - 4 * 3600, yesterday),
        )
        conn.execute(
            "UPDATE mix_editions SET active=0,retired_at=NULL,lease_until=0 WHERE id=?",
            (radio,),
        )
        conn.execute("UPDATE mix_audio SET lease_until=0")
    with TestClient(
        app,
        base_url="https://private.ts.net",
        headers={"Authorization": "Bearer " + token},
    ) as client:

        def signed(track_id):
            response = client.post(f"/api/v1/playback/{track_id}/url")
            return response.json()["url"] if response.status_code == 200 else None

        assert f"/files/mix:{today}:0?" in signed(f"mix:{yesterday}:0")
        assert signed(f"mix:{yesterday}:1") is None  # Only a live take today.
        assert f"/files/mix:{radio}:0?" in signed(f"mix:{yesterday}:2")
        for i in range(3):
            response = client.get(f"/api/v1/playback/files/mix:{yesterday}:{i}")
            assert response.status_code == 404
        events = {
            "events": [
                {
                    "track_id": f"mix:{yesterday}:0",
                    "seconds": 30,
                    "played_at": int(time.time()),
                    "event_id": "rotated-listen",
                }
            ]
        }
        assert client.post("/api/v1/play-events", json=events).status_code == 200
    with mixes.store.connect() as conn:
        leases = dict(conn.execute("SELECT key,lease_until FROM mix_audio"))
        assert conn.execute("SELECT COUNT(*) FROM mix_listens").fetchone()[0] == 1
    assert leases[song_key(reissue)] > time.time() + 5 * 3600
    assert leases[song_key(on_radio)] > time.time() + 5 * 3600


def test_a_rotated_song_stands_in_from_an_edition_retired_minutes_ago(
    tmp_path, monkeypatch
):
    """#24: the only compatible copy is in an edition retired a minute ago
    with no lease, which entry(playback=True) still plays; the stand-in
    search skipped it."""
    app, owner = protected(tmp_path, monkeypatch)
    mixes = app.state.store.mixes
    user, _ = member(app, owner)
    original = track(7, album="First", musicbrainz_release_id="release-c")
    reissue = track(7, album="Best Of", musicbrainz_release_id="release-d")
    for song in (original, reissue):
        cached(mixes, song)
    old = ready(mixes, user, [original], day=mixes.day() - timedelta(days=2))
    recent = ready(mixes, user, [reissue], day=mixes.day() - timedelta(days=1))
    with mixes.store.connect() as conn:
        conn.execute(
            "UPDATE mix_editions SET active=0,retired_at=?,lease_until=? WHERE id=?",
            (time.time() - 10 * 3600, time.time() - 4 * 3600, old),
        )
        conn.execute(
            "UPDATE mix_editions SET active=0,retired_at=?,lease_until=0 WHERE id=?",
            (time.time() - 60, recent),
        )
    assert mixes.stand_in(user, f"mix:{old}:0") == f"mix:{recent}:0"
    # Once that edition has been retired over 6 h (and the lease playing it
    # just now gave it has run out), it no longer stands in.
    with mixes.store.connect() as conn:
        conn.execute(
            "UPDATE mix_editions SET retired_at=?,lease_until=0 WHERE id=?",
            (time.time() - 7 * 3600, recent),
        )
    assert mixes.stand_in(user, f"mix:{old}:0") is None


def test_mix_playback_checks_its_file_without_holding_the_write_lock(
    tmp_path, monkeypatch
):
    """#67: playback renewed the edition's leases and then checked the file on
    the NAS inside the same write transaction, so a slow NAS kept every other
    writer (sign-ins, the queue, history) waiting for up to 10 s."""
    import sqlite3

    app, owner = protected(tmp_path, monkeypatch)
    mixes = app.state.store.mixes
    user, _ = member(app, owner)
    song = track(8)
    cached(mixes, song).write_bytes(b"mix audio")
    edition = ready(mixes, user, [song])
    available, writes = mixes.cache.available, []

    def slow_nas(item):
        # Another writer arrives while the NAS is being asked.
        with sqlite3.connect(mixes.store.database, timeout=0.2) as conn:
            conn.execute(
                "INSERT INTO documents VALUES ('probe', '{}') ON CONFLICT(name) DO NOTHING"
            )
        writes.append(True)
        return available(item)

    monkeypatch.setattr(mixes.cache, "available", slow_nas)
    assert mixes.playback(user, f"mix:{edition}:0").read_bytes() == b"mix audio"
    assert writes == [True]
    # A second play within minutes doesn't rewrite every entry's lease.
    with mixes.store.connect() as conn:
        leased = conn.execute(
            "SELECT lease_until FROM mix_editions WHERE id=?", (edition,)
        ).fetchone()[0]
    mixes.playback(user, f"mix:{edition}:0")
    with mixes.store.connect() as conn:
        assert (
            conn.execute(
                "SELECT lease_until FROM mix_editions WHERE id=?", (edition,)
            ).fetchone()[0]
            == leased
        )


def test_tag_candidates_accept_the_providers_list_response(tmp_path, monkeypatch):
    app, _ = protected(tmp_path, monkeypatch)
    home = HomeDiscovery(app.state.store, None)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json=[{"recording_mbid": "one"}])
            )
        ) as client:
            value, failed = await home.feed(client, "lb-radio/tags", tag="rock")
            assert value == {"recordings": [{"recording_mbid": "one"}]} and not failed

    asyncio.run(run())


def test_cache_download_uses_validation_without_nas_or_membership(
    tmp_path, monkeypatch, audio
):
    app, _ = protected(tmp_path, monkeypatch)
    store = app.state.store
    store.save_download_settings({"enabled": True, "sources": ["freemp3cloud"]})
    song = {"title": "Test", "artist": "Test", "album": "Studio", "duration": 3}
    candidate = {
        "title": "Test",
        "channel": "Test",
        "duration": 3,
        "quality": "320kbps",
        "source_url": "https://meln.top/mix-fixture.mp3",
    }
    downloader = MixDownloader(
        store.mix_cache,
        app.state.source_runtime,
        lambda: False,
        providers={"freemp3cloud": lambda q, n: [candidate]},
    )
    from server.providers import network

    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, content=audio.read_bytes())

    monkeypatch.setattr(
        network,
        "client",
        lambda: httpx.Client(transport=httpx.MockTransport(respond)),
    )
    assert downloader.fetch(song)
    assert len(requests) == 1 and requests[0].url.host == "meln.top"
    assert store.mix_cache.file(song_key(song)).is_file()
    assert store.queue()["items"] == []
    assert not (tmp_path / "nas").exists()
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM acquired").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM library_members").fetchone()[0] == 0
        probe = json.loads(conn.execute("SELECT probe FROM mix_audio").fetchone()[0])
        assert probe["codec"] == "mp3" and probe["duration"] == pytest.approx(
            3, abs=0.1
        )
    store.write_document("mix-settings", {"budget_bytes": 1})
    assert not downloader.fetch(track(3))


@pytest.mark.parametrize("failure", ["invalid_audio", "incomplete_transfer"])
def test_cache_rejects_invalid_or_incomplete_transfers(
    tmp_path, monkeypatch, audio, failure
):
    from server.providers import network

    app, _ = protected(tmp_path, monkeypatch)
    store = app.state.store
    store.save_download_settings({"enabled": True, "sources": ["freemp3cloud"]})
    candidate = {
        "title": "Song 1",
        "channel": "Artist 1",
        "duration": 3,
        "quality": "320kbps",
        "source_url": "https://meln.top/mix-fixture.mp3",
    }
    body = b"not audio" if failure == "invalid_audio" else audio.read_bytes()
    headers = (
        {"content-length": str(len(body) + 1)}
        if failure == "incomplete_transfer"
        else {}
    )
    monkeypatch.setattr(
        network,
        "client",
        lambda: httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=body, headers=headers)
            )
        ),
    )
    worker = MixDownloader(
        store.mix_cache,
        app.state.source_runtime,
        lambda: False,
        providers={"freemp3cloud": lambda q, n: [candidate]},
    )
    assert not worker.fetch(track()) and worker.result == "failed"
    assert not list(store.mix_cache.staging.iterdir())
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM mix_audio").fetchone()[0] == 0
    assert not (tmp_path / "nas").exists()


def test_explicit_library_add_copies_cache_and_survives_cache_cleanup(
    tmp_path, monkeypatch, audio
):
    from server.downloader import Downloader
    from server.media import track_file

    app, owner = protected(tmp_path, monkeypatch)
    store = app.state.store
    store.save_download_settings({"enabled": True, "sources": ["freemp3cloud"]})
    song = track()
    path = cached(store.mixes, song)
    path.write_bytes(audio.read_bytes())
    store.submit_catalogue([{**song, "status": "missing"}], user_id=owner)
    store.mix_cache.cleanup(time.time() + 7200)
    assert path.is_file()  # The queued library intent protects audio before processing.
    job = store.claim_request()
    worker = Downloader(
        store, providers={}
    )  # No provider search can rescue a broken promotion.
    worker.process(job)
    assert store.queue()["items"][0]["status"] == "completed", store.queue()["items"]
    published = store.library["albums"][0]["tracks"][0]
    durable = track_file(store, published["id"])
    assert store.user_libraries.contains(owner, published["id"])
    assert durable != path and durable.is_file() and path.is_file()
    store.mix_cache.cleanup(time.time() + 7 * 3600)
    assert not path.exists() and durable.is_file()


def test_background_pass_prepares_all_families_and_tomorrow_without_duplicate_work(
    tmp_path, monkeypatch
):
    app, owner = protected(tmp_path, monkeypatch)
    store = app.state.store
    mixes = store.mixes
    owned = [track(n, id=f"owned-{n}", genre="Rock", year=1994) for n in range(45)]
    discoveries = [
        track(n, genre="Rock", reason="Similar to Artist 0", bucket="related")
        for n in range(100, 150)
    ]
    discoveries += [track(150, bucket="popular")]
    for song in owned + discoveries:
        cached(mixes, song, now=time.time())
    monkeypatch.setattr(
        store.user_libraries,
        "projection",
        lambda user: {"tracks": owned, "artists": [], "albums": []},
    )
    calls = []

    async def candidates(client, projection):
        calls.append(projection)
        return discoveries, "Rock"

    monkeypatch.setattr(mixes, "candidates", candidates)

    async def run():
        await mixes.prepare_pass(None)
        first = mixes.list(owner)
        await mixes.prepare_pass(None)
        assert mixes.list(owner) == first
        assert (
            len(calls) == 2
        )  # Current and next day; revisiting Home never prepares again.
        assert {m["kind"] for m in first["mixes"]} == {
            "daily",
            "fresh",
            "back",
            "artist",
            "genre",
            "decade",
            "popular",
            "deep",
        }
        for mix in first["mixes"]:
            assert mix["day"] == str(mixes.day())
            assert all(mixes.playback(owner, t.id).is_file() for t in mix["tracks"])
        with store.connect() as conn:
            assert (
                conn.execute(
                    "SELECT COUNT(*) FROM mix_editions WHERE active=0 AND retired_at IS NULL AND kind!='radio'"
                ).fetchone()[0]
                == 8
            )
        assert store.queue()["items"] == []

    asyncio.run(run())


def test_ready_daily_editions_do_not_starve_an_unfinished_family(tmp_path, monkeypatch):
    app, owner = protected(tmp_path, monkeypatch)
    mixes = app.state.store.mixes
    today = mixes.day()
    cached(mixes, track())
    for day in (today, today + timedelta(days=1)):
        ready(mixes, owner, [track()], day=day)
    artist = mixes.stage(
        owner, "artist", today, "Around an artist", "Reason", [track()]
    )

    def unexpected_projection(user):
        pytest.fail("Retrying staged work must not repeat candidate generation")

    monkeypatch.setattr(mixes.store.user_libraries, "projection", unexpected_projection)
    asyncio.run(mixes.prepare_person(None, owner, today))
    assert artist in {m["id"] for m in mixes.list(owner)["mixes"]}


def test_disabled_members_upcoming_editions_never_reactivate(tmp_path, monkeypatch):
    app, owner = protected(tmp_path, monkeypatch)
    mixes = app.state.store.mixes
    user, _ = member(app, owner)
    tomorrow = mixes.day() + timedelta(days=1)
    ready(mixes, user, [track()], day=tomorrow)
    with mixes.store.connect() as conn:
        conn.execute("UPDATE users SET disabled=1 WHERE id=?", (user,))
    mixes.activate(tomorrow)
    assert mixes.list(user)["mixes"] == []


def test_reclaims_retired_audio_before_attempting_preparation(tmp_path, monkeypatch):
    app, owner = protected(tmp_path, monkeypatch)
    mixes = app.state.store.mixes
    path = cached(mixes, track())
    calls = []

    async def prepare(client, user, today):
        assert not path.exists()
        calls.append(user)

    monkeypatch.setattr(mixes, "prepare_person", prepare)
    asyncio.run(mixes.prepare_pass(None))
    assert calls == [owner]


def test_saved_edition_reacquires_expired_audio_without_experimental_streaming(
    tmp_path, monkeypatch
):
    from server.media import save_settings

    app, owner = protected(tmp_path, monkeypatch)
    mixes = app.state.store.mixes
    user, token = member(app, owner)
    song = track()
    path = cached(mixes, song)
    edition = ready(mixes, user, [song])
    playlist = mixes.save(user, edition)
    with mixes.store.connect() as conn:
        conn.execute(
            "UPDATE mix_editions SET active=0,retired_at=? WHERE id=?",
            (time.time() - 7200, edition),
        )
    mixes.cache.cleanup()
    assert not path.exists()
    save_settings(mixes.store, portraits=True, remote_playback=False)
    calls = []

    def fetch(worker, canonical):
        calls.append(canonical)
        if len(calls) == 1:
            worker.result, worker.error = "failed", "No matching full recording"
            return False
        cached(mixes, canonical, now=time.time())
        worker.result, worker.error = "ready", None
        return True

    monkeypatch.setattr(MixDownloader, "fetch", fetch)
    with TestClient(
        app,
        base_url="https://private.ts.net",
        headers={"Authorization": "Bearer " + token},
    ) as client:
        assert client.post("/api/v1/remote-playback", json=song).status_code == 503
        assert mixes.cache.status()["failed"] == 1
        response = client.post("/api/v1/remote-playback", json=song)
        assert response.status_code == 200, response.text
        restored = response.json()["history_track_id"]
        assert (
            restored.startswith("mix:") and response.json()["source"] == "Prepared mix"
        )
        assert client.get(response.json()["url"]).status_code == 200
        assert client.post("/api/v1/remote-playback", json=song).status_code == 200
        assert len(calls) == 2 and mixes.cache.status()["failed"] == 0
        assert client.post("/api/v1/remote-playback", json=track(2)).status_code == 409
    assert mixes.list(user)["mixes"] == []  # Playback leases are not Home cards.
    assert mixes.save(user, edition)["id"] == playlist["id"]
    with mixes.store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM library_members").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM acquired").fetchone()[0] == 0
    assert (
        mixes.queue_saved(owner, song) is None
    )  # Another member's save grants nothing.
    mixes.cache.cleanup(time.time() + 7 * 3600)
    assert not path.exists()


def test_saved_mix_prefetch_is_durable_and_works_without_a_library(
    tmp_path, monkeypatch
):
    app, owner = protected(tmp_path, monkeypatch)
    mixes = app.state.store.mixes
    from unittest.mock import AsyncMock

    monkeypatch.setattr(mixes.home, "popular", AsyncMock(return_value={"songs": []}))
    song = track()
    edition = ready(mixes, owner, [song])
    mixes.save(owner, edition)
    with mixes.store.connect() as conn:
        conn.execute(
            "UPDATE mix_editions SET active=0,retired_at=? WHERE id=?",
            (time.time() - 7200, edition),
        )
    identity = mixes.queue_saved(owner, song)
    assert identity and mixes.list(owner)["mixes"] == []
    mixes.store.save_download_settings({"enabled": True, "sources": ["freemp3cloud"]})

    def fetch(worker, song):
        cached(mixes, song)
        worker.result = "ready"
        return True

    monkeypatch.setattr(MixDownloader, "fetch", fetch)
    asyncio.run(mixes.prepare_person(None, owner, mixes.day()))
    assert mixes.playback(owner, identity).is_file()
    assert not mixes.list(owner)["mixes"]


def test_hide_edition_is_private_does_not_break_queue_and_expires_with_rotation(
    tmp_path, monkeypatch
):
    app, owner = protected(tmp_path, monkeypatch)
    mixes = app.state.store.mixes
    user, token = member(app, owner)
    cached(mixes, track())
    edition = ready(mixes, user, [track()])
    other = ready(mixes, owner, [track()])
    with TestClient(
        app,
        base_url="https://private.ts.net",
        headers={"Authorization": "Bearer " + token},
    ) as client:
        assert client.post(f"/api/v1/mixes/{other}/hide").status_code == 404
        assert client.post(f"/api/v1/mixes/{edition}/hide").status_code == 200
        assert client.get("/api/v1/home/mixes").json() == {
            "mixes": [],
            "preparing": False,
            "radio": None,
        }
        assert client.post(f"/api/v1/playback/mix:{edition}:0/url").status_code == 200
    tomorrow = mixes.day() + timedelta(days=1)
    next_edition = ready(mixes, user, [track()], day=tomorrow)
    mixes.activate(tomorrow)
    assert mixes.list(user)["mixes"][0]["id"] == next_edition


def test_daily_exposure_changes_at_least_half_the_mix_across_a_week(
    tmp_path, monkeypatch
):
    app, owner = protected(tmp_path, monkeypatch)
    mixes = app.state.store.mixes
    owned = [track(n, id=f"owned-{n}", genre="Rock") for n in range(100)]
    discoveries = [
        track(n, bucket="related", reason="Similar to Artist 0")
        for n in range(100, 130)
    ]
    for song in owned + discoveries:
        cached(mixes, song, now=time.time())
    monkeypatch.setattr(
        mixes.store.user_libraries,
        "projection",
        lambda user: {"tracks": owned, "artists": [], "albums": []},
    )

    async def candidates(client, projection):
        return discoveries, "Rock"

    monkeypatch.setattr(mixes, "candidates", candidates)
    day = mixes.day()
    previous = set()
    for offset in range(7):
        today = day + timedelta(days=offset)
        monkeypatch.setattr(mixes, "day", lambda: today)
        mixes.activate(today)
        asyncio.run(mixes.prepare_person(None, owner, today))
        row = mixes.edition(owner, "daily", today)
        songs = json.loads(row[1])["tracks"]
        current = {song_key(t) for t in songs}
        assert len(current) == len(songs) >= 25
        assert all(a["artist"] != b["artist"] for a, b in zip(songs, songs[1:]))
        if previous:
            assert len(current - previous) >= len(current) / 2
        previous = current


def test_preparation_cursor_survives_a_time_budget_and_resumes_later_songs(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace
    import server.mixes as implementation

    app, owner = protected(tmp_path, monkeypatch)
    mixes = app.state.store.mixes
    mixes.store.save_download_settings({"enabled": True, "sources": ["freemp3cloud"]})
    edition = mixes.stage(
        owner, "daily", mixes.day(), "Daily", "Reason", [track(n) for n in range(1, 4)]
    )
    row = mixes.edition(owner, "daily", mixes.day())
    payload = json.loads(row[1])
    payload["attempts"] = 2
    mixes.update(edition, payload)
    clock, calls = [0], []
    monkeypatch.setattr(
        implementation,
        "time",
        SimpleNamespace(time=time.time, monotonic=lambda: clock[0]),
    )

    def fetch(worker, song):
        calls.append(song["title"])
        clock[0] += 1
        worker.result, worker.error = "failed", "No match"
        if song["title"] == "Song 1":
            return False
        cached(mixes, song)
        worker.result = "ready"
        return True

    monkeypatch.setattr(MixDownloader, "fetch", fetch)
    asyncio.run(mixes.prepare_staged(owner, mixes.day(), mixes.day(), deadline=2))
    assert json.loads(mixes.edition(owner, "daily", mixes.day())[1])["cursor"] == 2
    clock[0] = 0
    asyncio.run(mixes.prepare_staged(owner, mixes.day(), mixes.day(), deadline=2))
    assert calls == ["Song 1", "Song 2", "Song 3"]
    assert [
        t["title"]
        for t in json.loads(mixes.edition(owner, "daily", mixes.day())[1])["tracks"]
    ] == ["Song 2", "Song 3"]


def test_cache_status_reports_only_reclaimable_audio_and_real_failures(
    tmp_path, monkeypatch
):
    app, owner = protected(tmp_path, monkeypatch)
    mixes = app.state.store.mixes
    retained = cached(mixes, track())
    orphan = cached(mixes, track(2))
    ready(mixes, owner, [track()])
    identity = mixes.stage(
        owner, "artist", mixes.day(), "Around an artist", "Reason", [track(3)]
    )
    asyncio.run(mixes.prepare_staged(owner, mixes.day(), mixes.day()))
    status = mixes.cache.status()
    assert status["reclaimable_bytes"] == orphan.stat().st_size
    assert status["failed"] == 0
    assert status["issues"][0]["message"] == "Downloads are paused"
    mixes.cache.cleanup()
    assert retained.exists() and not orphan.exists()


def test_empty_library_is_not_reported_as_perpetually_preparing(tmp_path, monkeypatch):
    app, owner = protected(tmp_path, monkeypatch)
    mixes = app.state.store.mixes
    from unittest.mock import AsyncMock

    monkeypatch.setattr(mixes.home, "popular", AsyncMock(return_value={"songs": []}))
    asyncio.run(mixes.prepare_person(None, owner, mixes.day()))
    assert mixes.list(owner) == {"mixes": [], "preparing": False, "radio": None}


def test_automatic_and_on_demand_downloads_share_one_transfer(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    app, _ = protected(tmp_path, monkeypatch)
    cache = app.state.store.mix_cache
    entered, release = Event(), Event()
    calls = []

    def fetch(worker, song):
        calls.append(song)
        entered.set()
        assert release.wait(5)
        cached(app.state.store.mixes, song)
        worker.result, worker.error = "ready", None
        return True

    monkeypatch.setattr(MixDownloader, "_fetch", fetch)
    first = MixDownloader(cache, app.state.source_runtime, lambda: False)
    second = MixDownloader(cache, app.state.source_runtime, lambda: False)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(first.fetch, track())
        assert entered.wait(5)
        try:
            assert not second.fetch(track()) and second.result == "deferred"
        finally:
            release.set()
        assert pending.result(timeout=5)
    assert second.fetch(track()) and len(calls) == 1


@pytest.mark.parametrize("chart_count", [0, 12])
def test_local_fallback_fills_daily_and_chart_mix_has_its_own_size(
    tmp_path, monkeypatch, chart_count
):
    app, owner = protected(tmp_path, monkeypatch)
    mixes = app.state.store.mixes
    owned = [track(n, id=f"own-{n}") for n in range(100)]
    chart = [track(n, bucket="popular") for n in range(100, 100 + chart_count)]
    for song in owned + chart:
        cached(mixes, song, now=time.time())
    monkeypatch.setattr(
        mixes.store.user_libraries,
        "projection",
        lambda user: {"tracks": owned, "artists": [], "albums": []},
    )

    async def candidates(client, projection):
        return chart, None

    monkeypatch.setattr(mixes, "candidates", candidates)
    asyncio.run(mixes.prepare_person(None, owner, mixes.day()))
    daily = json.loads(mixes.edition(owner, "daily", mixes.day())[1])
    assert len(daily["tracks"]) == 30
    assert sum(t.get("bucket") == "popular" for t in daily["tracks"]) == min(
        3, chart_count
    )
    assert mixes.edition(owner, "artist", mixes.day()) is None
    if chart_count:
        popular = json.loads(mixes.edition(owner, "popular", mixes.day())[1])
        assert len(popular["tracks"]) == chart_count
    else:
        assert daily["reason"] == "A fresh selection from your library"


def test_genre_themes_rotate_without_a_large_discography_dominating(
    tmp_path, monkeypatch
):
    app, _ = protected(tmp_path, monkeypatch)
    mixes = app.state.store.mixes
    songs = [track(n, artist="One rock artist", genre="Rock") for n in range(500)]
    songs += [
        track(501, genre="Jazz"),
        track(502, genre="Jazz"),
        track(503, genre="K-pop"),
    ]

    async def seeds(*args):
        return []

    async def feed(*args, **kwargs):
        return {}, False

    async def popular(*args):
        return {"songs": []}

    monkeypatch.setattr(mixes.home, "seeds", seeds)
    monkeypatch.setattr(mixes.home, "feed", feed)
    monkeypatch.setattr(mixes.home, "popular", popular)
    genres = []
    for offset in range(3):
        day = mixes.day() + timedelta(days=offset)
        _, genre = asyncio.run(
            mixes.candidates(None, {"tracks": songs, "mix_day": str(day)})
        )
        genres.append(genre)
    assert set(genres) == {"Rock", "Jazz", "K-pop"}


def test_cold_start_chart_is_labelled_regional_and_does_not_use_another_library(
    tmp_path, monkeypatch
):
    from unittest.mock import AsyncMock

    app, owner = protected(tmp_path, monkeypatch)
    mixes = app.state.store.mixes
    song = track(musicbrainz_recording_id="recording", musicbrainz_release_id="release")
    cached(mixes, song, now=time.time())
    monkeypatch.setattr(
        mixes.home,
        "popular",
        AsyncMock(
            return_value={
                "title": "Popular in Australia",
                "songs": [
                    {
                        "id": "recording",
                        "release_id": "release",
                        "name": song["title"],
                        "artist": song["artist"],
                        "album": song["album"],
                    }
                ],
            }
        ),
    )
    asyncio.run(mixes.prepare_person(None, owner, mixes.day()))
    feed = mixes.list(owner)
    assert len(feed["mixes"]) == 1
    assert feed["mixes"][0]["title"] == "Popular in Australia"
    assert "Regional chart" in feed["mixes"][0]["reason"]
    assert mixes.edition(owner, "daily", mixes.day()) is None


def test_imported_taste_seeds_discoveries_before_its_files_are_available(
    tmp_path, monkeypatch
):
    app, owner = protected(tmp_path, monkeypatch)
    mixes = app.state.store.mixes
    imported = track(10, id="imported", available=False)
    discovered = track(20, bucket="related")
    cached(mixes, discovered, now=time.time())
    monkeypatch.setattr(
        mixes.store.user_libraries,
        "projection",
        lambda user: {"tracks": [imported], "artists": [], "albums": []},
    )

    async def candidates(client, projection):
        assert projection["tracks"] == [imported]
        return [discovered], None

    monkeypatch.setattr(mixes, "candidates", candidates)
    asyncio.run(mixes.prepare_person(None, owner, mixes.day()))
    daily = mixes.list(owner)["mixes"][0]
    assert (
        daily["kind"] == "daily"
        and daily["reason"] == "Discoveries inspired by your library"
    )
    assert mixes.edition(owner, "back", mixes.day()) is None


def test_a_stalled_metadata_provider_cannot_starve_the_next_person(
    tmp_path, monkeypatch
):
    import server.mixes as implementation

    app, owner = protected(tmp_path, monkeypatch)
    user, _ = member(app, owner)
    mixes = app.state.store.mixes
    completed = []

    async def prepare(client, person, today):
        if person == owner:
            await asyncio.Event().wait()
        completed.append(person)

    monkeypatch.setattr(implementation, "PASS_SECONDS", 0.02)
    monkeypatch.setattr(mixes, "prepare_person", prepare)
    asyncio.run(mixes.prepare_pass(None))
    assert completed == [user]


def test_shutdown_cancels_a_pass_that_ignores_the_stop_flag(tmp_path, monkeypatch):
    import server.mixes as implementation

    app, _ = protected(tmp_path, monkeypatch)
    mixes = app.state.store.mixes

    async def stuck(client):
        await asyncio.Event().wait()

    monkeypatch.setattr(implementation, "SHUTDOWN_SECONDS", 0.05)
    monkeypatch.setattr(mixes, "prepare_pass", stuck)

    async def run():
        mixes.stopping = asyncio.Event()
        mixes.start(None)
        await asyncio.sleep(0)
        await asyncio.wait_for(mixes.close(), timeout=2)
        return mixes.task.cancelled()

    assert asyncio.run(run())


def test_new_families_follow_their_selection_rules(tmp_path, monkeypatch):
    app, owner = protected(tmp_path, monkeypatch)
    store = app.state.store
    mixes = store.mixes
    owned = [
        track(n, id=f"owned-{n}", genre="Rock", year=1990 + n % 10) for n in range(40)
    ]
    pool = [
        track(n, genre="Rock", reason="Similar to Artist 0", bucket="related")
        for n in range(100, 160)
    ]
    pool += [track(n, bucket="decade", decade=1990) for n in range(160, 172)]
    pool += [
        track(n, bucket="release", reason="New from Artist 1") for n in range(172, 176)
    ]
    for song in owned + pool:
        cached(mixes, song, now=time.time())
    monkeypatch.setattr(
        store.user_libraries,
        "projection",
        lambda user: {"tracks": owned, "artists": [], "albums": []},
    )

    async def candidates(client, projection):
        return pool, "Rock"

    monkeypatch.setattr(mixes, "candidates", candidates)
    now = int(time.time())
    with store.connect() as conn:
        # Two plays make a song no longer a deep cut.
        for n in range(5):
            for event in range(2):
                conn.execute(
                    "INSERT INTO play_events(user_id,track_id,played_at,seconds,device_session_id,event_id) VALUES (?,?,?,?,?,?)",
                    (owner, f"owned-{n}", now, 60, "fixture", f"e{n}-{event}"),
                )
        for n in range(40):
            conn.execute(
                "INSERT INTO library_members VALUES (?,?,?,?)",
                (
                    owner,
                    f"owned-{n}",
                    now - (86400 if n < 12 else 40 * 86400),
                    "import" if n != 0 else "migration",
                ),
            )
    today = mixes.day()
    asyncio.run(mixes.prepare_person(None, owner, today))

    def songs(kind):
        row = mixes.edition(owner, kind, today)
        return json.loads(row[1])["tracks"] if row else None

    daily = songs("daily")
    assert sum(t.get("bucket") in {"related", "decade"} for t in daily) == 15
    fresh = songs("fresh")
    assert len(fresh) >= 10 and all(t.get("bucket") for t in fresh)
    assert not {song_key(t) for t in fresh} & {song_key(t) for t in daily}
    assert {t["title"] for t in songs("releases")} <= {
        f"Song {n}" for n in range(172, 176)
    }
    deep = {t["id"] for t in songs("deep")}
    assert deep and not deep & {f"owned-{n}" for n in range(5)}
    # Only recent, non-migration additions; eleven qualify.
    assert {t["id"] for t in songs("recent")} == {f"owned-{n}" for n in range(1, 12)}
    decade = json.loads(mixes.edition(owner, "decade", today)[1])
    assert decade["title"] == "Your 1990s"
    assert any(t.get("bucket") == "decade" for t in decade["tracks"])
    genre = songs("genre")
    assert sum(1 for t in genre if t.get("bucket")) == 15


def test_artist_neighbourhood_mix_can_hold_more_of_the_artist():
    songs = [track(n, artist="Focus") for n in range(10)]
    songs += [track(n, artist=f"Neighbour {n}") for n in range(10, 30)]
    picked = choose(songs, 25, "day", per_artist=8)
    assert sum(t["artist"] == "Focus" for t in picked) == 8
    assert len(choose(songs, 25, "day")) == 22  # Default keeps two per artist.
    assert all(a["artist"] != b["artist"] for a, b in zip(picked, picked[1:]))


def test_recent_releases_come_from_resolved_library_artists(tmp_path, monkeypatch):
    app, _ = protected(tmp_path, monkeypatch)
    store = app.state.store
    mixes = store.mixes
    artist = {"id": "local-a", "name": "Artist A"}
    store.write_document(
        "library-artist:local-a",
        {"id": "a0e8a8f3-1a2b-4c3d-9e8f-000000000001", "name": "Artist A"},
    )
    day = mixes.day()
    calls = []

    async def get(client, path, **params):
        calls.append((path, params))
        recording = lambda n, date_text: {
            "id": f"0000000{n}-0000-4000-8000-000000000000",
            "title": f"New {n}",
            "artist-credit": [{"name": "Artist A", "artist": {"name": "Artist A"}}],
            "releases": [
                {
                    "id": f"1000000{n}-0000-4000-8000-000000000000",
                    "title": "Fresh album",
                    "status": "Official",
                    "date": date_text,
                    "release-group": {"primary-type": "Album"},
                }
            ],
        }
        return {
            "recordings": [
                recording(1, str(day)),
                recording(2, "1999-01-01"),  # A reissue of an old song.
                recording(3, str(day)),
                recording(4, str(day)),
                recording(5, str(day)),
            ]
        }

    monkeypatch.setattr(mixes.home.catalogue, "get", get)
    found = asyncio.run(
        mixes.releases(None, {"artists": [artist], "mix_day": str(day)})
    )
    assert [t["title"] for t in found] == ["New 1", "New 3", "New 4"]
    assert all(t["bucket"] == "release" for t in found)
    assert "firstreleasedate" in calls[0][1]["query"]
    assert asyncio.run(mixes.releases(None, {"artists": [], "mix_day": str(day)})) == []


def test_radio_appends_stable_entries_pages_and_learns_from_skips(
    tmp_path, monkeypatch
):
    app, owner = protected(tmp_path, monkeypatch)
    store = app.state.store
    mixes = store.mixes
    user, token = member(app, owner)
    owned = [track(n, id=f"owned-{n}", artist=f"Owner {n}") for n in range(60)]
    found = [
        track(n, bucket="related", artist=f"New {n}", reason="Similar to Owner 1")
        for n in range(100, 140)
    ]
    for song in owned + found:
        cached(mixes, song, now=time.time())
    monkeypatch.setattr(
        store.user_libraries,
        "projection",
        lambda person: {"tracks": owned, "artists": [], "albums": []},
    )
    today = mixes.day()
    mixes.stage(user, "fresh", today, "Fresh finds", "Reason", found)
    asyncio.run(mixes.prepare_radio(user, today))
    edition = mixes.radio_row(user, today)[0]
    payload = json.loads(mixes.radio_row(user, today)[1])
    assert len(payload["tracks"]) == 20 and not payload["queue"]
    assert sum(t.get("bucket") == "related" for t in payload["tracks"]) == 10
    # Radio is never promoted to an ordinary Home mix, but has its own card.
    mixes.activate(today)
    feed = mixes.list(user)
    assert all(m["kind"] != "radio" for m in feed["mixes"])
    assert feed["radio"] == {"title": "Member's radio", "ready": 20}
    with TestClient(
        app,
        base_url="https://private.ts.net",
        headers={"Authorization": "Bearer " + token},
    ) as client:
        first = client.get("/api/v1/radio").json()
        assert [t["id"] for t in first["tracks"]] == [
            f"mix:{edition}:{n}" for n in range(10)
        ]
        assert first["title"] == "Member's radio"
        url = client.post(f"/api/v1/playback/{first['tracks'][0]['id']}/url")
        assert url.status_code == 200, url.text
        page = client.get("/api/v1/radio", params={"after": first["tracks"][-1]["id"]})
        assert [t["id"] for t in page.json()["tracks"]] == [
            f"mix:{edition}:{n}" for n in range(10, 20)
        ]
        assert page.json()["preparing"] is True
        skipped = first["tracks"][0]
        assert (
            client.post(
                "/api/v1/radio/feedback",
                json={"track_id": skipped["id"], "action": "skip"},
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/api/v1/radio/feedback", json={"track_id": "mix:x:0", "action": "skip"}
            ).status_code
            == 404
        )
    # Entries already handed out never move when the station grows.
    mixes.stopping.clear()  # Leaving the client shut the app's worker down.
    asyncio.run(mixes.prepare_radio(user, today))
    grown = json.loads(mixes.radio_row(user, today)[1])
    assert len(grown["tracks"]) == 40
    assert grown["tracks"][:20] == payload["tracks"]
    weights = mixes.radio_weights(user, [skipped, owned[59]])
    assert weights[song_key(skipped)] < weights[song_key(owned[59])]
    # Another person cannot play or steer this station.
    assert mixes.radio_next(owner)["id"] != edition
    with pytest.raises(LookupError):
        mixes.radio_feedback(owner, skipped["id"], "like")


def test_short_mixes_are_topped_up_from_the_library_or_never_shown(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(mix_module, "MIN_MIX", 3)
    app, owner = protected(tmp_path, monkeypatch)
    mixes = app.state.store.mixes
    for n in (1, 2, 3, 5, 6):
        cached(mixes, track(n))
    today = mixes.day()
    # Too few songs even with backfill: nothing is staged.
    assert mixes.plan(owner, "fresh", today, "Fresh", "Why", [track(1)]) is None
    topped = mixes.plan(
        owner,
        "daily",
        today,
        "Daily",
        "Why",
        [track(1), track(2)],
        backfill=[track(2), track(3), track(4)],
    )
    short = mixes.plan(
        owner,
        "genre",
        today,
        "Genre",
        "Why",
        [track(5)],
        backfill=[track(6), track(7)],  # Song 7 has no audio.
    )
    asyncio.run(mixes.prepare_staged(owner, today, today))
    listed = mixes.list(owner)["mixes"]
    assert [m["id"] for m in listed] == [topped]
    assert [t.title for t in listed[0]["tracks"]] == ["Song 1", "Song 2", "Song 3"]
    with mixes.store.connect() as conn:
        state, retired = conn.execute(
            "SELECT json_extract(payload,'$.state'),retired_at FROM mix_editions WHERE id=?",
            (short,),
        ).fetchone()
    assert state == "short" and retired
    assert not mixes.list(owner)["preparing"]


def test_short_editions_published_before_the_minimum_leave_home(tmp_path, monkeypatch):
    monkeypatch.setattr(mix_module, "MIN_MIX", 3)
    app, owner = protected(tmp_path, monkeypatch)
    mixes = app.state.store.mixes
    for n in (1, 2, 3):
        cached(mixes, track(n))
    ready(mixes, owner, [track(1), track(2)], kind="artist")
    full = ready(mixes, owner, [track(1), track(2), track(3)], kind="genre")
    assert [m["id"] for m in mixes.list(owner)["mixes"]] == [full]


def test_artist_mix_backfill_prefers_the_artist_then_neighbours_then_genre(
    monkeypatch,
):
    monkeypatch.setattr(mix_module, "MIN_MIX", 20)
    owned = [track(n, artist="Focus", genre="Rock") for n in range(3)]
    owned += [track(n, artist="Near", genre="Pop") for n in range(3, 5)]
    owned += [track(n, artist="Other", genre="Rock") for n in range(5, 7)]
    owned += [track(n, artist="Unrelated", genre="Jazz") for n in range(7, 9)]
    fill = mix_module.Mixes.neighbourhood_fill(
        owned, "Focus", [track(99, artist="Near")], "salt", {}
    )
    assert [t["artist"] for t in fill[:3]] == ["Focus"] * 3
    assert {t["artist"] for t in fill[3:]} == {"Near", "Other"}
