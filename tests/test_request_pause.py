"""Pause and resume one music request (#228, D-109)."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from server.downloader import Cancelled, Downloader
from server.household_policy import HouseholdPolicy
from server.soulseek import candidate_for
from server.store import Store
from test_music_bug_fixes import client_for, seed_requests
from test_native_sync import fixture
from test_personal_libraries import household


def song(title="Song", number=1, **extra):
    return {
        "title": title,
        "artist": "Example",
        "album": "Studio",
        "album_artist": "Example",
        "year": 2020,
        "disc_number": 1,
        "track_number": number,
        "duration": 3,
        "quality_profile": "mp3_320",
        **extra,
    }


def insert(store, key, status, **payload):
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO queue(id,payload,status) VALUES (?,?,?)",
            (key, json.dumps(payload), status),
        )


def status(store, key):
    with store.connect() as conn:
        return conn.execute("SELECT status FROM queue WHERE id=?", (key,)).fetchone()[0]


def test_member_pauses_and_resumes_only_own_unfinished_requests(tmp_path, monkeypatch):
    app, owner, member, token, _ = household(tmp_path, monkeypatch)
    store = app.state.store
    seed_requests(store, owner, member)
    with client_for(app, token) as client:
        for key in ("waiting_for_sources", "searching", "downloading", "validating"):
            response = client.post(f"/api/v1/requests/{key}/pause")
            assert response.status_code == 200, response.text
            assert response.json() == {"paused": True}
            assert status(store, key) == "paused"
            # Pausing twice is a wrong state, like retrying a running request.
            assert client.post(f"/api/v1/requests/{key}/pause").status_code == 400
        # Finished requests have nothing to pause.
        for key in ("completed", "downloaded", "failed", "cancelled"):
            response = client.post(f"/api/v1/requests/{key}/pause")
            assert response.status_code == 400
            assert "still being added" in response.json()["detail"]
            assert status(store, key) == key
            response = client.post(f"/api/v1/requests/{key}/resume")
            assert response.status_code == 400
            assert "paused" in response.json()["detail"]
        for key in ("theirs-active", "missing"):
            assert client.post(f"/api/v1/requests/{key}/pause").status_code == 404
            assert client.post(f"/api/v1/requests/{key}/resume").status_code == 404
        assert status(store, "theirs-active") != "paused"
        for key in ("searching", "downloading"):
            response = client.post(f"/api/v1/requests/{key}/resume")
            assert response.status_code == 200, response.text
            assert response.json() == {"resumed": True}
            # Back to the queue: the downloader claims it as any other.
            assert status(store, key) == "waiting_for_sources"
    # Someone else's paused request: not theirs to resume.
    insert(store, "theirs-paused", "paused", requested_by=[owner])
    with client_for(app, token) as client:
        assert client.post("/api/v1/requests/theirs-paused/resume").status_code == 404
    assert status(store, "theirs-paused") == "paused"
    with TestClient(app, base_url="https://private.ts.net") as anonymous:
        assert anonymous.post("/api/v1/requests/theirs-active/pause").status_code in {
            401,
            403,
        }


def test_pausing_a_shared_request_pauses_the_one_job(tmp_path):
    store, owner, member = fixture(tmp_path)
    insert(store, "shared", "downloading", requested_by=[owner, member])
    assert store.user_libraries.pause_request(member, "shared") == {"paused": True}
    assert status(store, "shared") == "paused"
    # Importing finishes on its own: its file has passed validation.
    insert(store, "importing", "importing", requested_by=[member])
    with pytest.raises(ValueError, match="still being added"):
        store.user_libraries.pause_request(member, "importing")
    assert status(store, "importing") == "importing"
    # Either of them resumes it.
    assert store.user_libraries.resume_request(owner, "shared") == {"resumed": True}
    assert status(store, "shared") == "waiting_for_sources"


def test_nothing_claims_or_schedules_a_paused_request(tmp_path):
    store = Store(tmp_path)
    store.save_download_settings(
        {"enabled": True, "profile": "mp3_320", "sources": ["freemp3cloud"]}
    )
    insert(store, "paused", "paused", **song(), requested_by=["someone"])
    assert store.claim_request() is None
    assert store.next_retry() is None
    with store.connect() as conn:
        conn.execute("UPDATE queue SET status='waiting_for_sources'")
    assert store.claim_request()["id"] == "paused"


def test_a_worker_never_overwrites_a_pause(tmp_path):
    store, owner, member = fixture(tmp_path)
    store.save_download_settings(
        {"enabled": True, "profile": "mp3_320", "sources": ["freemp3cloud"]}
    )
    insert(store, "job", "waiting_for_sources", **song(), requested_by=[member])
    job = store.claim_request()
    token = job["claim_token"]
    store.user_libraries.pause_request(member, "job")
    # Each write a worker makes on its claim leaves the pause alone.
    store.progress("job", 10_000, 10_000, token)
    store.request_state("job", "validating", _claim_token=token)
    store.request_state("job", "failed", _claim_token=token, error="x")
    assert not store.defer_request(job, 60, 1)
    assert not store.request_active("job", token)
    assert status(store, "job") == "paused"
    # A resumed request claimed afresh is out of the old worker's reach too.
    store.user_libraries.resume_request(member, "job")
    again = store.claim_request()
    assert again["claim_token"] != token
    store.request_state("job", "failed", _claim_token=token, error="old worker")
    assert status(store, "job") == "searching"


def test_the_downloader_stops_a_paused_job_without_failing_it(tmp_path, monkeypatch):
    store, owner, member = fixture(tmp_path)
    store.save_download_settings(
        {"enabled": True, "profile": "mp3_320", "sources": ["freemp3cloud"]}
    )
    insert(store, "job", "waiting_for_sources", **song(), requested_by=[member])
    job = store.claim_request()
    worker = Downloader(store)
    monkeypatch.setattr("server.downloader.download_root", lambda *a, **k: None)

    def attempt(job, profile, errors, convert=False):
        store.user_libraries.pause_request(member, job["id"])
        worker.check(job["id"])
        pytest.fail("A paused job must stop at its next check")

    monkeypatch.setattr(worker, "attempt", attempt)
    worker.process(job)
    assert status(store, "job") == "paused"


def test_pausing_cancels_the_jobs_soulseek_transfer(tmp_path, monkeypatch):
    store, owner, member = fixture(tmp_path)
    (tmp_path / "slskd-connection.json").write_text(
        json.dumps(
            {
                "url": "http://localhost:5031",
                "api_key": "test-only",
                "download_root": str(tmp_path / "engine"),
            }
        )
    )
    store.save_download_settings(
        {"enabled": True, "profile": "mp3_320", "sources": ["freemp3cloud"]}
    )
    insert(store, "job", "waiting_for_sources", **song(), requested_by=[member])
    job = store.claim_request()
    worker = Downloader(store)
    worker.context.claim_token = job["claim_token"]
    monkeypatch.setattr("server.downloader.download_root", lambda *a, **k: None)
    monkeypatch.setattr("server.soulseek.TRANSFER_POLL", 0)
    entry = {
        "filename": "Music\\Example\\Studio\\01 Song.mp3",
        "size": 10000,
        "length": 3,
        "bitRate": 320,
    }
    polls, deletes = [], []

    def handler(request):
        if request.method == "DELETE":
            deletes.append((request.url.path, request.url.params.get("remove")))
            return httpx.Response(204)
        if request.method == "POST":
            return httpx.Response(201)
        polls.append(1)
        if len(polls) == 2:
            # The person pauses while the peer is sending.
            store.user_libraries.pause_request(member, "job")
        state = {"id": "owned-id", "stateDescription": "InProgress"}
        return httpx.Response(
            200,
            json={
                "directories": [
                    {"files": [{**entry, **state, "bytesTransferred": 2000}]}
                ]
            },
        )

    worker.soulseek.client = lambda: httpx.Client(
        base_url="http://engine/api/v0/", transport=httpx.MockTransport(handler)
    )
    candidate = {
        **candidate_for(song(), {"username": "peer"}, entry),
        "source": "soulseek",
    }
    with pytest.raises(Cancelled):
        worker.transfer("job", candidate, tmp_path / "staged.mp3")
    # The engine's transfer is cancelled; its partial file is kept (#96).
    assert deletes == [("/api/v0/transfers/downloads/peer/owned-id", "false")]
    assert status(store, "job") == "paused"


def test_a_paused_request_stays_paused_across_a_restart(tmp_path):
    store, owner, member = fixture(tmp_path)
    insert(store, "paused", "paused", **song(), requested_by=[member])
    insert(store, "running", "downloading", **song("Other", 2), requested_by=[member])
    store.recover_requests()
    assert status(store, "paused") == "paused"
    assert status(store, "running") == "waiting_for_sources"
    reopened = Store(tmp_path)
    reopened.recover_requests()
    assert status(reopened, "paused") == "paused"


def test_clear_finished_keeps_paused_requests(tmp_path):
    store, owner, member = fixture(tmp_path)
    insert(store, "paused", "paused", **song(), requested_by=[member])
    insert(store, "done", "completed", **song("Done", 2), requested_by=[member])
    assert store.user_libraries.clear_requests(member) == {"cleared": 1}
    store.clear_queue(finished_only=True)
    assert status(store, "paused") == "paused"
    assert store.user_libraries.queue(member)["total"] == 1


def test_groups_count_a_paused_song_as_neither_active_nor_failed(tmp_path):
    store, owner, member = fixture(tmp_path)
    album = {"request_scope": {member: ["album", "example", "studio", ""]}}
    insert(store, "a1", "paused", **song("One", 1), **album, requested_by=[member])
    insert(
        store,
        "a2",
        "completed",
        **song("Two", 2),
        **album,
        requested_by=[member],
    )
    insert(store, "lone", "paused", **song("Lone", 1, album=""), requested_by=[member])
    page = store.user_libraries.request_groups(member)
    assert page["active"] == 0
    groups = {g["title"]: g for g in page["groups"]}
    studio = groups["Studio"]
    assert studio["state"] == "paused"
    assert (studio["active"], studio["waiting"], studio["failed"]) == (0, 0, 0)
    assert studio["paused"] == 1 and studio["done"] == 1
    assert studio["progress"] == 0.5
    lone = groups["Lone"]
    assert lone["state"] == "paused" and lone["paused"] == 1
    assert lone["items"][0]["status"] == "paused"
    # Still wanted: the song shows as requested, without download progress.
    assert lone["items"][0]["server_state"] == "requested"
    assert lone["items"][0]["request_progress"] is None
    # A failure outranks a pause: the album's mark then retries (D-110).
    insert(store, "a3", "failed", **song("Three", 3), **album, requested_by=[member])
    page = store.user_libraries.request_groups(member)
    studio = next(g for g in page["groups"] if g["title"] == "Studio")
    assert studio["state"] == "failed" and studio["paused"] == 1
    # Unfinished work, paused too, stays above finished requests.
    assert page["groups"][-1]["title"] == "Studio"


def test_a_paused_request_takes_a_new_quality_target_and_stays_paused(tmp_path):
    store, owner, member = fixture(tmp_path)
    insert(
        store,
        "job",
        "paused",
        **song(quality_profile="aac_256"),
        requested_by=[member],
    )
    with store.connect() as conn:
        HouseholdPolicy(store).reconcile_pending(conn)
    assert status(store, "job") == "paused"
    with store.connect() as conn:
        payload = json.loads(
            conn.execute("SELECT payload FROM queue WHERE id='job'").fetchone()[0]
        )
    assert payload["quality_profile"] != "aac_256"


def test_the_download_log_says_paused(tmp_path):
    store, owner, member = fixture(tmp_path)
    insert(store, "job", "paused", **song(), requested_by=[member])
    lines = store.user_libraries.request_log(member, "job")["lines"]
    assert lines[-1] == {"text": "Paused", "ok": True}


def test_asking_again_resumes_a_paused_request_but_automatic_paths_do_not(
    tmp_path,
):
    store, owner, member = fixture(tmp_path)
    wanted = {**song("Wanted"), "status": "missing"}
    store.user_libraries.request(member, [wanted])
    with store.connect() as conn:
        (key,) = conn.execute("SELECT id FROM queue").fetchone()
    store.user_libraries.pause_request(member, key)
    # Artist monitoring (automatic) and policy changes leave the pause alone.
    store.submit_catalogue([wanted], user_id=owner)
    with store.connect() as conn:
        HouseholdPolicy(store).reconcile_pending(conn)
    assert status(store, key) == "paused"
    # Another person asking for the song now resumes it (#228).
    result = store.user_libraries.request(owner, [wanted])
    assert result["existing"] == 1
    assert status(store, key) == "waiting_for_sources"


def test_an_add_leaves_a_request_the_person_paused_paused(tmp_path):
    """#264: an ordinary Add (any page, any edition, a playlist's missing
    songs) never resumes the person's own paused request; Resume in
    Downloads does. Another member's Add still resumes it (#228)."""
    store, owner, member = fixture(tmp_path)
    users = store.user_libraries
    deluxe = {
        **song("Wanted"),
        "musicbrainz_release_id": "deluxe",
        "status": "missing",
    }
    users.request(member, [{**deluxe, "explicit_edition": True}])
    with store.connect() as conn:
        (key,) = conn.execute("SELECT id FROM queue").fetchone()
    users.pause_request(member, key)
    # The same song from the edition the album opens on.
    plain = {**deluxe, "musicbrainz_release_id": "plain", "track_number": 4}
    result = users.request(member, [plain])
    assert (result["created"], result["existing"], result["paused"]) == (0, 0, 1)
    assert status(store, key) == "paused"
    # A playlist's missing songs go through the same path.
    _, counts = users.add_apple_playlist(
        member,
        "pl.paused",
        "Paused",
        None,
        [{"title": "Wanted", "artist": "Example", "album": "Studio", "duration": 3}],
        True,
    )
    assert counts["requested"] == 0
    assert status(store, key) == "paused"
    assert len(store.queue()["items"]) == 1
    # Resume in Downloads resumes it.
    users.resume_request(member, key)
    assert status(store, key) == "waiting_for_sources"
    # Paused again: another member's Add still resumes it (#228).
    users.pause_request(member, key)
    assert users.request(owner, [plain])["existing"] == 1
    assert status(store, key) == "waiting_for_sources"


