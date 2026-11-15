"""Made for you, batch 11: similar songs when a queue ends (#202) and Rewind (#203)."""

import asyncio
from datetime import datetime
import json
import time
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from server.mix_cache import mix_key
from test_mixes import cached, member, track
from test_security import protected

ZONE = ZoneInfo("Australia/Melbourne")


def at(*parts):
    """Epoch seconds for a Melbourne wall-clock time."""
    return int(datetime(*parts, tzinfo=ZONE).timestamp())


def listen(store, user, track_id, played_at, seconds=200, event=None):
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO play_events(user_id,track_id,played_at,seconds,device_session_id,event_id) VALUES (?,?,?,?,?,?)",
            (
                user,
                track_id,
                played_at,
                seconds,
                "fixture",
                event or f"{track_id}-{played_at}",
            ),
        )


def client(app, token):
    return TestClient(
        app,
        base_url="https://private.ts.net",
        headers={"Authorization": "Bearer " + token},
    )


def station(app, monkeypatch, owned, found):
    """A member whose radio holds `owned` and `found`, all cached."""
    store = app.state.store
    mixes = store.mixes
    for song in owned + found:
        cached(mixes, song, now=time.time())
    monkeypatch.setattr(
        store.user_libraries,
        "projection",
        lambda person: {"tracks": owned, "artists": [], "albums": []},
    )


# --- #202: keep playing similar songs -------------------------------------


def test_a_finished_queue_continues_with_similar_songs_first(tmp_path, monkeypatch):
    app, owner = protected(tmp_path, monkeypatch)
    store = app.state.store
    mixes = store.mixes
    user, token = member(app, owner)
    owned = [
        track(n, id=f"owned-{n}", artist="Seed Artist" if n < 3 else f"Owner {n}")
        for n in range(30)
    ]
    found = [
        track(n, bucket="related", artist=f"New {n}", genre="Jazz")
        for n in range(100, 130)
    ]
    station(app, monkeypatch, owned, found)
    today = mixes.day()
    mixes.stage(user, "fresh", today, "Fresh finds", "Reason", found)
    asyncio.run(mixes.prepare_radio(user, today))
    edition = mixes.radio_row(user, today)[0]
    payload = json.loads(mixes.radio_row(user, today)[1])
    entries = {mix_key(t): n for n, t in enumerate(payload["tracks"])}
    # Hidden, just played and already queued songs never come back.
    seed_songs = [t for t in payload["tracks"] if t["artist"] == "Seed Artist"]
    others = [t for t in payload["tracks"] if t["artist"] != "Seed Artist"]
    hidden, played, queued = others[0], others[1], others[2]
    with store.connect() as conn:
        conn.execute("INSERT INTO mix_feedback VALUES (?,?)", (user, mix_key(hidden)))
    with client(app, token) as http:
        # Played ten minutes ago, as the apps report it.
        heard = http.post(
            "/api/v1/play-events",
            json={
                "events": [
                    {
                        "track_id": f"mix:{edition}:{entries[mix_key(played)]}",
                        "seconds": 200,
                        "played_at": int(time.time()) - 600,
                        "event_id": "played",
                    }
                ]
            },
        )
        assert heard.status_code == 200, heard.text
        answer = http.post(
            "/api/v1/radio/continuation",
            json={
                "seeds": ["owned-0"],
                "exclude": [f"mix:{edition}:{entries[mix_key(queued)]}"],
            },
        )
        assert answer.status_code == 200, answer.text
        body = answer.json()
        assert body["title"] == "Similar songs"
        songs = body["tracks"]
        assert 0 < len(songs) <= 10
        keys = [mix_key(payload["tracks"][int(s["id"].split(":")[2])]) for s in songs]
        assert not {mix_key(hidden), mix_key(played), mix_key(queued)} & set(keys)
        # The seed itself isn't played again.
        assert mix_key(owned[0]) not in keys
        # Songs by the seed's artist come first, then the seed-less rest.
        similar = {mix_key(t) for t in seed_songs} - {mix_key(owned[0])}
        flags = [k in similar for k in keys]
        assert flags == sorted(flags, reverse=True)
        assert any(flags) == bool(similar)
        # Another person's songs are no seeds and no continuation.
        assert (
            http.post(
                "/api/v1/radio/continuation", json={"seeds": [], "exclude": []}
            ).status_code
            == 200
        )
    assert mixes.radio_next(owner)["id"] != edition


