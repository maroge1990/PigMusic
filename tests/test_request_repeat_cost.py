"""Asking again for songs already queued costs one queue pass, not one per
song (#237).

Every song of a request that already had an unfinished job re-read and
re-evaluated the whole queue (`HouseholdPolicy.reconcile_pending`) inside the
write transaction: a repeated 500-song playlist did it 500 times while other
writers waited on the database.
"""

import json

from server import user_library
from server.auth import Auth
from server.household_policy import HouseholdPolicy
from server.store import Store

PASSWORD = "repeat cost fixture password"


def song(title, number=1, **changes):
    return {
        "title": title,
        "artist": "Example",
        "album_artist": "Example",
        "album": "Studio",
        "year": 2020,
        "disc_number": 1,
        "track_number": number,
        "duration": 200,
        "musicbrainz_release_id": "11111111-1111-4111-8111-111111111111",
        "status": "missing",
        **changes,
    }


def count_reconciles(monkeypatch):
    calls = []
    reconcile = HouseholdPolicy.reconcile_pending

    def counted(self, conn):
        calls.append(1)
        return reconcile(self, conn)

    monkeypatch.setattr(HouseholdPolicy, "reconcile_pending", counted)
    return calls


def test_songs_already_queued_are_reconciled_once_per_request(tmp_path, monkeypatch):
    store = Store(tmp_path)
    songs = [song(f"Song {i}", i) for i in range(30)]
    assert store.submit_catalogue(songs, user_id="alice")["created"] == 30
    calls = count_reconciles(monkeypatch)
    result = store.submit_catalogue(songs, user_id="bob", resume_paused=True)
    assert result["existing"] == 30 and result["created"] == 0
    assert len(calls) == 1


def test_a_request_of_only_new_songs_reconciles_nothing(tmp_path, monkeypatch):
    store = Store(tmp_path)
    calls = count_reconciles(monkeypatch)
    store.submit_catalogue([song(f"Song {i}", i) for i in range(5)], user_id="alice")
    assert calls == []


def test_a_one_off_quality_stands_on_jobs_made_after_a_queued_song(tmp_path):
    """Unchanged by #237: the one-off quality is saved for the first song's
    album only, so a reconcile after the last song would take it off a new
    job of another album. Such a request keeps the reconcile where it was."""
    store = Store(tmp_path)
    queued = song("Queued")
    store.submit_catalogue([queued], user_id="alice")
    other = song("Other", album="Live", musicbrainz_release_id="")
    result = store.submit_catalogue(
        [queued, other], preferred="lossless", user_id="alice"
    )
    assert result["existing"] == 1 and result["created"] == 1
    with store.connect() as conn:
        (raw,) = conn.execute(
            "SELECT payload FROM queue WHERE id=?", (by_title(store, "Other"),)
        ).fetchone()
    assert json.loads(raw)["quality_profile"] == "lossless"


def by_title(store, title):
    with store.connect() as conn:
        return next(
            key
            for key, raw in conn.execute("SELECT id,payload FROM queue")
            if json.loads(raw)["title"] == title
        )


def have(i):
    """A song the household has, as the library holds it."""
    return {
        **song(f"Have {i}", 40 + i, status=None),
        "id": f"have-{i}",
        "relative_path": f"Example/Studio/Have {i}.mp3",
        "codec": "mp3",
        "bitrate": 128,
    }


