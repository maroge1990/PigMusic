"""One malformed recovery record never stops the server starting (#240):
it is skipped as it is, logged and listed, and the other records are still
recovered."""

import errno
import json
import math
import os
import shutil
import sqlite3

import pytest
from fastapi.testclient import TestClient

from server import startup_problems, system_health
from server import document_records
from server.app import create_app
from server.organisation import Organisation
from server.recovery import Recovery
from server.store import Store
from server.tidy import Tidy
from test_recovery import setup
from test_tidy import legacy, library
from test_ui_contract import household, sign_in


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
        assert {"at", "area", "record", "error"} <= set(entry)
        assert set(entry) <= {"at", "area", "record", "error", "kind"}
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


def test_a_tidy_up_whose_memberships_cannot_be_bound_is_set_aside_before_undo(
    tmp_path, monkeypatch
):
    """Codex: a membership value SQLite cannot bind failed Undo after it had
    already put files back."""
    tidy, identity, operation, bad = recovered_with_malformed_records(
        tmp_path, monkeypatch
    )
    record = tidy.store.read_document("tidy:" + identity)
    record["memberships"] = [[{}, "song"]]
    put(tidy.store, {"tidy:" + identity: json.dumps(record)})
    root = tmp_path / "music"
    before = files(root)
    with pytest.raises(ValueError):
        tidy.undo(identity)
    assert files(root) == before
    assert tidy.history() == []
    assert "tidy:" + identity in reported("tidy")


def test_a_removal_whose_memberships_cannot_be_bound_is_set_aside_before_restore(
    tmp_path, monkeypatch
):
    store, root, path, track = setup(tmp_path, monkeypatch)
    recovery = Recovery(store)
    record = recovery.remove("saved")
    record["memberships"] = [[{}, "saved", 1, "household-add"]]
    put(store, {"removed:" + record["id"]: json.dumps(record)})
    before = files(root)
    with pytest.raises(ValueError):
        recovery.restore(record["id"])
    assert files(root) == before and not path.exists()
    assert "removed:" + record["id"] in reported("trash")


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
        "kind": "set_aside",
    }
    problems.clear()
    assert len(startup_problems.snapshot()) == 50


# Codex, #257: a number SQLite cannot bind (outside signed 64 bits) or no
# number at all (NaN, Infinity: json reads both) passed the type checks.
OUT_OF_RANGE = [2**63, -(2**63) - 1, float("nan"), float("inf")]


@pytest.mark.parametrize("added", OUT_OF_RANGE)
def test_a_removal_with_an_out_of_range_number_is_set_aside_before_restore(
    tmp_path, monkeypatch, added
):
    store, root, path, track = setup(tmp_path, monkeypatch)
    recovery = Recovery(store)
    with store.connect() as c:
        c.execute(
            "INSERT INTO users(id,username,display_name,role,created_at) VALUES ('owner','owner','Owner','owner',1)"
        )
    record = recovery.remove("saved")
    record["memberships"] = [["owner", "saved", added, "household-add"]]
    put(store, {"removed:" + record["id"]: json.dumps(record)})
    before = files(root)
    assert recovery.restore_many([record["id"]]) == {
        "restored": [],
        "failed": [record["id"]],
    }
    assert files(root) == before and not path.exists()
    assert "removed:" + record["id"] in reported("trash")


@pytest.mark.parametrize(
    "membership",
    [
        ["\ud800", "saved", 1, "household-add"],
        ["owner", "saved", 1, "household-\udfff"],
    ],
)
def test_a_removal_with_text_that_is_not_utf8_is_set_aside_before_restore(
    tmp_path, monkeypatch, membership
):
    """Codex on 7866ac1: json reads an escaped lone surrogate, which SQLite
    can't bind, so Restore linked the file back before failing."""
    store, root, path, track = setup(tmp_path, monkeypatch)
    recovery = Recovery(store)
    with store.connect() as c:
        c.execute(
            "INSERT INTO users(id,username,display_name,role,created_at) VALUES ('owner','owner','Owner','owner',1)"
        )
    record = recovery.remove("saved")
    record["memberships"] = [membership]
    put(store, {"removed:" + record["id"]: json.dumps(record)})
    before = files(root)
    assert recovery.restore_many([record["id"]]) == {
        "restored": [],
        "failed": [record["id"]],
    }
    assert files(root) == before and not path.exists()
    assert "removed:" + record["id"] in reported("trash")


