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
    assert year["top_mixes"] == [{"title": "Daily blend", "plays": 2}]
    assert year["top_songs"][0]["track"]["title"] in {"Song 0", "Song 1"}
