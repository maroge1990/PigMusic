"""How much a person may queue (D-128, #242).

Per person up to 2,000 songs waiting and 1,000 new songs in any 10 minutes;
the household 10,000 waiting; everyone included, the owner too. Past a
limit the songs that fit are queued, in order, and the rest are left out
(`excluded` in outcomes, their positions in `over_limit`); nothing already
asked for is lost, and asking again for a song already queued or paused
never counts.
"""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from server import queue_limits
from server.queue_limits import QueueLimits
from server.store import Store
from test_request_matrix import Household, release, track


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def song(n, album=1):
    return {**track(n, number=n), "album": f"Limit Album {album}"}


def missing(n, album=1):
    return {**song(n, album), "status": "missing"}


def limited(store, user, songs):
    """The person's own Add (UserLibrary.request), as the route makes it."""
    return store.user_libraries.request(user, songs)


def marks(result):
    """Outcomes with the songs a limit left out shown as over_limit; each
    is `excluded` in the answer itself, which every client can read."""
    outcomes = list(result["outcomes"])
    for position in result["over_limit"]:
        assert outcomes[position] == "excluded", result
        outcomes[position] = "over_limit"
    return outcomes


def waiting(store, user=None):
    with store.connect() as conn:
        return conn.execute(queue_limits.WAITING_COUNT, (user or "",)).fetchone()


def queued_titles(store):
    with store.connect() as conn:
        return sorted(
            json.loads(p)["title"] for (p,) in conn.execute("SELECT payload FROM queue")
        )


def small(store, person=3, household=100, songs=100, seconds=600, clock=None):
    store.queue_limits = QueueLimits(
        person=person,
        household=household,
        songs=songs,
        seconds=seconds,
        **({"clock": clock} if clock else {}),
    )
    return store.queue_limits


def test_the_decided_numbers():
    limits = QueueLimits()
    assert (limits.person, limits.household) == (2000, 10000)
    assert (limits.songs, limits.seconds) == (1000, 600)


def test_past_the_persons_cap_the_songs_that_fit_are_queued_in_order(tmp_path):
    store = Store(tmp_path)
    small(store, person=3)
    result = limited(store, "alice", [song(n) for n in range(1, 6)])
    assert marks(result) == ["created"] * 3 + ["over_limit"] * 2
    assert (result["created"], result["left_out"]) == (3, 2)
    assert (result["limit"], result["retry_after"]) == ("person_waiting", None)
    assert queued_titles(store) == [f"Matrix Song {n}" for n in (1, 2, 3)]
    # Nothing lost: asking again for the batch changes nothing and loses
    # nothing; once a job finishes there is room for the next song.
    again = limited(store, "alice", [song(n) for n in range(1, 6)])
    assert marks(again) == ["existing"] * 3 + ["over_limit"] * 2
    with store.connect() as conn:
        conn.execute(
            "UPDATE queue SET status='completed' WHERE json_extract(payload,'$.title')=?",
            ("Matrix Song 1",),
        )
    later = limited(store, "alice", [song(n) for n in range(2, 6)])
    assert marks(later) == ["existing", "existing", "created", "over_limit"]
    assert later["left_out"] == 1


def test_asking_again_for_queued_or_paused_songs_never_counts(tmp_path):
    store = Store(tmp_path)
    small(store, person=2, songs=2)
    assert limited(store, "alice", [song(1), song(2)])["created"] == 2
    with store.connect() as conn:
        conn.execute(
            "UPDATE queue SET status='paused',payload=json_set(payload,"
            "'$.paused_by','alice') WHERE json_extract(payload,'$.title')=?",
            ("Matrix Song 2",),
        )
    # At the cap and with the rate spent, both again: nothing left out, and
    # a lost answer's retry neither double-counts nor loses a song.
    for _ in range(2):
        result = limited(store, "alice", [song(1), song(2)])
        assert marks(result) == ["existing", "paused"]
        assert (result["left_out"], result["limit"]) == (0, None)
    assert waiting(store, "alice") == (2, 2)