def test_a_song_snapshot_keeps_any_value_json_stores(tmp_path, monkeypatch):
    """Only what is bound into SQL is held to SQLite's limits: the song
    snapshot and paths may hold a large number or a non-UTF-8 file name."""
    store, root, path, track = setup(tmp_path, monkeypatch)
    record = Recovery(store).remove("saved")
    record["track"] = {"\ud800": "x", "mtime_ns": 2**63, "relative_path": "\udcff"}
    record["original"] = "Artist/\udcff.mp3"
    assert document_records.parse("removed:k", json.dumps(record)) == record


def test_a_song_whose_file_time_passes_64_bits_is_removed_and_restored(
    tmp_path, monkeypatch
):
    """Codex on 438047f: an mtime of 2**63 ns in the song snapshot let
    Remove unlink the original and write a record its reader set aside, so
    the song left Trash and Restore said it was not found."""
    store, root, path, track = setup(tmp_path, monkeypatch)
    # As a file's stat gives it (a downloaded song keeps its mtime_ns).
    store.snapshot_tracks["saved"]["mtime_ns"] = 2**63
    recovery = Recovery(store)
    record = recovery.remove("saved")
    assert not path.exists()
    assert record["id"] in {r["id"] for r in recovery.list()}
    recovery.restore(record["id"])
    assert path.read_bytes() == b"original music"
    assert reported("trash") == set()


def test_a_removal_whose_record_would_not_read_back_changes_nothing(
    tmp_path, monkeypatch
):
    store, root, path, track = setup(tmp_path, monkeypatch)

    def unreadable(record):
        raise ValueError("fixture")

    monkeypatch.setitem(document_records.BINDABLE, "removed", unreadable)
    before = files(root)
    with pytest.raises(ValueError, match="nothing was changed"):
        Recovery(store).remove("saved")
    assert files(root) == before and path.exists()
    with store.connect() as c:
        assert not c.execute(
            "SELECT 1 FROM documents WHERE name >= 'removed:' AND name < 'removed;'"
        ).fetchone()


@pytest.mark.parametrize("removed_at", [float("inf"), float("nan")])
def test_a_removal_with_an_out_of_range_time_is_left_out_of_lists(
    tmp_path, monkeypatch, removed_at
):
    store, root, path, track = setup(tmp_path, monkeypatch)
    recovery = Recovery(store)
    record = recovery.remove("saved")
    record["removed_at"] = removed_at
    put(store, {"removed:" + record["id"]: json.dumps(record)})
    assert recovery.purge_plan(0)["items"] == []
    assert "removed:" + record["id"] in reported("trash")


def test_a_tidy_up_whose_removal_has_an_out_of_range_number_stops_before_undo(
    tmp_path, monkeypatch
):
    tidy, identity, operation, bad = recovered_with_malformed_records(
        tmp_path, monkeypatch
    )
    removal = {
        "id": "huge",
        "track_id": "song",
        "root": str(tmp_path / "music"),
        "original": "Artist/Album/Song.mp3",
        "trash": ".pig-music-trash/huge/Artist/Album/Song.mp3",
        "sha256": "0" * 64,
        "status": "removed",
        "memberships": [["owner", "song", 2**63, "household-add"]],
    }
    put(tidy.store, {"removed:huge": json.dumps(removal)})
    record = tidy.store.read_document("tidy:" + identity)
    record["removals"] = ["huge"]
    tidy.save(record)
    root = tmp_path / "music"
    before = files(root)
    with pytest.raises(ValueError):
        tidy.undo(identity)
    assert files(root) == before
    assert tidy.store.read_document("organisation:" + operation)["status"] != "undone"
    assert "removed:huge" in reported("trash")