def test_withdrawing_from_a_shared_pause_leaves_the_song_addable(tmp_path):
    """Codex round 1 on #264: the pauser of a shared request withdraws.
    The pause is then nobody's own: it stays paused for the other person,
    and the pauser's next Add rejoins (and resumes) it like anyone's."""
    store, owner, member = fixture(tmp_path)
    users = store.user_libraries
    wanted = {**song("Shared"), "status": "missing"}
    users.request(member, [wanted])
    with store.connect() as conn:
        (key,) = conn.execute("SELECT id FROM queue").fetchone()
    users.request(owner, [wanted])
    users.pause_request(member, key)
    assert users.cancel_request(member, key)["continues_for_others"]
    assert status(store, key) == "paused"
    result = users.request(member, [wanted])
    assert (result["existing"], result["paused"]) == (1, 0)
    assert status(store, key) == "waiting_for_sources"
    # Theirs again: pause and Resume in Downloads both work for them.
    users.pause_request(member, key)
    assert users.resume_request(member, key) == {"resumed": True}
    assert status(store, key) == "waiting_for_sources"


def test_withdrawing_from_an_older_shared_pause_does_not_pass_it_on(tmp_path):
    """Codex round 2 on #264: a pause from before paused_by was recorded is
    its first requester's. When they withdraw, it becomes nobody's own, not
    the next requester's, so that person's Add still resumes it."""
    store, owner, member = fixture(tmp_path)
    users = store.user_libraries
    wanted = {**song("Older"), "status": "missing"}
    users.request(member, [wanted])
    users.request(owner, [wanted])
    with store.connect() as conn:
        (key,) = conn.execute("SELECT id FROM queue").fetchone()
        # Paused before the pauser was recorded.
        conn.execute("UPDATE queue SET status='paused' WHERE id=?", (key,))
    users.cancel_request(member, key)
    assert status(store, key) == "paused"
    result = users.request(owner, [wanted])
    assert (result["existing"], result["paused"]) == (1, 0)
    assert status(store, key) == "waiting_for_sources"