@pytest.mark.parametrize("shared_first", [True, False])
def test_joining_another_persons_job_costs_a_waiting_slot(tmp_path, shared_first):
    """Codex round 1: joining made the job wait for one more person without
    asking whether they had room. A join costs a personal slot and a song of
    the rate, never a household slot; in either order only one of the two
    songs fits a cap of one."""
    store = Store(tmp_path)
    small(store, person=1, household=2)
    assert limited(store, "bob", [song(1)])["created"] == 1
    songs = [song(1), song(2)] if shared_first else [song(2), song(1)]
    result = limited(store, "alice", songs)
    assert marks(result) == (
        ["existing", "over_limit"] if shared_first else ["created", "over_limit"]
    )
    assert result["limit"] == "person_waiting"
    assert waiting(store, "alice")[1] == 1
    # Alice wasn't added to the job she was left out of.
    with store.connect() as conn:
        requesters = [
            json.loads(p)["requested_by"]
            for (p,) in conn.execute("SELECT payload FROM queue")
        ]
    assert sorted(map(len, requesters)) == ([2] if shared_first else [1, 1])
    # A repeat by someone already on the job is free, at the cap.
    assert marks(limited(store, "bob", [song(1)])) == ["existing"]


def test_a_join_takes_no_household_slot(tmp_path):
    store = Store(tmp_path)
    small(store, person=5, household=1)
    limited(store, "bob", [song(1)])
    assert marks(limited(store, "alice", [song(1), song(2)])) == [
        "existing",
        "over_limit",
    ]


def test_the_household_ceiling_holds_across_two_people(tmp_path):
    store = Store(tmp_path)
    small(store, person=10, household=4)
    assert limited(store, "alice", [song(n) for n in (1, 2, 3)])["created"] == 3
    result = limited(store, "bob", [song(n, 2) for n in (4, 5, 6)])
    assert marks(result) == ["created", "over_limit", "over_limit"]
    assert result["limit"] == "household_waiting"
    assert waiting(store)[0] == 4


def test_cancelled_and_finished_jobs_free_room(tmp_path):
    store = Store(tmp_path)
    small(store, person=2)
    limited(store, "alice", [song(1), song(2)])
    assert marks(limited(store, "alice", [song(3)])) == ["over_limit"]
    with store.connect() as conn:
        conn.execute(
            "UPDATE queue SET status='cancelled' WHERE json_extract(payload,'$.title')=?",
            ("Matrix Song 1",),
        )
    assert marks(limited(store, "alice", [song(3)])) == ["created"]


def test_a_paused_job_holds_its_place_so_resuming_never_passes_the_cap(tmp_path):
    store = Store(tmp_path)
    small(store, person=2)
    limited(store, "alice", [song(1), song(2)])
    with store.connect() as conn:
        conn.execute("UPDATE queue SET status='paused'")
    assert marks(limited(store, "alice", [song(3)])) == ["over_limit"]


def test_the_rate_is_a_rolling_window_per_person(tmp_path):
    store = Store(tmp_path)
    clock = Clock()
    small(store, person=100, songs=4, seconds=60, clock=clock)
    clock.now += 10
    assert limited(store, "alice", [song(1)])["created"] == 1
    clock.now += 20
    result = limited(store, "alice", [song(n) for n in range(2, 7)])
    assert marks(result) == ["created"] * 3 + ["over_limit"] * 2
    # One song is free again when the first admission (20 s ago) is a
    # minute old.
    assert (result["limit"], result["retry_after"]) == ("rate", 40)
    # Bob has his own window.
    assert limited(store, "bob", [song(n) for n in range(11, 15)])["created"] == 4
    clock.now += 39
    assert marks(limited(store, "alice", [song(5)])) == ["over_limit"]
    clock.now += 1
    answer = limited(store, "alice", [song(5), song(6)])
    assert marks(answer) == ["created", "over_limit"]
    # The next three were admitted 40 s ago.
    assert answer["retry_after"] == 20
    clock.now += 20
    assert marks(limited(store, "alice", [song(6)])) == ["created"]
    clock.now += 6000
    assert store.queue_limits.available("alice") == 4
    # Pruned on use: nothing kept for her past the window.
    assert "alice" not in store.queue_limits.admitted


