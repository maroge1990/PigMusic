"""Every way of asking for a song, from every state it can be in (#244).

#235 was a state nobody had tried: a failed job that named a release, asked
for again from a playlist that names none, answered a 500 (KeyError). Here
every prior state of a song (nothing, waiting, searching, downloading,
paused, failed, in the library, removed; the earlier job asked for with no
release, with one, or as "only this edition"; by the same person or another)
meets every way of asking (a song with no release, the same release, another
release or as an edition; its album; an Apple Music playlist; artist
monitoring; Retry of the job). Each must answer without a 5xx and leave at
most one unfinished job for the song (one per edition asked for as such).

One household serves the whole module; each case has a song of its own.
"""

import hashlib
import itertools
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.auth import Auth
from server.catalogue import Catalogue
from server.matching import song_key
from server.queue_states import PENDING
from server.storage import library_root
from server.store import Store

PASSWORD = "request matrix fixture password"

STATES = [
    "waiting",
    "searching",
    "downloading",
    "paused",
    "failed",
    "in_library",
    "removed",
]
# How the earlier job named its edition.
EDITIONS = ["no_release", "release", "edition"]
REQUESTERS = ["same", "other"]
WAYS = [
    "song_no_release",
    "song_same_release",
    "song_other_release",
    "song_edition",
    "album",
    "playlist",
    "monitoring",
    "retry",
]

PRIORS = [("none", None, None)] + list(itertools.product(STATES, EDITIONS, REQUESTERS))
CASES = [(*prior, way) for prior in PRIORS for way in WAYS]


def case_id(case):
    state, edition, requester, way = case
    return "-".join(p for p in (state, edition, requester, way) if p)


def release(n, which):
    return str(uuid.UUID(int=n * 16 + which, version=4))


def track(n, release_id=None, explicit=False, number=1, title=None):
    song = {
        "artist": f"Matrix Artist {n}",
        "album_artist": f"Matrix Artist {n}",
        "album": f"Matrix Album {n}",
        "title": title or f"Matrix Song {n}",
        "year": "2024",
        "duration": 200,
        "disc_number": 1,
        "track_number": number,
    }
    if release_id:
        song["musicbrainz_release_id"] = release_id
    if explicit:
        song["explicit_edition"] = True
    return song