def household(directory, songs):
    """A store whose library holds `songs`, and two people."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "library-snapshot.json").write_text(json.dumps({"tracks": songs}))
    store = Store(directory)
    auth = Auth(store)
    owner = auth.create_owner("mark", PASSWORD)
    alice, bob = (
        auth.activate(
            auth.setup_code(owner, name, name.title())["code"], PASSWORD, "fixture"
        )["id"]
        for name in ("alice", "bob")
    )
    return store, alice, bob


def test_songs_the_household_has_are_taken_up_once_per_request(tmp_path, monkeypatch):
    """Adding a playlist of music already on the server walked the whole
    library and queue (`membership_changed`) once per song."""
    store, alice, bob = household(tmp_path, [have(i) for i in range(30)])
    calls = []
    changed = HouseholdPolicy.membership_changed

    def counted(self, conn, track_ids):
        calls.append(list(track_ids))
        return changed(self, conn, track_ids)

    monkeypatch.setattr(HouseholdPolicy, "membership_changed", counted)
    reconciles = count_reconciles(monkeypatch)
    wanted = [song(f"Have {i}", 40 + i, status="downloaded") for i in range(30)]
    # One the household already has beside one already queued: still one pass.
    store.submit_catalogue([song("Queued")], user_id=alice)
    result = store.submit_catalogue(
        [*wanted, song("Queued")], user_id=bob, resume_paused=True
    )
    assert result == {
        "created": 0,
        "existing": 31,
        "skipped": 0,
        "paused": 0,
        "paused_ids": [],
        "downloads_enabled": False,
    }
    assert calls == [[f"have-{i}" for i in range(30)]]
    assert len(reconciles) == 1
    assert all(store.user_libraries.contains(bob, f"have-{i}") for i in range(30))


def seeded(directory):
    """A queue with every kind of job a repeated request can meet, and a
    library of songs the household has. Bob wants lossless, so a job of
    Alice's that he joins takes a new target, and a song he takes up is
    queued for an upgrade."""
    store, alice, bob = household(directory, [have(i) for i in range(5)])
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO quality_preferences VALUES (?,?,?,?)",
            (bob, "system", "*", "lossless"),
        )
        for user, track in ((alice, 0), (alice, 1), (bob, 3)):
            conn.execute(
                "INSERT INTO library_members VALUES (?,?,0,'household-add')",
                (user, f"have-{track}"),
            )
        # An upgrade of Alice's already under way for a song Bob takes up.
        conn.execute(
            "INSERT INTO queue(id,payload,status) VALUES ('upgrade',?,'downloading')",
            (
                json.dumps(
                    {
                        **song("Have 1", 41, status=None),
                        "library_track_id": "have-1",
                        "quality_profile": "mp3_320",
                        "requested_by": [alice],
                        "source": "x",
                    }
                ),
            ),
        )
    store.submit_catalogue(
        [song(f"Mine {i}", i) for i in range(3)] + [song("Paused mine", 10)],
        user_id=bob,
    )
    store.submit_catalogue(
        [song(f"Theirs {i}", 20 + i) for i in range(3)]
        + [song("Paused theirs", 11), song("Failed", 12)],
        user_id=alice,
    )
    with store.connect() as conn:
        for title in ("Paused mine", "Paused theirs"):
            conn.execute(
                "UPDATE queue SET status='paused' WHERE id=?", (by_title(store, title),)
            )
        conn.execute(
            "UPDATE queue SET status='failed' WHERE id=?", (by_title(store, "Failed"),)
        )
        # Under way from a source: a new target sends it back to the start.
        key = by_title(store, "Theirs 1")
        (raw,) = conn.execute("SELECT payload FROM queue WHERE id=?", (key,)).fetchone()
        conn.execute(
            "UPDATE queue SET status='downloading',payload=? WHERE id=?",
            (json.dumps({**json.loads(raw), "source": "x", "received_bytes": 5}), key),
        )
    return store, alice, bob


def submission():
    edition = "22222222-2222-4222-8222-222222222222"
    return [
        song("New 0", 30),
        song("Have 0", 40, status="downloaded"),
        song("Mine 0", 0),
        song("Theirs 0", 20),
        song("Paused mine", 10),
        song("Have 3", 43, status="downloaded"),  # Already in his library.
        song("New 1", 31),
        # The household has it, with an upgrade under way.
        song("Have 1", 41, status="downloaded"),
        song("Theirs 1", 21),
        # The failed song asked for again without an edition, then as "only
        # this edition".
        song("Failed", 12),
        song("Failed", 12, explicit_edition=True, musicbrainz_release_id=edition),
        song("Paused theirs", 11),
        song("New 0", 30),  # Twice in one request.
        song("Have 0", 40, status="downloaded"),  # And this one.
        song("Mine 1", 1),
        song("Have 2", 42, status="downloaded"),
        song("Theirs 2", 22),
        song("Mine 2", 2),
        song("Have 4", 44, status="downloaded"),
        # New after the last song that was already queued or in the library.
        song("New 2", 32),
    ]


def rows(store, alice, bob):
    """The queue, who has which song, each person's library revision and the
    unassigned songs, without what differs between any two runs: timestamps,
    the random claim token (kept as whether one was given), the people's
    random ids (named instead) and an upgrade job's id, which holds the time
    (named by its song and target instead)."""
    result = {}
    with store.connect() as conn:
        names = dict(conn.execute("SELECT id,username FROM users"))
        assert names[alice] == "alice" and names[bob] == "bob"
        for key, status, raw in conn.execute("SELECT id,status,payload FROM queue"):
            for identity, name in names.items():
                raw = raw.replace(identity, name)
            payload = json.loads(raw)
            if "claim_token" in payload:
                payload["claim_token"] = True
            if key != "upgrade" and payload.get("library_track_id"):
                key = "upgrade of {library_track_id} to {quality_profile}".format(
                    **payload
                )
            assert key not in result
            result[key] = (status, payload)
        result["members"] = sorted(
            (names.get(user, "owner"), track, source)
            for user, track, source in conn.execute(
                "SELECT user_id,track_id,source FROM library_members"
            )
        )
        result["revisions"] = sorted(
            (names.get(user, "owner"), revision)
            for user, revision in conn.execute(
                "SELECT user_id,revision FROM membership_versions"
            )
        )
        result["unassigned"] = sorted(
            r[0] for r in conn.execute("SELECT track_id FROM unassigned_tracks")
        )
    return result


def test_one_pass_leaves_the_queue_as_one_per_song_did(tmp_path, monkeypatch):
    """The reference replays the same request one song at a time, so the
    queue is reconciled after every song that was already queued, and the
    library and queue walked after every song the household already had, as
    they were before #237. Only what a request computes across its songs is carried
    over: each song's scope (album or song) comes from the whole request."""
    for resume_paused in (True, False):
        tracks = submission()
        batch, alice, bob = seeded(tmp_path / f"batch-{resume_paused}")
        result = batch.submit_catalogue(
            tracks, user_id=bob, resume_paused=resume_paused
        )

        reference, their_alice, their_bob = seeded(
            tmp_path / f"reference-{resume_paused}"
        )
        expected = {
            "created": 0,
            "existing": 0,
            "skipped": 0,
            "paused": 0,
            "paused_ids": [],
        }
        scopes = user_library.request_scope(tracks)
        with monkeypatch.context() as patch:
            position = iter(scopes)
            patch.setattr(user_library, "request_scope", lambda _: [next(position)])
            for track in tracks:
                one = reference.submit_catalogue(
                    [track], user_id=their_bob, resume_paused=resume_paused
                )
                for name in expected:
                    expected[name] += one[name]
                expected["downloads_enabled"] = one["downloads_enabled"]

        assert result == expected
        assert result["created"] >= 4 and result["existing"] >= 12
        assert result["skipped"] == 2
        after = rows(batch, alice, bob)
        assert after == rows(reference, their_alice, their_bob)
        # The reconcile had work to do: Bob's target reached Alice's jobs.
        joined = after[by_title(batch, "Theirs 1")]
        assert joined[0] == "waiting_for_sources"
        assert joined[1]["quality_profile"] == "lossless"
        assert joined[1]["claim_token"] is True and "source" not in joined[1]
        paused = after[by_title(batch, "Paused theirs")]
        assert paused[0] == ("waiting_for_sources" if resume_paused else "paused")
        assert paused[1]["quality_profile"] == "lossless"
        # Taking up the household's songs had work to do too: an upgrade
        # queued for each, and the one under way given Bob's target.
        for track in (0, 2, 4):
            upgrade = after[f"upgrade of have-{track} to lossless"]
            assert upgrade[0] == "waiting_for_sources"
            assert "bob" in upgrade[1]["requested_by"]
        assert after["upgrade"][0] == "waiting_for_sources"
        assert after["upgrade"][1]["quality_profile"] == "lossless"
        assert ("bob", "have-1", "household-add") in after["members"]