def test_a_window_never_admits_more_than_the_rate(tmp_path):
    """Codex round 1: the bucket admitted 1,000 songs at 0 s and 998 more at
    599 s. The window admits none until the first leave it."""
    clock = Clock()
    limits = QueueLimits(clock=clock)
    limits.take("alice", 1000)
    clock.now += 599
    assert limits.available("alice") == 0
    assert limits.wait("alice") == 1
    clock.now += 0.5
    assert limits.wait("alice") == 1
    clock.now += 0.5
    assert limits.available("alice") == 1000


def test_any_ten_minutes_hold_at_most_a_thousand(tmp_path):
    """Random arrivals against the real limits: in every window starting at
    an admission, at most 1,000 songs; and nothing refused had room."""
    import random

    chance = random.Random(242)
    for _ in range(20):
        clock = Clock()
        limits = QueueLimits(clock=clock)
        log = []
        for _ in range(300):
            clock.now += chance.choice([0, 0.5, 1, 7, 30, 120, 599, 600, 601])
            asked = chance.randint(1, 300)
            free = limits.available("alice")
            given = min(asked, free)
            limits.take("alice", given)
            if given:
                log.append((clock.now, given))
            if given < asked:
                assert limits.available("alice") == 0
                # Room comes back when Retry-After says, not a second sooner.
                wait = limits.wait("alice")
                clock.now += wait - 1
                assert limits.available("alice") == 0
                clock.now += 1
                assert limits.available("alice") >= 1
        for start, _ in log:
            assert sum(n for at, n in log if start <= at < start + 600) <= 1000


def test_a_restart_forgets_the_window_but_not_the_waiting_caps(tmp_path):
    store = Store(tmp_path)
    small(store, person=3, songs=3)
    limited(store, "alice", [song(1), song(2), song(3)])
    restarted = Store(tmp_path)
    small(restarted, person=3, songs=3)
    assert restarted.queue_limits.available("alice") == 3
    assert marks(limited(restarted, "alice", [song(4)])) == ["over_limit"]


def test_two_threads_never_spend_the_same_rate(tmp_path):
    store = Store(tmp_path)
    small(store, person=1000, songs=10)

    def ask(i):
        return limited(store, "alice", [song(i * 10 + k) for k in range(4)])

    with ThreadPoolExecutor(max_workers=6) as pool:
        answers = list(pool.map(ask, range(1, 7)))
    assert sum(a["created"] for a in answers) == 10
    assert store.queue_limits.available("alice") == 0