def test_continuation_falls_back_to_the_library_while_the_radio_prepares(
    tmp_path, monkeypatch
):
    app, owner = protected(tmp_path, monkeypatch)
    store = app.state.store
    user, token = member(app, owner)
    owned = [track(n, id=f"owned-{n}", artist=f"Owner {n}") for n in range(5)]
    station(app, monkeypatch, owned, [])
    listen(store, user, "owned-1", int(time.time()) - 60)
    with client(app, token) as http:
        body = http.post(
            "/api/v1/radio/continuation", json={"seeds": ["owned-0"], "exclude": []}
        ).json()
    ids = [s["id"] for s in body["tracks"]]
    assert ids and set(ids) <= {"owned-2", "owned-3", "owned-4"}
    assert all(s["server_state"] == "in_library" for s in body["tracks"])
    assert body["preparing"] is True


def test_a_skip_of_a_library_song_steers_the_radio(tmp_path, monkeypatch):
    app, owner = protected(tmp_path, monkeypatch)
    user, token = member(app, owner)
    owned = [track(n, id=f"owned-{n}", artist=f"Owner {n}") for n in range(3)]
    station(app, monkeypatch, owned, [])
    with client(app, token) as http:
        answer = http.post(
            "/api/v1/radio/feedback", json={"track_id": "owned-1", "action": "skip"}
        )
        assert answer.status_code == 200, answer.text
        assert (
            http.post(
                "/api/v1/radio/feedback", json={"track_id": "nobody", "action": "skip"}
            ).status_code
            == 404
        )
    weights = app.state.store.mixes.radio_weights(user, owned)
    assert weights[mix_key(owned[1])] < weights[mix_key(owned[2])]


def test_keep_playing_is_a_preference_on_by_default(tmp_path, monkeypatch):
    app, owner = protected(tmp_path, monkeypatch)
    _, token = member(app, owner)
    with client(app, token) as http:
        assert http.get("/api/v1/preferences").json()["keep_playing"] is True
        saved = http.put(
            "/api/v1/preferences",
            json={
                "appearance": "dark",
                "default_tab": "artists",
                "keep_playing": False,
            },
        )
        assert saved.status_code == 200, saved.text
        assert http.get("/api/v1/preferences").json()["keep_playing"] is False
        # An older app that doesn't know the setting leaves it as it was.
        http.put("/api/v1/preferences", json={"appearance": "light"})
        assert http.get("/api/v1/preferences").json()["keep_playing"] is False


# --- #203: Rewind ---------------------------------------------------------


def test_rewind_counts_this_weeks_listening_in_the_households_time_zone(
    tmp_path, monkeypatch
):
    app, owner = protected(tmp_path, monkeypatch)
    store = app.state.store
    user, token = member(app, owner)
    owned = [
        track(1, id="a1", artist="Alpha", album="First"),
        track(2, id="a2", artist="Alpha", album="First"),
        track(3, id="b1", artist="Beta", album="Second"),
        track(4, id="c1", artist="Gamma", album="Third"),
    ]
    station(app, monkeypatch, owned, [])
    # Thursday 19 Nov 2026, noon in Melbourne; the week began Monday 16 Nov.
    now = at(2026, 11, 19, 12, 0)
    listen(store, user, "a1", at(2026, 11, 16, 0, 30), 200)
    listen(store, user, "a1", at(2026, 11, 18, 9, 0), 200)
    listen(store, user, "a2", at(2026, 11, 18, 10, 0), 100)
    listen(store, user, "b1", at(2026, 11, 18, 11, 0), 160)
    # A skip under 30 s isn't a listen.
    listen(store, user, "c1", at(2026, 11, 18, 12, 0), 20)
    # Sunday 23:30, last week in Melbourne (Sunday 12:30 UTC): not this week.
    listen(store, user, "b1", at(2026, 11, 15, 23, 30), 200)
    # Gamma played fully last month: not a new artist this week.
    listen(store, user, "c1", at(2026, 10, 2, 12, 0), 200)
    # Another person's listening is never counted.
    listen(store, owner, "a1", at(2026, 11, 18, 9, 0), 999, event="owner")
    monkeypatch.setattr("server.rewind.time.time", lambda: now)
    with client(app, token) as http:
        answer = http.get("/api/v1/rewind", params={"period": "week"})
        assert answer.status_code == 200, answer.text
        week = answer.json()
    assert week["start"] == at(2026, 11, 16, 0, 0)
    assert week["plays"] == 4
    assert week["minutes"] == round((200 + 200 + 100 + 160) / 60)
    assert [s["track"]["id"] for s in week["top_songs"]][:1] == ["a1"]
    assert week["top_songs"][0]["plays"] == 2
    assert [a["name"] for a in week["top_artists"]] == ["Alpha", "Beta"]
    assert week["top_artists"][0]["plays"] == 3
    assert week["top_albums"][0]["title"] == "First"
    # Beta was first played last Sunday: not new this week; Alpha is.
    assert week["new_artists"] == ["Alpha"]
    with client(app, token) as http:
        month = http.get("/api/v1/rewind", params={"period": "month"}).json()
        assert month["plays"] == 5
        assert (
            http.get("/api/v1/rewind", params={"period": "decade"}).status_code == 422
        )


