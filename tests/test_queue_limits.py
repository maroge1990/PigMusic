"""How much a person may queue (D-128, #242).

Per person up to 2,000 songs waiting and 1,000 new songs in any 10 minutes;
the household 10,000 waiting; everyone included, the owner too. Past a
limit the songs that fit are queued, in order, and the rest are answered
`over_limit`; nothing already asked for is lost, and asking again for a
song already queued or paused never counts.
"""

import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from server import queue_limits
from server.queue_limits import QueueLimits
from server.store import Store
from test_request_matrix import Household, track


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
    assert result["outcomes"] == ["created"] * 3 + ["over_limit"] * 2
    assert (result["created"], result["left_out"]) == (3, 2)
    assert (result["limit"], result["retry_after"]) == ("person_waiting", None)
    assert queued_titles(store) == [f"Matrix Song {n}" for n in (1, 2, 3)]
    # Nothing lost: asking again for the batch changes nothing and loses
    # nothing; once a job finishes there is room for the next song.
    again = limited(store, "alice", [song(n) for n in range(1, 6)])
    assert again["outcomes"] == ["existing"] * 3 + ["over_limit"] * 2
    with store.connect() as conn:
        conn.execute(
            "UPDATE queue SET status='completed' WHERE json_extract(payload,'$.title')=?",
            ("Matrix Song 1",),
        )
    later = limited(store, "alice", [song(n) for n in range(2, 6)])
    assert later["outcomes"] == ["existing", "existing", "created", "over_limit"]
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
        assert result["outcomes"] == ["existing", "paused"]
        assert (result["left_out"], result["limit"]) == (0, None)
    assert waiting(store, "alice") == (2, 2)


def test_joining_another_persons_job_is_free_but_counts_as_waiting(tmp_path):
    store = Store(tmp_path)
    small(store, person=1)
    assert limited(store, "bob", [song(1)])["created"] == 1
    result = limited(store, "alice", [song(1), song(2)])
    # Song 1 joined bob's job (free); it now waits for alice too, so song 2
    # is past her cap of one.
    assert result["outcomes"] == ["existing", "over_limit"]


def test_the_household_ceiling_holds_across_two_people(tmp_path):
    store = Store(tmp_path)
    small(store, person=10, household=4)
    assert limited(store, "alice", [song(n) for n in (1, 2, 3)])["created"] == 3
    result = limited(store, "bob", [song(n, 2) for n in (4, 5, 6)])
    assert result["outcomes"] == ["created", "over_limit", "over_limit"]
    assert result["limit"] == "household_waiting"
    assert waiting(store)[0] == 4


def test_cancelled_and_finished_jobs_free_room(tmp_path):
    store = Store(tmp_path)
    small(store, person=2)
    limited(store, "alice", [song(1), song(2)])
    assert limited(store, "alice", [song(3)])["outcomes"] == ["over_limit"]
    with store.connect() as conn:
        conn.execute(
            "UPDATE queue SET status='cancelled' WHERE json_extract(payload,'$.title')=?",
            ("Matrix Song 1",),
        )
    assert limited(store, "alice", [song(3)])["outcomes"] == ["created"]


def test_a_paused_job_holds_its_place_so_resuming_never_passes_the_cap(tmp_path):
    store = Store(tmp_path)
    small(store, person=2)
    limited(store, "alice", [song(1), song(2)])
    with store.connect() as conn:
        conn.execute("UPDATE queue SET status='paused'")
    assert limited(store, "alice", [song(3)])["outcomes"] == ["over_limit"]


def test_the_rate_bucket_refills_on_its_clock_and_is_per_person(tmp_path):
    store = Store(tmp_path)
    clock = Clock()
    small(store, person=100, songs=4, seconds=60, clock=clock)
    result = limited(store, "alice", [song(n) for n in range(1, 7)])
    assert result["outcomes"] == ["created"] * 4 + ["over_limit"] * 2
    # One song refills every 15 seconds.
    assert (result["limit"], result["retry_after"]) == ("rate", 15)
    # Bob has his own bucket.
    assert limited(store, "bob", [song(n) for n in range(11, 15)])["created"] == 4
    clock.now += 14
    assert limited(store, "alice", [song(5)])["outcomes"] == ["over_limit"]
    clock.now += 1
    assert limited(store, "alice", [song(5), song(6)])["outcomes"] == [
        "created",
        "over_limit",
    ]
    clock.now += 600
    assert limited(store, "alice", [song(6)])["outcomes"] == ["created"]
    # Never more than the bucket holds.
    clock.now += 6000
    assert store.queue_limits.available("alice") == 4


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
        assert body["outcomes"] == ["created", "created", "over_limit"]
        assert (body["left_out"], body["limit"]) == (1, "person_waiting")
        # One song, left out: a busy answer the iPhone keeps, which still
        # says what happened. No Retry-After: room comes as songs finish.
        alone = place.client.post("/api/v1/requests", json={"tracks": [song(3)]})
        assert alone.status_code == 429, alone.text
        assert alone.json()["code"] == "queue_limit"
        assert alone.json()["outcomes"] == ["over_limit"]
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
    small(place.store, person=100, songs=2, seconds=600, clock=Clock())
    with place.client:
        place.client.post("/api/v1/requests", json={"tracks": [song(1), song(2)]})
        answer = place.client.post("/api/v1/requests", json={"tracks": [song(3)]})
        assert answer.status_code == 429, answer.text
        assert answer.headers["retry-after"] == "300"
        assert (answer.json()["limit"], answer.json()["retry_after"]) == ("rate", 300)


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