def test_a_purge_folder_identity_may_use_all_64_bits(tmp_path, monkeypatch):
    """A device or inode number is unsigned and never bound into SQL."""
    store, root, path, track = setup(tmp_path, monkeypatch)
    record = Recovery(store).remove("saved")
    record["quarantine_folder"] = [2**64 - 1, 2**63]
    assert document_records.parse("removed:x", json.dumps(record)) == record
    record["quarantine_folder"] = [2**64, 1]
    assert document_records.parse("removed:y", json.dumps(record)) is None


# #258: a readable record whose file step fails again at startup (disk full,
# permission denied, the library mount changed) is listed as unfinished.
UNFINISHED = (
    "A saved {} task could not be finished yet; it will be tried again at the"
    " next start."
)


def unfinished(area):
    return {
        p["record"]
        for p in startup_problems.snapshot()
        if p["area"] == area and p.get("kind") == "unfinished"
    }


def test_a_trash_task_that_cannot_finish_at_startup_is_listed(tmp_path, monkeypatch):
    store, root, path, track = setup(tmp_path, monkeypatch)
    recovery = Recovery(store)
    healthy = recovery.remove("saved")
    healthy["status"] = "removing"
    recovery.save("removed:" + healthy["id"], healthy)
    # A purge stopped part-way whose Trash folder can't be opened now.
    purge = dict(
        healthy,
        id="stuck-purge",
        status="removed",
        trash=".pig-music-trash/stuck-purge/Artist/Album/Song.mp3",
        quarantine=".pig-music-purge-0",
        quarantine_folder=[1, 2],
    )
    # A removal whose file step fails again: the disk is full.
    (root / "Artist" / "Album" / "Copy.mp3").write_bytes(b"original music")
    full = dict(
        healthy,
        id="disk-full",
        original="Artist/Album/Copy.mp3",
        trash=".pig-music-trash/disk-full/Copy.mp3",
    )
    put(
        store,
        {
            "removed:stuck-purge": json.dumps(purge),
            "removed:disk-full": json.dumps(full),
        },
    )
    real_link, refused = os.link, []

    def no_space(source, target, *args, **kwargs):
        if "disk-full" in str(target):
            refused.append(target)
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_link(source, target, *args, **kwargs)

    monkeypatch.setattr(os, "link", no_space)
    before = raw(store, ["removed:stuck-purge", "removed:disk-full"])
    Recovery(Store(store.directory)).recover()
    assert refused
    assert store.read_document("removed:" + healthy["id"])["status"] == "removed"
    assert raw(store, before) == before
    assert (root / "Artist" / "Album" / "Copy.mp3").is_file()
    assert unfinished("trash") == {"removed:stuck-purge", "removed:disk-full"}
    assert reported("trash") == unfinished("trash")
    lines = system_health.startup_lines()
    assert {line["sentence"] for line in lines} == {UNFINISHED.format("trash")}


def test_an_organisation_task_that_cannot_finish_at_startup_is_listed(
    tmp_path, monkeypatch
):
    tidy, identity, operation = interrupted_tidy(tmp_path, monkeypatch)

    def refused(*args, **kwargs):
        raise PermissionError(errno.EACCES, "Permission denied")

    with monkeypatch.context() as patch:
        patch.setattr(os, "link", refused)
        patch.setattr(os, "replace", refused)
        tidy.organisation.recover()
    name = "organisation:" + operation
    assert tidy.store.read_document(name)["status"] == "publishing"
    assert unfinished("organisation") == {name}
    # Tried again at the next start, it finishes.
    tidy.organisation.recover()
    assert tidy.store.read_document(name)["status"] == "completed"


def test_an_organisation_undo_that_cannot_finish_at_startup_is_listed(
    tmp_path, monkeypatch
):
    tidy, identity, operation, bad = recovered_with_malformed_records(
        tmp_path, monkeypatch
    )
    name = "organisation:" + operation
    step = tidy.store.read_document(name)
    step["status"] = "undoing"
    tidy.recovery.save(name, step)

    def refused(*args, **kwargs):
        raise PermissionError(errno.EACCES, "Permission denied")

    with monkeypatch.context() as patch:
        patch.setattr(os, "link", refused)
        patch.setattr(shutil, "copyfile", refused)
        tidy.organisation.recover()
    assert tidy.store.read_document(name)["status"] == "undoing"
    assert unfinished("organisation") == {name}