def test_rewind_names_the_mixes_that_worked(tmp_path, monkeypatch):
    app, owner = protected(tmp_path, monkeypatch)
    store = app.state.store
    mixes = store.mixes
    user, token = member(app, owner)
    station(app, monkeypatch, [], [])
    songs = [track(n) for n in range(3)]
    for song in songs:
        cached(mixes, song, now=time.time())
    daily = mixes.stage(user, "daily", mixes.day(), "Daily blend", "Reason", songs)
    mixes.publish(daily, songs, mixes.day())
    now = int(time.time())
    listen(store, user, f"mix:{daily}:0", now - 100)
    listen(store, user, f"mix:{daily}:1", now - 50)
    with client(app, token) as http:
        year = http.get("/api/v1/rewind", params={"period": "year"}).json()
    # The edition and its kind, for the mix's cover (#216).
    assert year["top_mixes"] == [
        {"title": "Daily blend", "plays": 2, "id": daily, "kind": "daily"}
    ]
    assert year["top_songs"][0]["track"]["title"] in {"Song 0", "Song 1"}


def report(http, track_id, played_at, event):
    """A listen as both apps report it: once, at 30 s heard."""
    answer = http.post(
        "/api/v1/play-events",
        json={
            "events": [
                {
                    "track_id": track_id,
                    "seconds": 30,
                    "played_at": played_at,
                    "event_id": event,
                }
            ]
        },
    )
    assert answer.status_code == 200, answer.text


def test_rewind_minutes_count_the_songs_heard_not_the_30_s_report(
    tmp_path, monkeypatch
):
    # Codex round 1: the apps send seconds=30 once per listen; a four-minute
    # song listened to counts as four minutes.
    app, owner = protected(tmp_path, monkeypatch)
    store = app.state.store
    mixes = store.mixes
    user, token = member(app, owner)
    station(app, monkeypatch, [], [])
    songs = [track(n, duration=240) for n in range(2)]
    for song in songs:
        cached(mixes, song, now=time.time())
    daily = mixes.stage(user, "daily", mixes.day(), "Daily blend", "Reason", songs)
    mixes.publish(daily, songs, mixes.day())
    now = int(time.time())
    with client(app, token) as http:
        report(http, f"mix:{daily}:0", now - 600, "one")
        report(http, f"mix:{daily}:1", now - 300, "two")
        week = http.get("/api/v1/rewind", params={"period": "year"}).json()
    assert week["plays"] == 2 and week["minutes"] == 8
    assert week["top_artists"][0]["minutes"] == 4


def test_rewind_keeps_a_year_of_listening_after_old_mixes_are_deleted(
    tmp_path, monkeypatch
):
    # Codex round 1: retired editions are deleted after 31 days; their
    # listens still rank songs, artists and mixes, and keep artists not new.
    app, owner = protected(tmp_path, monkeypatch)
    store = app.state.store
    mixes = store.mixes
    user, token = member(app, owner)
    station(app, monkeypatch, [], [])
    songs = [track(n, duration=180, artist="Kept Artist") for n in range(2)]
    for song in songs:
        cached(mixes, song, now=time.time())
    daily = mixes.stage(user, "daily", mixes.day(), "Old blend", "Reason", songs)
    mixes.publish(daily, songs, mixes.day())
    now = int(time.time())
    with client(app, token) as http:
        report(http, f"mix:{daily}:0", now - 600, "kept")
        with store.connect() as conn:
            conn.execute("DELETE FROM mix_editions WHERE id=?", (daily,))
        year = http.get("/api/v1/rewind", params={"period": "year"}).json()
    assert year["top_artists"][0]["name"] == "Kept Artist"
    assert year["top_mixes"] == [
        {"title": "Old blend", "plays": 1, "id": None, "kind": None}
    ]
    song = year["top_songs"][0]["track"]
    assert song["title"] == "Song 0" and song["available"] is False


