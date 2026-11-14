"""Verify candidate projection against a stopped, read-only production backup.

Runs without NAS/network mounts. Only disposable state is modified; durable
rows must remain identical except for edition anchors and validated additive display-cache pointers.
"""

import argparse
import json
import re
import shutil
import sqlite3
from pathlib import Path

from server.store import Store


def rows(database):
    with sqlite3.connect(database) as conn:
        tables = [
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        ]
        return {
            table: sorted(
                conn.execute(
                    'SELECT * FROM "' + table.replace('"', '""') + '"'
                ).fetchall()
            )
            for table in tables
        }


def retired_plex(name):
    """Plex sync state that migration 11 deletes."""
    return (
        name.startswith(("plex-status", "plex-playlist:"))
        or name == "plex-refresh-required"
    )


def verify_owner_attribution(store, before, owner_id):
    """Validate the deliberate account cutover on a disposable copy only."""
    after = rows(store.directory / "pig-music.sqlite3")
    tracks = {t["id"] for a in store.library["albums"] for t in a["tracks"]}
    assert {r[1] for r in after["library_members"] if r[0] == owner_id} == tracks
    assert all(
        r[0] == owner_id and r[3] == "migration" for r in after["library_members"]
    )
    previous_playlists = {r[0]: r for r in before["playlists"]}
    for row in after["playlists"]:
        key, raw, attributed = row
        original_key = "pig-favourites" if key == "pig-favourites:" + owner_id else key
        previous = previous_playlists.pop(original_key)
        expected = json.loads(previous[1])
        if original_key == "pig-favourites":
            expected["id"] = key
        assert json.loads(raw) == expected and attributed == owner_id
    assert not previous_playlists
    previous_queue = {r[0]: r for r in before["queue"]}
    for row in after["queue"]:
        previous = previous_queue.pop(row[0])
        expected = json.loads(previous[1])
        expected.setdefault("requested_by", [owner_id])
        assert json.loads(row[1]) == expected and row[2:] == previous[2:]
    assert not previous_queue
    documents = dict(after["documents"])
    for key, raw in before["documents"]:
        if retired_plex(key):
            continue
        assert documents[key] == raw
        if key.startswith("artist-job:") and key.count(":") == 1:
            artist_id = key.split(":")[1]
            assert json.loads(
                documents["artist-job:" + owner_id + ":" + artist_id]
            ) == {**json.loads(raw), "id": artist_id, "user_id": owner_id}
    for table in (
        "selections",
        "migration_decisions",
        "managed_files",
        "play_events",
        "device_copies",
        "native_mutations",
        "user_preferences",
        "quality_preferences",
        "unassigned_tracks",
        "sync_entities",
        "sync_revisions",
        "sqlite_sequence",
    ):
        if table in before:
            assert before[table] == after[table]
    from server.user_library import initialise_owner

    initialise_owner(store)
    assert rows(store.directory / "pig-music.sqlite3") == after, "Attribution repeated"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--expected", type=Path, required=True)
    parser.add_argument(
        "--bootstrap-owner-fixture",
        action="store_true",
        help="Also validate owner attribution on the disposable destination",
    )
    args = parser.parse_args()
    if args.destination.exists():
        raise ValueError("Migration fixture must be a new disposable directory")
    args.destination.mkdir(parents=True)
    for name in (
        "pig-music.sqlite3",
        "pig-music.sqlite3-wal",
        "pig-music.sqlite3-shm",
        "library-snapshot.json",
        "media-settings.json",
    ):
        source = args.source / name
        if source.exists():
            shutil.copyfile(source, args.destination / name)
    covers = args.source / "covers"
    if covers.is_dir():
        shutil.copytree(covers, args.destination / "covers")
    database = args.destination / "pig-music.sqlite3"
    before = rows(database)
    expected = json.loads(args.expected.read_text())
    snapshot_path = args.destination / "library-snapshot.json"
    # Metadata only: stream original tracks so large base64 maps are not loaded.
    import ijson

    with snapshot_path.open("rb") as stream:
        if stream.read(3) != b"\xef\xbb\xbf":
            stream.seek(0)
        original_tracks = list(ijson.items(stream, "tracks.item", use_float=True))
    store = Store(args.destination)
    migrated_tracks = json.loads(snapshot_path.read_text(encoding="utf-8-sig")).get(
        "tracks", []
    )
    image_fields = {"cover_sha", "cover_rescan"}
    assert [
        {k: v for k, v in t.items() if k not in image_fields} for t in original_tracks
    ] == [
        {k: v for k, v in t.items() if k not in image_fields} for t in migrated_tracks
    ], "Snapshot track metadata changed"
    assert all(
        store.covers.path(t["cover_sha"]) for t in migrated_tracks if t.get("cover_sha")
    ), "Migrated artwork missing"
    assert (
        store.summary()["stats"]["tracks"] == expected["tracks"]
    ), "Library count changed"
    assert set(expected["album_ids"]).issubset(
        {a["id"] for a in store.library["albums"]}
    ), "Existing album links changed"
    after = rows(database)
    original_documents = dict(before["documents"])
    added_images = set()
    for name, payload in after["documents"]:
        if name.startswith("image-public:") and name not in original_documents:
            value = json.loads(payload)
            assert set(value) == {"sha"} and store.covers.path(
                value["sha"]
            ), "Invalid additive display-cache pointer"
            added_images.add(name)
    # Migration 11 retires Plex sync: its table and state documents are removed.
    before.pop("plex_accounts", None)
    before["documents"] = [
        r for r in before.get("documents", []) if not retired_plex(r[0])
    ]
    for values in (before, after):
        values["documents"] = [
            r
            for r in values["documents"]
            if r[0] != "album-edition-anchors" and r[0] not in added_images
        ]
    ledger = after.pop("schema_migrations", [])
    assert [r[0] for r in ledger] == [
        1,
        2,
        3,
        4,
        5,
        6,
        7,
        8,
        9,
        10,
        11,
        12,
        13,
    ], "Unexpected migration versions"
    assert all(
        isinstance(r[1], str) and r[1] for r in ledger
    ), "Missing migration timestamps"
    original_ledger = before.pop("schema_migrations", [])
    assert set(original_ledger).issubset(
        set(ledger)
    ), "Existing migration ledger changed"
    for table in ("users", "sessions", "setup_codes", "auth_events", "login_attempts"):
        if table not in before:
            assert (
                after.pop(table, []) == []
            ), "Account foundation populated unexpected records"
    for table in (
        "library_members",
        "membership_versions",
        "play_events",
        "device_copies",
        "native_mutations",
        "user_preferences",
        "quality_preferences",
        "unassigned_tracks",
        "sync_entities",
        "sync_revisions",
        "sqlite_sequence",
        "mix_editions",
        "mix_audio",
        "mix_feedback",
        "mix_listens",
        "radio_feedback",
    ):
        if table not in before:
            assert (
                after.pop(table, []) == []
            ), "Personal foundation populated unexpected records"
    if before.get("playlists") and len(before["playlists"][0]) == 2:
        assert all(
            row[2] is None for row in after["playlists"]
        ), "Unexpected playlist ownership"
        after["playlists"] = [row[:2] for row in after["playlists"]]
    # Migration 13 gives each queue entry a random incarnation (#183): the
    # only change to an entry, a fresh 32-hex token in a new last column.
    if (
        before.get("queue")
        and after.get("queue")
        and len(after["queue"][0]) == len(before["queue"][0]) + 1
    ):
        assert all(
            isinstance(row[-1], str) and re.fullmatch(r"[0-9a-f]{32}", row[-1])
            for row in after["queue"]
        ), "Queue entries without an incarnation"
        assert len({row[-1] for row in after["queue"]}) == len(
            after["queue"]
        ), "Queue incarnations repeat"
        after["queue"] = [row[:-1] for row in after["queue"]]
    assert before == after, "Existing durable preferences, queue or membership changed"
    if args.bootstrap_owner_fixture:
        from server.auth import Auth

        attribution_before = rows(database)
        owner = Auth(store).create_owner(
            "migrationfixture", "disposable migration fixture password"
        )
        verify_owner_attribution(store, attribution_before, owner)
        print("Disposable owner attribution verified; source backup unchanged")
    print(
        "Stopped-state migration verified:",
        expected["tracks"],
        "tracks; existing links and durable rows retained",
    )


if __name__ == "__main__":
    main()