class Household:
    def __init__(self, root, monkeypatch):
        monkeypatch.delenv("PIG_MUSIC_TEST_LEGACY_API", raising=False)
        monkeypatch.setenv("PIG_MUSIC_PRELOAD", "false")
        music = root / "music"
        music.mkdir()
        monkeypatch.setenv("PIG_MUSIC_LIBRARY", str(music))
        data = root / "data"
        auth = Auth(Store(data))
        self.owner = auth.create_owner("mark", PASSWORD)
        code = auth.setup_code(self.owner, "member", "Member")
        self.member = auth.activate(code["code"], PASSWORD, "fixture")["id"]
        self.app = create_app(data)
        self.clients = {}
        for user, name in ((self.owner, "mark"), (self.member, "member")):
            token = self.app.state.auth.login(
                name, PASSWORD, "device", "phone", "ios", "fixture"
            )
            self.clients[user] = TestClient(
                self.app,
                base_url="https://private.ts.net",
                headers={"Authorization": "Bearer " + token["token"]},
            )
        self.store = self.app.state.store
        # Only the tests move jobs on: the app's downloader claims nothing.
        monkeypatch.setattr(self.store, "claim_request", lambda: None)
        self.store.save_download_settings(
            {"enabled": True, "sources": ["freemp3cloud"]}
        )

    @property
    def client(self):
        return self.clients[self.owner]

    def jobs(self, song):
        with self.store.connect() as conn:
            rows = conn.execute(
                "SELECT id,status,payload FROM queue WHERE "
                "json_extract(payload,'$.song_key')=json(?) ORDER BY created_at,id",
                (json.dumps(list(song_key(song))),),
            ).fetchall()
        return [(r[0], r[1], json.loads(r[2])) for r in rows]

    def set_status(self, job_id, status):
        token = uuid.uuid4().hex
        with self.store.connect() as conn:
            conn.execute(
                "UPDATE queue SET status=?,payload=json_set(payload,'$.claim_token',?) "
                "WHERE id=?",
                (status, token, job_id),
            )
        return token

    def publish(self, job_id, n):
        """The downloader's part for this one job: claim, stage, publish."""
        token = self.set_status(job_id, "searching")
        ((_, _, payload),) = [j for j in self.jobs(track(n)) if j[0] == job_id]
        job = {"id": job_id, **payload, "claim_token": token}
        self.store.request_state(job_id, "validating", _claim_token=token)
        stage = self.store.directory / "downloads" / "staging"
        stage.mkdir(parents=True, exist_ok=True)
        source = stage / (job_id + ".mp3")
        source.write_bytes(b"music " + job_id.encode())
        target = library_root(self.store) / f"Matrix Artist {n}" / f"Song {n}.mp3"
        target.parent.mkdir(parents=True, exist_ok=True)
        self.store.publish_request(
            job,
            source,
            target,
            {"codec": "mp3", "bitrate_kbps": 320, "duration": 200},
            hashlib.sha256(source.read_bytes()).hexdigest(),
        )

    def remove(self, user, n):
        client = self.clients[user]
        ids = [
            t["id"]
            for t in self.store.user_libraries.projection(user)["tracks"]
            if t["title"] == f"Matrix Song {n}"
        ]
        assert ids, "nothing to remove"
        preview = client.post(
            "/api/v1/library/removal-preview", json={"track_ids": ids}
        )
        assert preview.status_code == 200, preview.text
        removed = client.post(
            "/api/v1/library/remove",
            json={"track_ids": ids, "confirmation": preview.json()["confirmation"]},
        )
        assert removed.status_code == 200, removed.text


@pytest.fixture(scope="module")
def household(tmp_path_factory):
    with pytest.MonkeyPatch.context() as monkeypatch:
        place = Household(tmp_path_factory.mktemp("matrix"), monkeypatch)
        with place.client:
            yield place


counter = itertools.count(1)


def prepare(place, n, state, edition, requester):
    """The song's prior state; the earlier job's id (None for none)."""
    if state == "none":
        return None
    who = place.owner if requester == "same" else place.member
    earlier = track(
        n,
        release_id=None if edition == "no_release" else release(n, 1),
        explicit=edition == "edition",
    )
    # Through the person's own client, as the apps ask (the API's model
    # makes the duration a float, which an explicit edition's job id keeps).
    client = place.clients[who]
    result = client.post("/api/v1/requests", json={"tracks": [earlier]})
    assert result.status_code == 200 and result.json()["created"] == 1, result.text
    ((job_id, _, _),) = place.jobs(earlier)
    if state in {"searching", "downloading", "failed"}:
        place.set_status(job_id, state)
    elif state == "cancelled":
        cancelled = client.post(f"/api/v1/requests/{job_id}/cancel")
        assert cancelled.json()["cancelled"], cancelled.text
        assert [j[1] for j in place.jobs(earlier)] == ["cancelled"]
    elif state == "paused":
        paused = client.post(f"/api/v1/requests/{job_id}/pause")
        assert paused.json() == {"paused": True}, paused.text
    elif state in {"in_library", "removed"}:
        place.publish(job_id, n)
        assert [j[1] for j in place.jobs(earlier)] == ["completed"]
        if state == "removed":
            place.remove(who, n)
    return job_id