def test_the_migration_keeps_details_of_earlier_mix_listens(tmp_path, monkeypatch):
    import importlib

    app, owner = protected(tmp_path, monkeypatch)
    store = app.state.store
    mixes = store.mixes
    user, _ = member(app, owner)
    songs = [track(n, artist="Earlier") for n in range(2)]
    daily = mixes.stage(user, "daily", mixes.day(), "Earlier blend", "Reason", songs)
    listen(store, user, f"mix:{daily}:1", int(time.time()) - 100, event="earlier")
    listen(store, user, "mix:gone:0", int(time.time()) - 90, event="gone")
    migration = importlib.import_module("server.migrations.0014_listen_details")
    with store.connect() as conn:
        conn.execute("DROP TABLE listen_details")
        migration.up(conn)
        rows = conn.execute(
            "SELECT event_id,title,artist,mix_title FROM listen_details"
        ).fetchall()
    assert rows == [("earlier", "Song 1", "Earlier", "Earlier blend")]


def test_rewind_never_offers_a_mix_song_playback_would_refuse(tmp_path, monkeypatch):
    # Codex round 2: an edition retired over six hours ago, its lease over,
    # keeps its cached audio (another edition may hold the same song), but
    # playback refuses it; Rewind must not offer it to Play your top songs.
    app, owner = protected(tmp_path, monkeypatch)
    store = app.state.store
    mixes = store.mixes
    user, token = member(app, owner)
    station(app, monkeypatch, [], [])
    songs = [track(n, duration=180) for n in range(2)]
    for song in songs:
        cached(mixes, song, now=time.time())
    daily = mixes.stage(user, "daily", mixes.day(), "Old blend", "Reason", songs)
    mixes.publish(daily, songs, mixes.day())
    now = int(time.time())
    with client(app, token) as http:
        report(http, f"mix:{daily}:0", now - 600, "old")
        live = http.get("/api/v1/rewind", params={"period": "year"}).json()
        assert live["top_songs"][0]["track"]["available"] is True
        with store.connect() as conn:
            conn.execute(
                "UPDATE mix_editions SET active=0,retired_at=?,lease_until=0 WHERE id=?",
                (now - 7 * 3600, daily),
            )
        assert http.post(f"/api/v1/playback/mix:{daily}:0/url").status_code == 404
        year = http.get("/api/v1/rewind", params={"period": "year"}).json()
    assert year["top_songs"][0]["track"]["available"] is False


# --- #216: Rewind's days, pages and covers ---------------------------------


def test_rewind_counts_each_local_day_up_to_today_across_a_clock_change(
    tmp_path, monkeypatch
):
    app, owner = protected(tmp_path, monkeypatch)
    store = app.state.store
    user, token = member(app, owner)
    owned = [
        track(1, id="a1", duration=240),
        track(2, id="b1", duration=120),
    ]
    station(app, monkeypatch, owned, [])
    # The apps report each listen once, at 30 s: it counts as the song.
    # Melbourne's clocks go forward at 2 am on Sunday 4 Oct 2026: that day
    # has 23 hours. Tuesday 6 Oct; the week began Monday 5 Oct.
    now = at(2026, 10, 6, 15, 0)
    # Last month: not in October.
    listen(store, user, "a1", at(2026, 9, 30, 9, 0), 30)
    listen(store, user, "a1", at(2026, 10, 1, 9, 0), 30)
    listen(store, user, "b1", at(2026, 10, 3, 23, 30), 30)
    listen(store, user, "a1", at(2026, 10, 4, 1, 30), 30)
    listen(store, user, "a1", at(2026, 10, 4, 3, 30), 30)
    listen(store, user, "b1", at(2026, 10, 4, 23, 59), 30)
    # Half past midnight on Monday: Monday, though 24 hours after Sunday's
    # midnight is not yet.
    listen(store, user, "a1", at(2026, 10, 5, 0, 30), 30)
    # Reported a moment ahead of the server's clock: still today.
    listen(store, user, "b1", now + 120, 30)
    monkeypatch.setattr("server.rewind.time.time", lambda: now)
    with client(app, token) as http:
        week = http.get("/api/v1/rewind", params={"period": "week"}).json()
        month = http.get("/api/v1/rewind", params={"period": "month"}).json()
    midnights = [at(2026, 10, n, 0, 0) for n in range(1, 7)]
    assert midnights[4] - midnights[3] == 23 * 3600
    # October so far: the 1st to today, and nothing after now.
    assert [d["start"] for d in month["days"]] == midnights
    assert [d["plays"] for d in month["days"]] == [1, 0, 1, 3, 1, 1]
    assert [d["minutes"] for d in month["days"]] == [4, 0, 2, 10, 4, 2]
    assert sum(d["plays"] for d in month["days"]) == month["plays"]
    assert [d["start"] for d in week["days"]] == midnights[4:]
    assert [(d["plays"], d["minutes"]) for d in week["days"]] == [(1, 4), (1, 2)]


