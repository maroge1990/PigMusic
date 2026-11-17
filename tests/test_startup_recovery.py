"""One malformed recovery record never stops the server starting (#240):
it is skipped as it is, logged and listed, and the other records are still
recovered."""

import json

import pytest
from fastapi.testclient import TestClient

from server import startup_problems
from server import document_records
from server.app import create_app
from server.organisation import Organisation
from server.recovery import Recovery
from server.store import Store
from server.tidy import Tidy
from test_recovery import setup
from test_tidy import legacy, library


@pytest.fixture(autouse=True)
def fresh_problems():
    startup_problems.clear()
    document_records.clear()
    yield
    startup_problems.clear()
    document_records.clear()


def put(store, rows):
    """Write raw payloads, as a damaged or older database might hold them."""
    with store.connect() as c:
        for name, payload in rows.items():
            c.execute(
                "INSERT INTO documents VALUES (?,?) ON CONFLICT(name) DO UPDATE SET payload=excluded.payload",
                (name, payload),
            )


def raw(store, names):
    with store.connect() as c:
        return {
            name: c.execute(
                "SELECT payload FROM documents WHERE name=?", (name,)
            ).fetchone()[0]
            for name in names
        }


def malformed(prefix, wrong_type=None):
    rows = {f"{prefix}:no-status": json.dumps({"id": "no-status"})}
    if wrong_type:
        rows[f"{prefix}:wrong-type"] = json.dumps(wrong_type)
    rows[f"{prefix}:not-an-object"] = json.dumps(["no-status"])
    rows[f"{prefix}:not-json"] = "{secret payload text"
    return rows


def reported(area):
    entries = [p for p in startup_problems.snapshot() if p["area"] == area]
    for entry in entries:
        assert set(entry) == {"at", "area", "record", "error"}
        # The class name only: never the exception text or the payload.
        assert entry["error"].isidentifier()
    return {entry["record"] for entry in entries}


def test_trash_recovery_skips_a_malformed_record(tmp_path, monkeypatch):
    store, root, path, track = setup(tmp_path, monkeypatch)
    recovery = Recovery(store)
    record = recovery.remove("saved")
    record["status"] = "removing"
    recovery.save("removed:" + record["id"], record)
    bad = malformed(
        "removed",
        {
            "id": "wrong-type",
            "status": "removed",
            "quarantine": True,
            "track_id": "saved",
            "root": str(root.resolve()),
            "trash": 5,
        },
    )
    put(store, bad)
    Recovery(Store(store.directory)).recover()
    assert store.read_document("removed:" + record["id"])["status"] == "removed"
    assert raw(store, bad) == bad
    assert reported("trash") == set(bad)


def test_the_store_opens_with_unreadable_removals_and_reports_them_once(
    tmp_path, monkeypatch
):
    store, root, path, track = setup(tmp_path, monkeypatch)
    record = Recovery(store).remove("saved")
    bad = {
        "removed:not-an-object": json.dumps(["saved"]),
        "removed:not-json": "{secret payload text",
        "removed:no-track": json.dumps({"id": "no-track", "status": "removed"}),
        "removed:wrong-type": json.dumps({"status": "removed", "track_id": ["x"]}),
    }
    put(store, bad)
    reopened = Store(store.directory)
    reopened.refresh_library()
    reopened.refresh_library()
    # The good removal still keeps its song out of the library.
    assert not reopened.library["albums"]
    assert raw(reopened, bad) == bad
    names = [p["record"] for p in startup_problems.snapshot()]
    assert sorted(names) == sorted(bad)
    assert all(p["area"] == "trash" for p in startup_problems.snapshot())
    assert reopened.read_document("removed:" + record["id"])["status"] == "removed"


def interrupted_tidy(tmp_path, monkeypatch):
    """A tidy-up stopped by a restart while publishing its first move."""
    tidy, root, _, _ = library(tmp_path, monkeypatch)
    finish = Organisation.finish

    class Restart(BaseException):
        """The process stops: nothing after this runs."""

    def stopped(self, record, root):
        raise Restart

    monkeypatch.setattr(Organisation, "finish", stopped)
    (item,) = tidy.plan()["albums"]
    with pytest.raises(Restart):
        tidy.apply(item["id"], item["fingerprint"])
    monkeypatch.setattr(Organisation, "finish", finish)
    (entry,) = tidy.history()
    (operation,) = tidy.store.read_document("tidy:" + entry["id"])["moves"]
    assert (
        tidy.store.read_document("organisation:" + operation)["status"] == "publishing"
    )
    store = Store(tidy.store.directory)
    recovery = Recovery(store)
    organisation = Organisation(store, recovery)
    return Tidy(store, recovery, organisation), entry["id"], operation


