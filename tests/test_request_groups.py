import json

from fastapi.testclient import TestClient

from server.app import create_app
from server.auth import Auth
from server.store import Store


def song(title, number, album="Meteora", duration=200):
    return {
        "artist": "Linkin Park",
        "album_artist": "Linkin Park",
        "album": album,
        "title": title,
        "year": 2003,
        "duration": duration,
        "disc_number": 1,
        "track_number": number,
        "status": "missing",
        "musicbrainz_release_id": "33333333-3333-4333-8333-333333333333",
    }


def test_downloads_group_by_request_and_collapse_repeats(tmp_path, monkeypatch):
    monkeypatch.delenv("PIG_MUSIC_TEST_LEGACY_API", raising=False)
    store = Store(tmp_path)
    auth = Auth(store)
    owner = auth.create_owner("mark", "groups fixture password")
    code = auth.setup_code(owner, "member", "Member")
    member = auth.activate(code["code"], "groups fixture password", "f")["id"]
    token = auth.login(
        "member", "groups fixture password", "device", "phone", "ios", "f"
    )["token"]
    app = create_app(tmp_path)
    with TestClient(
        app,
        base_url="https://private.ts.net",
        headers={"Authorization": "Bearer " + token},
    ) as client:
        live = app.state.store
        # Grouping only: the downloader must not start these requests.
        monkeypatch.setattr(live, "claim_request", lambda: None)
        live.save_download_settings({"enabled": True, "sources": ["freemp3cloud"]})
        live.submit_catalogue(
            [song("Foreword", 1), song("Don't Stay", 2), song("Somewhere", 3)],
            user_id=member,
        )
        # The same song asked for again (slightly different tags): one row.
        live.submit_catalogue([song("Foreword", 1, duration=201)], user_id=member)
        live.submit_catalogue(
            [{**song("Numb", 1, album=""), "album_artist": ""}], user_id=member
        )
        with live.connect() as conn:
            rows = conn.execute("SELECT id,payload FROM queue").fetchall()
            for key, raw in rows:
                payload = json.loads(raw)
                if payload["title"] == "Don't Stay":
                    payload.update(source="soulseek", codec="flac")
                    conn.execute(
                        "UPDATE queue SET status='completed',payload=? WHERE id=?",
                        (json.dumps(payload), key),
                    )
                if payload["title"] == "Somewhere":
                    conn.execute(
                        "UPDATE queue SET status='failed',payload=? WHERE id=?",
                        (json.dumps({**payload, "error": "secret detail"}), key),
                    )
        # The repeat joined the first request: no second job for it.
        assert client.get("/api/v1/requests").json()["total"] == 4
        page = client.get("/api/v1/requests/groups")
        assert page.status_code == 200, page.text
        groups = page.json()["groups"]
        assert sorted(g["kind"] for g in groups) == ["album", "song"]
        album = next(g for g in groups if g["kind"] == "album")
        single = next(g for g in groups if g["kind"] == "song")
        assert album["title"] == "Meteora" and album["artist"] == "Linkin Park"
        assert album["songs"] == 3 and album["done"] == 1 and album["failed"] == 1
        assert album["state"] == "waiting"
        assert album["release_id"]
        assert 0 < album["progress"] < 1
        foreword = [i for i in album["items"] if i["title"] == "Foreword"]
        assert len(foreword) == 1 and foreword[0]["status"] == "waiting_for_sources"
        done = next(i for i in album["items"] if i["title"] == "Don't Stay")
        assert done["source"] == "soulseek" and done["quality"] == "FLAC"
        assert single["title"] == "Numb" and single["songs"] == 1
        assert "secret detail" not in page.text and "/private" not in page.text
        assert client.get("/api/v1/requests/groups?limit=1").json()["next_offset"] == 1


EDITION = "44444444-4444-4444-8444-444444444444"


def mark(store, status, **match):
    """Set the status of the queue jobs whose payload matches `match`."""
    with store.connect() as conn:
        for key, raw in conn.execute("SELECT id,payload FROM queue").fetchall():
            payload = json.loads(raw)
            if all(payload.get(k) == v for k, v in match.items()):
                conn.execute("UPDATE queue SET status=? WHERE id=?", (status, key))