def test_rewind_days_stop_at_today(tmp_path, monkeypatch):
    app, owner = protected(tmp_path, monkeypatch)
    user, token = member(app, owner)
    station(app, monkeypatch, [], [])
    # Thursday 19 Nov 2026: Monday to Thursday, no later days.
    now = at(2026, 11, 19, 12, 0)
    monkeypatch.setattr("server.rewind.time.time", lambda: now)
    with client(app, token) as http:
        week = http.get("/api/v1/rewind", params={"period": "week"}).json()
        year = http.get("/api/v1/rewind", params={"period": "year"}).json()
    assert [d["start"] for d in week["days"]] == [
        at(2026, 11, n, 0, 0) for n in range(16, 20)
    ]
    assert all(d["plays"] == 0 and d["minutes"] == 0 for d in week["days"])
    # 1 Jan to 19 Nov 2026.
    assert len(year["days"]) == 323
    assert year["days"][0]["start"] == at(2026, 1, 1, 0, 0)
    assert year["days"][-1]["start"] == at(2026, 11, 19, 0, 0)


def test_rewind_links_artists_and_albums_to_their_library_or_catalogue_pages(
    tmp_path, monkeypatch
):
    from server.rewind import cover

    app, owner = protected(tmp_path, monkeypatch)
    store = app.state.store
    mixes = store.mixes
    user, token = member(app, owner)
    artist_mbid = "0383dadf-2a4e-4d10-a46a-e9e041da8eb3"
    release = "f5093c06-23e3-404f-aeaa-40f72885ee3a"
    owned = [
        track(1, id="a1", artist="Alpha", album="First", album_artist="Alpha"),
        track(2, id="a2", artist="Alpha", album="First", album_artist="Alpha"),
        track(3, id="a3", artist="Alpha", album="Second", album_artist="Alpha"),
    ]
    for song, (album_id, art) in zip(
        owned,
        [("album-first", "first"), ("album-first", "first"), ("album-2", "second")],
    ):
        song.update(album_id=album_id, cover_url=f"/api/v1/artwork/{art}.jpg")
    monkeypatch.setattr(
        store.user_libraries,
        "projection",
        lambda person: {
            "tracks": owned,
            "artists": [{"id": "artist-alpha", "name": "Alpha"}],
            "albums": [
                {"id": "album-first", "title": "First", "artist": "Alpha"},
                {
                    "id": "album-2",
                    "title": "Second",
                    "artist": "Alpha",
                    "musicbrainz_release_id": "second-release",
                },
            ],
        },
    )
    store.write_document(
        "library-artist:artist-alpha", {"id": artist_mbid, "name": "Alpha"}
    )
    found = [
        track(
            9,
            artist="Beta",
            album="Third",
            album_artist="Beta",
            musicbrainz_release_id=release,
        )
    ]
    cached(mixes, found[0], now=time.time())
    daily = mixes.stage(user, "daily", mixes.day(), "Daily blend", "Reason", found)
    mixes.publish(daily, found, mixes.day())
    now = int(time.time())
    # Alpha was heard before this year; Beta is new.
    listen(store, user, "a1", at(2025, 6, 1, 12, 0))
    for n, song in enumerate(["a1", "a2", "a1", "a3"]):
        listen(store, user, song, now - 1000 + n)
    listen(store, user, f"mix:{daily}:0", now - 100)
    with client(app, token) as http:
        year = http.get("/api/v1/rewind", params={"period": "year"}).json()
    alpha, beta = year["top_artists"]
    assert alpha == {
        "name": "Alpha",
        "plays": 4,
        "minutes": round(4 * 200 / 60),
        "id": "artist-alpha",
        "musicbrainz_artist_id": artist_mbid,
        "new": False,
        # The most played album's cover, not the last one heard.
        "cover_url": cover(owned[0]),
    }
    assert beta["id"] is None and beta["musicbrainz_artist_id"] is None
    assert beta["new"] is True
    assert beta["cover_url"] == f"/api/v1/catalogue/artwork/release/{release}"
    assert year["new_artists"] == ["Beta"]
    albums = {a["title"]: a for a in year["top_albums"]}
    assert albums["First"]["id"] == "album-first"
    assert albums["First"]["musicbrainz_release_id"] is None
    assert albums["Second"]["id"] == "album-2"
    assert albums["Second"]["musicbrainz_release_id"] == "second-release"
    # Not in the library: the catalogue release page only.
    assert albums["Third"]["id"] is None
    assert albums["Third"]["musicbrainz_release_id"] == release