def test_a_failed_request_takes_nothing_from_the_rate(tmp_path, monkeypatch):
    store = Store(tmp_path)
    small(store, person=100, songs=5)
    # A song that fails after the first job was inserted: nothing commits.
    with pytest.raises(RuntimeError):
        original = store.quality_policy
        calls = []

        def failing(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("boom")
            return original(*args, **kwargs)

        monkeypatch.setattr(store, "quality_policy", failing)
        limited(store, "alice", [song(1), song(2)])
    monkeypatch.undo()
    assert queued_titles(store) == []
    assert store.queue_limits.available("alice") == 5


def test_automatic_paths_are_not_limited(tmp_path):
    # Artist monitoring calls submit_catalogue directly, not as a person.
    store = Store(tmp_path)
    small(store, person=1, household=1, songs=1)
    result = store.submit_catalogue([missing(1), missing(2)], user_id="owner")
    assert result["created"] == 2 and result["left_out"] == 0


def test_two_devices_at_the_cap_boundary_take_the_last_room_once(tmp_path):
    store = Store(tmp_path)
    small(store, person=5, songs=1000)
    limited(store, "alice", [song(n) for n in range(1, 5)])

    def ask(i):
        return limited(store, "alice", [song(i * 10), song(i * 10 + 1)])

    with ThreadPoolExecutor(max_workers=8) as pool:
        answers = list(pool.map(ask, range(2, 10)))
    assert sum(a["created"] for a in answers) == 1
    assert sum(a["left_out"] for a in answers) == 15
    assert waiting(store, "alice") == (5, 5)


def test_a_500_song_add_and_a_200_song_import_pass_unchanged(tmp_path, monkeypatch):
    place = Household(tmp_path, monkeypatch)
    with place.client:
        answer = place.client.post(
            "/api/v1/requests",
            json={"tracks": [song(n, 1) for n in range(1, 501)]},
        )
        assert answer.status_code == 200, answer.text
        assert answer.json()["created"] == 500
        assert answer.json()["left_out"] == 0
        assert answer.json()["limit"] is None
        member = place.clients[place.member]
        body = {
            "apple_playlist_id": "pl.limits",
            "name": "Two hundred",
            "songs": [
                {"title": f"Import {n}", "artist": "Import Artist"} for n in range(200)
            ],
            "download_missing": True,
        }
        imported = member.post("/api/v1/imports/apple-music", json=body)
        assert imported.status_code == 200, imported.text
        assert imported.json()["counts"]["requested"] == 200
        assert imported.json()["counts"]["left_out"] == 0


def test_the_owner_is_limited_too_and_the_api_says_so(tmp_path, monkeypatch):
    place = Household(tmp_path, monkeypatch)
    small(place.store, person=2, songs=1000)
    with place.client:
        answer = place.client.post(
            "/api/v1/requests", json={"tracks": [song(n) for n in (1, 2, 3)]}
        )
        assert answer.status_code == 200, answer.text
        body = answer.json()
        assert marks(body) == ["created", "created", "over_limit"]
        assert (body["left_out"], body["limit"]) == (1, "person_waiting")
        # One song, left out: a busy answer the iPhone keeps, which still
        # says what happened. No Retry-After: room comes as songs finish.
        alone = place.client.post("/api/v1/requests", json={"tracks": [song(3)]})
        assert alone.status_code == 429, alone.text
        assert alone.json()["code"] == "queue_limit"
        assert marks(alone.json()) == ["over_limit"]
        assert alone.json()["left_out"] == 1
        assert "retry-after" not in alone.headers
        # The member's own cap is untouched by the owner's.
        member = place.clients[place.member]
        assert (
            member.post("/api/v1/requests", json={"tracks": [song(4)]}).json()[
                "created"
            ]
            == 1
        )


def test_past_the_rate_the_busy_answer_says_when(tmp_path, monkeypatch):
    place = Household(tmp_path, monkeypatch)
    clock = Clock()
    small(place.store, person=100, songs=2, seconds=600, clock=clock)
    with place.client:
        place.client.post("/api/v1/requests", json={"tracks": [song(1), song(2)]})
        clock.now += 100
        answer = place.client.post("/api/v1/requests", json={"tracks": [song(3)]})
        assert answer.status_code == 429, answer.text
        # When those two leave the window.
        assert answer.headers["retry-after"] == "500"
        assert (answer.json()["limit"], answer.json()["retry_after"]) == ("rate", 500)


def test_a_full_import_past_the_cap_says_how_many_were_left_out(tmp_path, monkeypatch):
    place = Household(tmp_path, monkeypatch)
    small(place.store, person=1)
    with place.client:
        body = {
            "apple_playlist_id": "pl.limits",
            "name": "Over",
            "songs": [{"title": f"Over {n}", "artist": "Over"} for n in range(3)],
            "download_missing": True,
        }
        answer = place.client.post("/api/v1/imports/apple-music", json=body)
        assert answer.status_code == 200, answer.text
        assert answer.json()["counts"]["requested"] == 1
        assert answer.json()["counts"]["left_out"] == 2


def fill(store, count, people=("alice", "bob", "carol")):
    """`count` unfinished jobs and as many finished ones, the requester
    taking turns."""
    rows = []
    for n in range(count * 2):
        user = people[n % len(people)]
        payload = {
            **missing(n),
            "song_key": ["fill", str(n)],
            "requested_by": [user],
            "request_scope": {user: "song"},
            "quality_profile": "lossless",
        }
        status = "waiting_for_sources" if n < count else "completed"
        rows.append((f"fill-{n}", json.dumps(payload), status))
    with store.connect() as conn:
        conn.executemany("INSERT INTO queue(id,payload,status) VALUES (?,?,?)", rows)


def test_the_waiting_count_runs_once_per_request(tmp_path, monkeypatch):
    store = Store(tmp_path)
    counts = []
    count = queue_limits.Room._count

    def counted(self):
        counts.append(1)
        return count(self)

    monkeypatch.setattr(queue_limits.Room, "_count", counted)
    limited(store, "alice", [song(n) for n in range(1, 51)])
    assert len(counts) == 1
    # Only songs already queued: nothing to count.
    limited(store, "alice", [song(n) for n in range(1, 51)])
    assert len(counts) == 1


@pytest.mark.loop_timing
def test_the_limits_do_not_slow_a_500_song_request(tmp_path):
    """At the household ceiling (10,000 waiting, as many finished), a
    500-song Add with the limits takes about as long as without them."""
    took = {}
    for name in ("unlimited", "limited"):
        store = Store(tmp_path / name)
        store.queue_limits = QueueLimits(person=20000, household=20000)
        fill(store, 10000)
        songs = [missing(n, 7) for n in range(500)]
        started = time.perf_counter()
        result = store.submit_catalogue(
            songs, user_id="alice", resume_paused=True, limited=name == "limited"
        )
        took[name] = time.perf_counter() - started
        assert result["created"] == 500
    assert took["limited"] < took["unlimited"] * 1.2 + 0.05, took
    started = time.perf_counter()
    assert waiting(store, "alice")[0] == 10500
    assert time.perf_counter() - started < 0.1


# Retry in Downloads (Codex round 1): queuing a failed or cancelled job
# again makes it wait; only with room, and otherwise it stays as it was.


def job(place, n):
    return place.jobs(song(n))[0][0]


def status_of(place, key):
    with place.store.connect() as conn:
        return conn.execute("SELECT status FROM queue WHERE id=?", (key,)).fetchone()[0]


def test_retry_in_downloads_keeps_the_waiting_limits(tmp_path, monkeypatch):
    place = Household(tmp_path, monkeypatch)
    small(place.store, person=2, household=100, songs=1000)
    member = place.clients[place.member]
    with place.client:
        member.post("/api/v1/requests", json={"tracks": [song(1)]})
        failed = job(place, 1)
        place.set_status(failed, "failed")
        member.post("/api/v1/requests", json={"tracks": [song(2), song(3)]})
        answer = member.post(f"/api/v1/requests/{failed}/retry")
        assert answer.status_code == 429, answer.text
        assert answer.json()["code"] == "queue_limit"
        assert answer.json()["limit"] == "person_waiting"
        assert status_of(place, failed) == "failed"
        # The owner's retry of it from the queue is refused too: it would
        # wait for the member.
        admin = place.client.post(f"/api/v1/admin/queue/{failed}/retry")
        assert admin.status_code == 429, admin.text
        assert status_of(place, failed) == "failed"
        place.set_status(job(place, 2), "cancelled")
        assert member.post(f"/api/v1/requests/{failed}/retry").json() == {
            "retried": True
        }
        assert status_of(place, failed) == "waiting_for_sources"


def test_the_admin_retry_keeps_the_household_ceiling(tmp_path, monkeypatch):
    place = Household(tmp_path, monkeypatch)
    small(place.store, person=100, household=2, songs=1000)
    with place.client:
        place.client.post("/api/v1/requests", json={"tracks": [song(1)]})
        failed = job(place, 1)
        place.set_status(failed, "cancelled")
        place.clients[place.member].post(
            "/api/v1/requests", json={"tracks": [song(2), song(3)]}
        )
        answer = place.client.post(f"/api/v1/admin/queue/{failed}/retry")
        assert answer.status_code == 429, answer.text
        assert answer.json()["limit"] == "household_waiting"
        assert status_of(place, failed) == "cancelled"


def test_a_retry_that_joins_another_job_needs_the_persons_room(tmp_path, monkeypatch):
    # #243: Retry joins an unfinished job for the same song instead of
    # queuing a twin; joining makes it wait for this person too.
    place = Household(tmp_path, monkeypatch)
    small(place.store, person=1, household=100, songs=1000)
    member = place.clients[place.member]
    with place.client:
        # Asked for under a release, failed, then asked for with none: a
        # new job beside the failed one.
        named = {**song(1), "musicbrainz_release_id": release(1, 1)}
        member.post("/api/v1/requests", json={"tracks": [named]})
        failed = job(place, 1)
        place.set_status(failed, "failed")
        place.client.post("/api/v1/requests", json={"tracks": [song(1)]})
        assert len(place.jobs(song(1))) == 2
        member.post("/api/v1/requests", json={"tracks": [song(2)]})
        answer = member.post(f"/api/v1/requests/{failed}/retry")
        assert answer.status_code == 429, answer.text
        for key, state, payload in place.jobs(song(1)):
            if key != failed:
                assert place.member not in payload["requested_by"]
        assert status_of(place, failed) == "failed"


def test_resuming_a_paused_request_is_free_at_the_cap(tmp_path, monkeypatch):
    place = Household(tmp_path, monkeypatch)
    small(place.store, person=1, household=1, songs=1)
    member = place.clients[place.member]
    with place.client:
        member.post("/api/v1/requests", json={"tracks": [song(1)]})
        key = job(place, 1)
        assert member.post(f"/api/v1/requests/{key}/pause").status_code == 200
        assert member.post(f"/api/v1/requests/{key}/resume").json() == {"resumed": True}


# An answer with songs left out stays readable by installed iPhones
# (Codex round 1): their generated client knows only the outcomes of the
# schema they were built with (api/openapi.v1.json at 508a8ec).

OLD = json.loads(
    (Path(__file__).parent / "fixtures" / "acquisition_result_508a8ec.json").read_text()
)


def conforms(value, schema):
    """The parts of JSON Schema the generated Swift client checks: types,
    enums, required properties, array items."""
    if "$ref" in schema:
        return conforms(value, OLD[schema["$ref"].rsplit("/", 1)[1]])
    if "anyOf" in schema:
        return any(conforms(value, option) for option in schema["anyOf"])
    kind = schema.get("type")
    checks = {
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "string": lambda v: isinstance(v, str),
        "array": lambda v: isinstance(v, list),
        "object": lambda v: isinstance(v, dict),
        "null": lambda v: v is None,
    }
    if kind and not checks[kind](value):
        return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    if kind == "array":
        return all(conforms(v, schema.get("items", {})) for v in value)
    if kind == "object":
        if any(name not in value for name in schema.get("required", [])):
            return False
        return all(
            conforms(value[name], part)
            for name, part in schema.get("properties", {}).items()
            if name in value
        )
    return True


def test_a_partial_answer_fits_the_schema_installed_iphones_have(tmp_path, monkeypatch):
    place = Household(tmp_path, monkeypatch)
    small(place.store, person=1)
    old = OLD["AcquisitionResultDTO"]
    # What the first build of this change sent would not have fitted.
    first = {"created": 1, "existing": 0, "skipped": 0, "downloads_enabled": True}
    assert not conforms({**first, "outcomes": ["created", "over_limit"]}, old)
    with place.client:
        answer = place.client.post(
            "/api/v1/requests", json={"tracks": [song(1), song(2)]}
        )
        assert answer.status_code == 200
        body = answer.json()
        assert body["outcomes"] == ["created", "excluded"]
        assert body["over_limit"] == [1]
        assert conforms(body, old), body
        busy = place.client.post("/api/v1/requests", json={"tracks": [song(3)]})
        assert busy.status_code == 429
        assert conforms(busy.json(), OLD["APIError"])
        assert busy.json()["over_limit"] == [0]


CURRENT = json.loads(
    (Path(__file__).parents[1] / "api" / "openapi.v1.json").read_text()
)["components"]["schemas"]


def test_every_queue_limit_answer_fits_the_current_error_schema(tmp_path, monkeypatch):
    """Codex round 2: a waiting limit's 429 sent retry_after null, which the
    contract's integer doesn't allow. It is left out when no wait applies."""
    place = Household(tmp_path, monkeypatch)
    error = CURRENT["APIError"]
    assert error["properties"]["retry_after"] == {"type": "integer"}
    small(place.store, person=1, songs=1000)
    member = place.clients[place.member]
    with place.client:
        member.post("/api/v1/requests", json={"tracks": [song(1)]})
        waiting_add = member.post("/api/v1/requests", json={"tracks": [song(2)]})
        place.set_status(job(place, 1), "failed")
        member.post("/api/v1/requests", json={"tracks": [song(3)]})
        waiting_retry = member.post(f"/api/v1/requests/{job(place, 1)}/retry")
        admin_retry = place.client.post(f"/api/v1/admin/queue/{job(place, 1)}/retry")
        small(place.store, person=100, songs=1, clock=Clock())
        place.client.post("/api/v1/requests", json={"tracks": [song(4)]})
        rate_add = place.client.post("/api/v1/requests", json={"tracks": [song(5)]})
    for answer in (waiting_add, waiting_retry, admin_retry, rate_add):
        assert answer.status_code == 429, answer.text
        body = answer.json()
        assert conforms(body, error), body
        assert conforms(body, OLD["APIError"]), body
    assert "retry_after" not in waiting_add.json()
    assert "retry_after" not in waiting_retry.json()
    assert "retry_after" not in admin_retry.json()
    assert rate_add.json()["retry_after"] == 600


# The jobs the server makes on its own are exempt (coordinator's decision on
# Codex's confirmation round): with every limit at nothing they queue
# exactly what they queue without limits, as on origin/main. They are still
# counted as waiting for the people on them (see queue_limits).


def zero(store):
    store.queue_limits = QueueLimits(person=0, household=0, songs=0)


def shape(store):
    with store.connect() as conn:
        rows = conn.execute("SELECT status,payload FROM queue").fetchall()
    return sorted(
        (
            status,
            json.loads(p).get("library_track_id") or json.loads(p).get("title"),
            json.loads(p).get("quality_profile"),
            len(json.loads(p).get("requested_by") or []),
        )
        for status, p in rows
    )


def quality_household(root, monkeypatch, full):
    from test_personal_removal import household as removal_household

    root.mkdir()
    store, _, _, track, owner, member, _ = removal_household(root, monkeypatch)
    if full:
        zero(store)
    tracks = [
        {
            **track,
            "id": f"q{n}",
            "title": f"Quality {n}",
            "track_number": n,
            "relative_path": f"q{n}.mp3",
            "codec": "mp3",
            "bitrate": 128,
        }
        for n in range(3)
    ]
    (store.directory / "library-snapshot.json").write_text(
        json.dumps({"tracks": tracks})
    )
    store.refresh_library()
    # Taking the songs up queues their upgrades (membership_changed).
    store.user_libraries.add(owner, [t["id"] for t in tracks])
    return store, owner


def both(tmp_path, monkeypatch, scenario):
    """The queue a scenario leaves with every limit full, and with none."""
    shapes = []
    for full in (True, False):
        store, owner = quality_household(tmp_path / str(full), monkeypatch, full)
        scenario(store, owner)
        shapes.append(shape(store))
    assert shapes[0] == shapes[1]
    assert shapes[0], "the scenario queued nothing to compare"
    return shapes[0]


def emptied(store):
    with store.connect() as conn:
        conn.execute("DELETE FROM queue")


def test_upgrades_after_a_library_add_are_exempt(tmp_path, monkeypatch):
    both(tmp_path, monkeypatch, lambda store, owner: None)


def test_a_members_quality_choice_is_exempt(tmp_path, monkeypatch):
    from server.household_policy import HouseholdPolicy

    def choose(store, owner):
        emptied(store)
        saved = HouseholdPolicy(store).set_quality(owner, "system", "*", "lossless")
        assert "deferred" not in saved

    assert len(both(tmp_path, monkeypatch, choose)) == 3


def test_the_owners_album_quality_is_exempt(tmp_path, monkeypatch):
    def override(store, owner):
        emptied(store)
        album = store.library["albums"][0]["id"]
        assert "deferred" not in store.quality_override("album", album, "lossless")

    assert len(both(tmp_path, monkeypatch, override)) == 3


def test_the_upgrade_scan_is_exempt(tmp_path, monkeypatch):
    def scan(store, owner):
        emptied(store)
        with store.connect() as conn:
            for n in range(3):
                conn.execute(
                    "INSERT OR REPLACE INTO documents VALUES (?,?)",
                    (
                        f"quality-policy:q{n}",
                        json.dumps(
                            {
                                **store.quality_policy(
                                    store.library["albums"][0]["tracks"][0]
                                ),
                                "preferred": "lossless",
                                "recheck_hours": 24,
                                "next_check": 0,
                            }
                        ),
                    ),
                )
        store.save_download_settings({"enabled": True, "sources": ["freemp3cloud"]})
        store.schedule_upgrades()

    both(tmp_path, monkeypatch, scan)


def test_the_owners_library_migration_is_exempt(tmp_path):
    from test_library import apple_export
    from test_library import track as library_track

    shapes = []
    for full in (True, False):
        root = tmp_path / str(full)
        root.mkdir()
        (root / "library-snapshot.json").write_text(json.dumps({"tracks": []}))
        store = Store(root)
        if full:
            zero(store)
        result = store.import_apple(
            apple_export(
                [library_track(id=f"i{n}", title=f"Import {n}") for n in range(3)]
            )
        )
        store.update_selection(
            [t["id"] for a in result["albums"] for t in a["tracks"]], True
        )
        submitted = store.submit_migration()
        assert submitted["created"] == 3 and "left_out" not in submitted
        shapes.append(shape(store))
    assert shapes[0] == shapes[1]


def test_an_artists_discography_is_exempt(tmp_path):
    import threading

    from server.artist_jobs import ArtistJobs

    store = Store(tmp_path)
    zero(store)
    jobs = ArtistJobs.__new__(ArtistJobs)
    jobs.store = store
    jobs.owner = lambda: "owner"
    state = {"status": "running", "generation": "g", "done": [], "created": 0}
    jobs.current = lambda identity, generation: state
    jobs.progress = lambda identity, generation, **changes: state.update(changes)
    jobs.lock = threading.RLock()
    assert jobs.submit("a", "g", "album-1", [missing(n) for n in (1, 2)])
    assert (state["created"], state["done"]) == (2, ["album-1"])
    assert "left_out" not in state