def member_store(tmp_path):
    store = Store(tmp_path)
    auth = Auth(store)
    owner = auth.create_owner("mark", "groups fixture password")
    code = auth.setup_code(owner, "member", "Member")
    return store, auth.activate(code["code"], "groups fixture password", "f")["id"]


def test_distinct_editions_stay_distinct_groups(tmp_path):
    store, member = member_store(tmp_path)
    original = [song("Foreword", 1), song("Don't Stay", 2)]
    deluxe = [
        {**t, "musicbrainz_release_id": EDITION, "explicit_edition": True}
        for t in original
    ]
    store.submit_catalogue(original, user_id=member)
    store.submit_catalogue(deluxe, user_id=member)
    mark(
        store, "completed", musicbrainz_release_id=song("", 1)["musicbrainz_release_id"]
    )
    mark(store, "failed", musicbrainz_release_id=EDITION)
    groups = store.user_libraries.request_groups(member)["groups"]
    assert len(groups) == 2
    by_release = {g["release_id"]: g for g in groups}
    assert by_release[EDITION]["failed"] == 2 and by_release[EDITION]["done"] == 0
    first = by_release[song("", 1)["musicbrainz_release_id"]]
    assert first["done"] == 2 and first["failed"] == 0
    # Every job stays reachable for retry/cancel.
    assert sum(len(g["items"]) for g in groups) == 4
    assert len({g["id"] for g in groups}) == 2


def test_conflicting_recordings_are_not_collapsed(tmp_path):
    store, member = member_store(tmp_path)
    store.submit_catalogue(
        [
            {**song("Foreword", 1), "musicbrainz_recording_id": "rec-a"},
            song("Don't Stay", 2),
        ],
        user_id=member,
    )
    # Same position and title, different tags and a different recording.
    store.submit_catalogue(
        [
            {
                **song("Foreword", 1, duration=180),
                "musicbrainz_recording_id": "rec-b",
            },
            song("Don't Stay", 2, duration=201),
        ],
        user_id=member,
    )
    (album,) = store.user_libraries.request_groups(member)["groups"]
    forewords = [i for i in album["items"] if i["title"] == "Foreword"]
    assert sorted(i["musicbrainz_recording_id"] for i in forewords) == [
        "rec-a",
        "rec-b",
    ]
    # Don't Stay names no recordings: the repeat collapses.
    assert [i["title"] for i in album["items"]].count("Don't Stay") == 1


def test_a_single_song_with_album_tags_is_a_song_request(tmp_path):
    store, member = member_store(tmp_path)
    store.submit_catalogue([song("Numb", 13)], user_id=member)
    (group,) = store.user_libraries.request_groups(member)["groups"]
    assert group["kind"] == "song" and group["title"] == "Numb"


def test_partly_failed_album_is_failed_not_done(tmp_path):
    store, member = member_store(tmp_path)
    store.submit_catalogue([song("Foreword", 1), song("Don't Stay", 2)], user_id=member)
    mark(store, "completed", title="Foreword")
    mark(store, "failed", title="Don't Stay")
    (album,) = store.user_libraries.request_groups(member)["groups"]
    assert album["state"] == "failed"
    assert (album["done"], album["failed"], album["progress"]) == (1, 1, 0.5)
    # Work still running wins over the failure; all done is done.
    mark(store, "downloading", title="Don't Stay")
    assert store.user_libraries.request_groups(member)["groups"][0]["state"] == "active"
    mark(store, "completed", title="Don't Stay")
    assert store.user_libraries.request_groups(member)["groups"][0]["state"] == "done"


def test_scope_is_per_requester(tmp_path):
    store, member = member_store(tmp_path)
    with store.connect() as conn:
        owner_id = conn.execute("SELECT id FROM users WHERE role='owner'").fetchone()[0]
    store.submit_catalogue([song("Foreword", 1), song("Don't Stay", 2)], user_id=member)
    # The owner asks for one of those songs on its own.
    store.submit_catalogue([song("Foreword", 1)], user_id=owner_id)
    (mine,) = store.user_libraries.request_groups(member)["groups"]
    (theirs,) = store.user_libraries.request_groups(owner_id)["groups"]
    assert mine["kind"] == "album" and mine["songs"] == 2
    assert theirs["kind"] == "song" and theirs["songs"] == 1
    assert "request_scope" not in json.dumps(
        store.user_libraries.queue(member)["items"]
    )