def test_organisation_recovery_skips_a_malformed_record(tmp_path, monkeypatch):
    tidy, _, operation = interrupted_tidy(tmp_path, monkeypatch)
    store = tidy.store
    bad = malformed(
        "organisation",
        {"id": "wrong-type", "status": "publishing", "track_id": ["x"]},
    )
    put(store, bad)
    tidy.organisation.recover()
    assert store.read_document("organisation:" + operation)["status"] == "completed"
    assert raw(store, bad) == bad
    assert reported("organisation") == set(bad)


def test_tidy_recovery_skips_a_malformed_record(tmp_path, monkeypatch):
    tidy, identity, operation = interrupted_tidy(tmp_path, monkeypatch)
    store = tidy.store
    tidy.organisation.recover()
    # As an earlier build stored it (no journal): restart recovery stops it.
    legacy(tidy, store.read_document("tidy:" + identity))
    bad = malformed(
        "tidy",
        {"id": "wrong-type", "status": "applying", "moves": 5, "removals": []},
    )
    # Records of other areas that tidy recovery reads are malformed too.
    others = {
        "organisation:not-json": "{",
        "removed:not-json": "{",
        "organisation:no-status": json.dumps({"id": "o", "placed_from": {}}),
        "removed:no-status": json.dumps({"id": "r"}),
    }
    put(store, bad | others)
    tidy.recover()
    entry = store.read_document("tidy:" + identity)
    assert entry["status"] == "stopped" and entry["error"]
    assert raw(store, bad | others) == bad | others
    assert reported("tidy") == set(bad)
    # Unreadable rows of other areas are reported once, under their own area.
    assert reported("organisation") == {
        "organisation:not-json",
        "organisation:no-status",
    }
    assert reported("trash") == {"removed:not-json", "removed:no-status"}


def malformed_everywhere(store):
    bad = (
        malformed("removed")
        | malformed(
            "organisation", {"id": "w", "status": "publishing", "track_id": [1]}
        )
        | malformed("tidy", {"id": "w", "status": "applying", "moves": 5})
    )
    put(store, bad)
    return bad


TIDY_SKIPS = {
    "tidy:no-status",
    "tidy:wrong-type",
    "tidy:not-an-object",
    "tidy:not-json",
}


def test_the_server_starts_with_malformed_records(tmp_path):
    app = create_app(tmp_path)
    bad = malformed_everywhere(app.state.store)
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
    assert raw(app.state.store, bad) == bad
    assert reported("trash") == {name for name in bad if name.startswith("removed:")}
    assert reported("organisation") == {
        name for name in bad if name.startswith("organisation:")
    }
    assert reported("tidy") == TIDY_SKIPS