def test_an_add_says_what_happened_to_each_song_in_order(tmp_path):
    """Codex round 1 on batch K: a batch's totals can't say which song was
    taken, and the iPhone asking again song by song recreated a request
    cancelled meanwhile. The answer names each song's outcome, in order."""
    store, owner, member = fixture(tmp_path)
    users = store.user_libraries
    household = {
        "title": "Song 0",
        "artist": "Artist",
        "album_artist": "Artist",
        "album": "Album",
        "duration": 100,
    }
    kept = {**song("Kept"), "status": "missing"}
    users.request(member, [kept])
    with store.connect() as conn:
        (key,) = conn.execute("SELECT id FROM queue").fetchone()
        conn.execute(
            "INSERT INTO documents VALUES (?,?)",
            ("excluded:fixture", json.dumps({"track": song("Unwanted")})),
        )
    users.pause_request(member, key)
    new = {**song("New", 2), "status": "missing"}
    result = store.submit_catalogue(
        [
            new,
            {**household, "status": "downloaded"},
            {**song("Unwanted"), "status": "missing"},
            {**kept},
            {**new},
            {**household, "status": "downloaded"},
        ],
        user_id=member,
        resume_paused=True,
    )
    assert result["outcomes"] == [
        "created",
        "added",
        "excluded",
        "paused",
        "existing",
        "in_library",
    ]
    assert (result["created"], result["existing"], result["skipped"]) == (1, 2, 2)
    assert result["paused"] == 1 and result["paused_ids"] == [key]