def test_the_system_page_lists_an_unfinished_task(tmp_path, monkeypatch):
    startup_problems.report(
        "trash", "removed:x", OSError("/music/private.flac"), kind="unfinished"
    )
    app = household(tmp_path, monkeypatch)
    with TestClient(app, base_url="https://private.ts.net") as client:
        sign_in(client, "mark")
        answer = client.get("/api/v1/admin/system/health")
        assert answer.status_code == 200, answer.text
    problems = answer.json()["startup_problems"]
    assert {
        "at": problems[0]["at"],
        "area": "trash",
        "sentence": UNFINISHED.format("trash"),
    } in problems
    assert "/music" not in answer.text and "removed:x" not in answer.text


def locked_on(monkeypatch, prefix, status):
    """The final journal save of a `prefix` record reaching `status` meets
    a locked database, once."""
    save, failed = Recovery.save, []

    def locked(self, key, value):
        if key.startswith(prefix) and value.get("status") == status and not failed:
            failed.append(key)
            raise sqlite3.OperationalError("database is locked")
        return save(self, key, value)

    monkeypatch.setattr(Recovery, "save", locked)
    return failed


def test_a_locked_database_during_trash_recovery_is_retried_not_set_aside(
    tmp_path, monkeypatch
):
    """Codex on 438047f: a retryable SQLite failure was listed as a record
    set aside rather than a task to be tried again."""
    store, root, path, track = setup(tmp_path, monkeypatch)
    recovery = Recovery(store)
    record = recovery.remove("saved")
    record["status"] = "removing"
    recovery.save("removed:" + record["id"], record)
    failed = locked_on(monkeypatch, "removed:", "removed")
    Recovery(Store(store.directory)).recover()
    name = "removed:" + record["id"]
    assert failed == [name]
    assert unfinished("trash") == {name} and reported("trash") == {name}
    assert store.read_document(name)["status"] == "removing"
    Recovery(Store(store.directory)).recover()
    assert store.read_document(name)["status"] == "removed"


def test_a_locked_database_during_organisation_recovery_is_retried_not_set_aside(
    tmp_path, monkeypatch
):
    tidy, identity, operation = interrupted_tidy(tmp_path, monkeypatch)
    name = "organisation:" + operation
    failed = locked_on(monkeypatch, "organisation:", "completed")
    tidy.organisation.recover()
    assert failed == [name]
    assert unfinished("organisation") == {name}
    assert reported("organisation") == {name}
    assert tidy.store.read_document(name)["status"] == "publishing"
    tidy.organisation.recover()
    assert tidy.store.read_document(name)["status"] == "completed"


def test_trash_lists_every_record_beside_an_odd_song_snapshot(tmp_path, monkeypatch):
    """Codex on b71c163: a snapshot holding a lone surrogate or NaN, kept
    as json stores it, aborted the strict JSON encoder and hid all of
    Trash. The answer shows U+FFFD and null; the record is unchanged."""
    app = household(tmp_path, monkeypatch)

    def removal(identity, track):
        return {
            "id": identity,
            "status": "removed",
            "track_id": "song",
            "track": track,
            "root": str(tmp_path / "nas"),
            "original": "song.mp3",
            "trash": f".pig-music-trash/{identity}/song.mp3",
            "sha256": "0" * 64,
        }

    rows = {
        "removed:healthy": json.dumps(removal("healthy", {"title": "One"})),
        "removed:surrogate": json.dumps(removal("surrogate", {"title": "\udcff"})),
        "removed:nan": json.dumps(removal("nan", {"title": "Two", "gain": math.nan})),
    }
    put(app.state.store, rows)
    with TestClient(app, base_url="https://private.ts.net") as client:
        sign_in(client, "mark")
        answer = client.get("/api/v1/admin/library/trash")
    assert answer.status_code == 200, answer.text
    listed = {r["id"]: r["track"] for r in answer.json()}
    assert listed == {
        "healthy": {"title": "One"},
        "surrogate": {"title": "\ufffd"},
        "nan": {"title": "Two", "gain": None},
    }
    assert raw(app.state.store, rows) == rows