def test_the_server_starts_when_a_recovery_fails_as_a_whole(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    bad = malformed_everywhere(app.state.store)

    def broken(self):
        raise RuntimeError("recovery query failed")

    monkeypatch.setattr(Recovery, "recover", broken)
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
    assert "*" in reported("trash")
    (whole,) = [p for p in startup_problems.snapshot() if p["record"] == "*"]
    assert whole["error"] == "RuntimeError"
    # The recoveries after it still ran.
    assert reported("tidy") == TIDY_SKIPS


def recovered_with_malformed_records(tmp_path, monkeypatch):
    """An interrupted tidy-up, malformed records in every area, and a
    restart's recovery: the malformed records stay where they are."""
    tidy, identity, operation = interrupted_tidy(tmp_path, monkeypatch)
    bad = malformed_everywhere(tidy.store)
    tidy.recovery.recover()
    tidy.organisation.recover()
    tidy.recover()
    assert raw(tidy.store, bad) == bad
    return tidy, identity, operation, bad


def test_lists_skip_malformed_records_after_a_restart(tmp_path, monkeypatch):
    tidy, identity, operation, bad = recovered_with_malformed_records(
        tmp_path, monkeypatch
    )
    ids = {name.split(":", 1)[1] for name in bad}
    trash = tidy.recovery.list()
    assert trash and not ids & {r["id"] for r in trash}
    assert [r["id"] for r in tidy.history()] == [identity]
    assert [r["id"] for r in tidy.organisation.undoable()] == [operation]
    assert operation in {r["id"] for r in tidy.organisation.history()}
    assert not ids & {r["id"] for r in tidy.organisation.history()}


def test_a_healthy_tidy_up_undoes_next_to_malformed_records(tmp_path, monkeypatch):
    tidy, identity, operation, bad = recovered_with_malformed_records(
        tmp_path, monkeypatch
    )
    assert tidy.undo(identity)["status"] == "undone"
    assert tidy.store.read_document("organisation:" + operation)["status"] == "undone"
    assert tidy.history() == []
    assert raw(tidy.store, bad) == bad


def test_a_healthy_organisation_step_undoes_next_to_malformed_records(
    tmp_path, monkeypatch
):
    tidy, identity, operation, bad = recovered_with_malformed_records(
        tmp_path, monkeypatch
    )
    tidy.organisation.undo(operation)
    assert tidy.store.read_document("organisation:" + operation)["status"] == "undone"
    assert tidy.organisation.undoable() == []
    assert raw(tidy.store, bad) == bad


def test_an_undo_whose_step_is_unreadable_stops_before_changing_anything(
    tmp_path, monkeypatch
):
    tidy, identity, operation, bad = recovered_with_malformed_records(
        tmp_path, monkeypatch
    )
    step = tidy.store.read_document("organisation:" + operation)
    root = tmp_path / "music"
    before = sorted(p.relative_to(root) for p in root.rglob("*") if p.is_file())
    put(tidy.store, {"organisation:" + operation: "{"})
    with pytest.raises(ValueError):
        tidy.undo(identity)
    after = sorted(p.relative_to(root) for p in root.rglob("*") if p.is_file())
    assert after == before
    assert tidy.store.read_document("tidy:" + identity)["status"] != "undone"
    put(tidy.store, {"organisation:" + operation: json.dumps(step)})
    assert tidy.undo(identity)["status"] == "undone"


def with_unresolved_removal(tmp_path, monkeypatch, removal):
    """A healthy tidy-up that lists `removal` as unresolved."""
    tidy, identity, operation, bad = recovered_with_malformed_records(
        tmp_path, monkeypatch
    )
    record = tidy.store.read_document("tidy:" + identity)
    record["unresolved"] = [{"kind": "removal", "id": removal["id"]}]
    tidy.save(record)
    put(tidy.store, {"removed:" + removal["id"]: json.dumps(removal)})
    return tidy, identity


def files(root):
    return sorted(p.relative_to(root) for p in root.rglob("*") if p.is_file())


def test_a_damaged_unresolved_removal_breaks_neither_history_nor_undo(
    tmp_path, monkeypatch
):
    """Codex: a removal lacking its song details broke history, and Undo
    after it had already put files back."""
    damaged = {"id": "broken", "status": "removed", "track_id": "song"}
    tidy, identity = with_unresolved_removal(tmp_path, monkeypatch, damaged)
    (entry,) = tidy.history()
    assert entry["id"] == identity and entry["unresolved"] == []
    root = tmp_path / "music"
    before = files(root)
    with pytest.raises(ValueError):
        tidy.undo(identity)
    assert files(root) == before
    assert tidy.store.read_document("tidy:" + identity)["status"] == "applying"
    assert "removed:broken" in reported("trash")


def test_an_older_removal_without_song_details_is_still_shown_and_undone_around(
    tmp_path, monkeypatch
):
    # As every release wrote it, but the snapshot had no entry for the song
    # and the fields later releases added are absent.
    older = {
        "id": "older",
        "track_id": "song",
        "track": None,
        "root": str(tmp_path / "music"),
        "original": "Artist/Album/Song.mp3",
        "trash": ".pig-music-trash/older/Artist/Album/Song.mp3",
        "sha256": "0" * 64,
        "status": "removed",
    }
    tidy, identity = with_unresolved_removal(tmp_path, monkeypatch, older)
    (entry,) = tidy.history()
    assert [(u["id"], u["title"]) for u in entry["unresolved"]] == [("older", None)]
    assert "older" in {r["id"] for r in tidy.recovery.list()}
    # Undo puts back what it recorded and leaves the unresolved step listed.
    assert tidy.undo(identity)["status"] == "stopped"
    assert "removed:older" not in reported("trash")


def test_problems_keep_the_latest_fifty_without_detail():
    for n in range(60):
        startup_problems.report("trash", f"removed:{n}", KeyError("/private/path"))
    problems = startup_problems.snapshot()
    assert len(problems) == 50
    assert problems[0]["record"] == "removed:10"
    assert problems[-1] == {
        "at": problems[-1]["at"],
        "area": "trash",
        "record": "removed:59",
        "error": "KeyError",
    }
    problems.clear()
    assert len(startup_problems.snapshot()) == 50