def test_a_song_gone_from_the_library_since_it_was_matched_is_asked_for(
    tmp_path, monkeypatch
):
    """Codex round 2 on batch K: the person's Add matches songs to the
    household library first, then submits. A library refresh in between
    that loses the copy left the song neither added nor queued, answered
    `in_library` (and counted skipped before that). It is asked for."""
    store, owner, member = fixture(tmp_path)
    users = store.user_libraries
    household = {
        **song("Song 0"),
        "artist": "Artist",
        "album_artist": "Artist",
        "album": "Album",
        "duration": 100,
    }
    from server.matching import Match

    real = store.index.match
    calls = []

    def changing(track):
        calls.append(track["title"])
        # The refresh: the household copy is gone by the second look.
        return real(track) if len(calls) == 1 else Match("absent")

    monkeypatch.setattr(store.index, "match", changing)
    result = users.request(member, [household])
    assert len(calls) == 2
    assert result["outcomes"] == ["created"]
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM queue").fetchone()[0] == 1
        assert not conn.execute(
            "SELECT 1 FROM library_members WHERE user_id=?", (member,)
        ).fetchone()


def test_an_add_left_paused_names_the_paused_request(tmp_path, monkeypatch):
    """#276: the iPhone clears a song's Paused mark when the request it was
    left paused under resumes, so the answer names that request: here one
    from another edition (Greatest Hits), which the added song's own title,
    artist and album don't identify."""
    app, owner, member, token, _ = household(tmp_path, monkeypatch)
    store = app.state.store
    hits = {
        "title": "Wanted",
        "artist": "Example",
        "album": "Greatest Hits",
        "album_artist": "Example",
        "duration": 3,
        "musicbrainz_release_id": "11111111-1111-4111-8111-111111111111",
    }
    with client_for(app, token) as client:
        first = client.post("/api/v1/requests", json={"tracks": [hits]})
        assert first.status_code == 200, first.text
        assert first.json()["paused_ids"] == []
        with store.connect() as conn:
            (key,) = conn.execute("SELECT id FROM queue").fetchone()
        assert client.post(f"/api/v1/requests/{key}/pause").status_code == 200
        studio = {
            **hits,
            "album": "Studio",
            "musicbrainz_release_id": "22222222-2222-4222-8222-222222222222",
        }
        again = client.post("/api/v1/requests", json={"tracks": [studio]})
    assert again.status_code == 200, again.text
    assert again.json()["paused"] == 1
    assert again.json()["paused_ids"] == [key]
    assert first.json()["outcomes"] == ["created"]
    assert again.json()["outcomes"] == ["paused"]
    assert status(store, key) == "paused"