def ask(place, n, way, prior_id):
    """Asks in `way`; returns the HTTP status (200 for monitoring, which is
    no HTTP call: an exception there fails the test as a 500 would)."""
    client = place.client
    if way.startswith("song_"):
        song = {
            "song_no_release": track(n),
            "song_same_release": track(n, release(n, 1)),
            "song_other_release": track(n, release(n, 2)),
            "song_edition": track(n, release(n, 1), explicit=True),
        }[way]
        return client.post("/api/v1/requests", json={"tracks": [song]})
    if way == "album":
        songs = [
            track(n, release(n, 1)),
            track(n, release(n, 1), number=2, title=f"Matrix Other {n}"),
        ]
        return client.post("/api/v1/requests", json={"tracks": songs})
    if way == "playlist":
        return client.post(
            "/api/v1/imports/apple-music",
            json={
                "apple_playlist_id": f"pl.matrix{n}",
                "name": f"Matrix {n}",
                "songs": [
                    {
                        "title": f"Matrix Song {n}",
                        "artist": f"Matrix Artist {n}",
                        "album": f"Matrix Album {n}",
                        "duration": 200,
                    }
                ],
                "download_missing": True,
            },
        )
    if way == "monitoring":
        tracks = Catalogue(place.store).presence([track(n, release(n, 1))])
        place.store.submit_catalogue(tracks, user_id=place.owner)
        return None
    assert way == "retry"
    return client.post(f"/api/v1/requests/{prior_id}/retry")


def unfinished_twins(jobs):
    """Unfinished jobs that stand for the same thing: any edition (ordinary
    jobs), or one edition asked for as such. One of each at most."""
    seen = {}
    for job_id, status, payload in jobs:
        if status in PENDING:
            edition = payload.get("explicit_edition") and payload.get(
                "musicbrainz_release_id"
            )
            seen.setdefault(edition or "any edition", []).append((job_id, status))
    return {k: v for k, v in seen.items() if len(v) > 1}


@pytest.mark.parametrize(
    "state,edition,requester,way",
    [pytest.param(*case, id=case_id(case)) for case in CASES],
)
def test_asking_again_from_any_state(household, state, edition, requester, way):
    if way == "retry" and state == "none":
        pytest.skip("No job to retry: Retry is offered only on an existing request")
    n = next(counter)
    prior_id = prepare(household, n, state, edition, requester)
    answer = ask(household, n, way, prior_id)
    if answer is not None:
        assert answer.status_code < 500, (answer.status_code, answer.text)
        assert answer.headers["content-type"].startswith("application/json")
    twins = unfinished_twins(household.jobs(track(n)))
    assert not twins, (twins, household.jobs(track(n)))


# Then Retry of the earlier job, which finished without a file. When asking
# again made another job for the song meanwhile (the earlier job named another
# release, or the new request's details give another id: a playlist names no
# album artist or year, monitoring's duration is MusicBrainz's whole
# milliseconds), Retry joins that job rather than queue a twin (#243); an
# "only this edition" job is another thing. A person's own Cancel withdraws
# them from the job, so their Retry finds nothing (404); only the
# administrator's Retry reaches it.
RETRIED = [
    (state, edition, requester, way, route)
    for state in ("failed", "cancelled")
    for edition, requester in itertools.product(EDITIONS, REQUESTERS)
    for way in WAYS
    if way != "retry"
    for route in ("mine", "admin")
]


@pytest.mark.parametrize(
    "state,edition,requester,way,route",
    [pytest.param(*case, id="-".join(case)) for case in RETRIED],
)
def test_retrying_the_earlier_job_after_asking_again(
    household, state, edition, requester, way, route
):
    n = next(counter)
    prior_id = prepare(household, n, state, edition, requester)
    answer = ask(household, n, way, prior_id)
    assert answer is None or answer.status_code < 500, answer.text
    who = household.owner if requester == "same" else household.member
    address = {
        "mine": f"/api/v1/requests/{prior_id}/retry",
        "admin": f"/api/v1/admin/queue/{prior_id}/retry",
    }[route]
    client = household.clients[who if route == "mine" else household.owner]
    retried = client.post(address)
    assert retried.status_code < 500, retried.text
    twins = unfinished_twins(household.jobs(track(n)))
    assert not twins, (twins, household.jobs(track(n)))
