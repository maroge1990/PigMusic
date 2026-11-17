"""One malformed recovery record never stops the server starting (#240):
it is skipped as it is, logged and listed, and the other records are still
recovered."""

import json

import pytest
from fastapi.testclient import TestClient

from server import startup_problems
from server import store as store_module
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
    store_module._unreadable_removals.clear()
    yield
    startup_problems.clear()
    store_module._unreadable_removals.clear()


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
    # Unreadable rows of other areas are reported by their own recovery.
    assert reported("tidy") == set(bad) | {
        "organisation:no-status",
        "removed:no-status",
    }


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


# Tidy recovery also reads removals: one without a status is skipped there too.
TIDY_SKIPS = {
    "tidy:no-status",
    "tidy:wrong-type",
    "tidy:not-an-object",
    "tidy:not-json",
    "removed:no-status",
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
