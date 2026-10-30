import hashlib
import json
import os
import time
from pathlib import Path

import pytest
from server.store import Store
from server.exporter import Exporter
from server.media import track_file


def acquired(store, content=b"validated", identity="song"):
    path = (
        store.directory
        / "downloads"
        / "library"
        / "Artist"
        / "Album"
        / (identity + ".mp3")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    track = {
        "id": identity,
        "title": "Song",
        "artist": "Artist",
        "album_artist": "Artist",
        "album": "Album",
        "year": 2020,
        "disc_number": 1,
        "track_number": 1,
        "duration": 200,
        "codec": "mp3",
        "relative_path": path.relative_to(
            store.directory / "downloads" / "library"
        ).as_posix(),
        "sha256": hashlib.sha256(content).hexdigest(),
    }
    with store.connect() as conn:
        conn.execute("INSERT INTO acquired VALUES (?,?)", (identity, json.dumps(track)))
    store.refresh_library()
    return track, path


def test_export_commits_before_local_cleanup_and_scanning_does_not_duplicate(
    tmp_path, monkeypatch
):
    root = tmp_path / "nas"
    root.mkdir()
    monkeypatch.setenv("PIG_MUSIC_LIBRARY", str(root))
    store = Store(tmp_path / "data")
    track, source = acquired(store)
    exporter = Exporter(store, root)
    exporter.publish(track)
    assert not source.exists()
    exported = exporter.records()[0]
    target = root / exported["export_relative_path"]
    assert (
        target.read_bytes() == b"validated" and track_file(store, track["id"]) == target
    )
    exporter.publish(exported)  # Restart is idempotent after removing the local copy.
    (store.directory / "library-snapshot.json").write_text(
        json.dumps(
            {
                "tracks": [
                    {
                        **exported,
                        "id": "scanner-id",
                        "relative_path": exported["export_relative_path"],
                    }
                ]
            }
        )
    )
    store.refresh_library()
    assert store.library["stats"]["tracks"] == 1


def test_export_refuses_collision_and_tampered_source_without_removal(tmp_path):
    root = tmp_path / "nas"
    root.mkdir()
    store = Store(tmp_path / "data")
    track, source = acquired(store)
    exporter = Exporter(store, root)
    target = root / exporter.relative(track)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"existing")
    with pytest.raises(ValueError):
        exporter.publish(track)
    assert target.read_bytes() == b"existing" and source.read_bytes() == b"validated"
    target.unlink()
    source.write_bytes(b"tampered")
    with pytest.raises(ValueError):
        exporter.publish(track)
    assert source.exists() and not target.exists()


def test_export_disabled_by_default_and_failed_publication_retains_local_file(
    tmp_path, monkeypatch
):
    root = tmp_path / "nas"
    root.mkdir()
    store = Store(tmp_path / "data")
    track, source = acquired(store)
    exporter = Exporter(store, root)
    exporter.process()
    assert list(root.iterdir()) == []
    monkeypatch.setattr(
        "server.exporter.os.link",
        lambda *_: (_ for _ in ()).throw(OSError("NAS hard links unavailable")),
    )
    with pytest.raises(OSError):
        exporter.publish(track)
    assert source.read_bytes() == b"validated" and not list(root.rglob("*.pig-part"))


def test_export_requires_expected_mount_marker_and_names_fit_unicode_filesystem_limits(
    tmp_path,
):
    root = tmp_path / "nas"
    root.mkdir()
    store = Store(tmp_path / "data")
    track, source = acquired(store)
    exporter = Exporter(store, root)
    (store.directory / "library-export.json").write_text(
        json.dumps({"enabled": True, "library_id": "expected"})
    )
    exporter.process()
    assert source.exists() and list(root.iterdir()) == []
    assert store.read_document("library-export-status")["status"] == "deferred"
    long = {
        **track,
        "title": "\u97f3" * 80,
        "artist": "\u697d" * 80,
        "album_artist": "\u697d" * 80,
        "album": "\u66f2" * 80,
    }
    assert all(
        len(part.encode("utf-8")) <= 240 for part in Path(exporter.relative(long)).parts
    )


def test_nas_quality_replacement_waits_for_export_then_moves_old_file_to_recoverable_trash(
    tmp_path, monkeypatch
):
    root = tmp_path / "nas"
    root.mkdir()
    monkeypatch.setenv("PIG_MUSIC_LIBRARY", str(root))
    store = Store(tmp_path / "data")
    track, source = acquired(store)
    exporter = Exporter(store, root)
    exporter.publish(track)
    old = exporter.records()[0]
    original = root / old["export_relative_path"]
    source.write_bytes(b"new source")
    replacement = {**track, "sha256": hashlib.sha256(b"new source").hexdigest()}
    with store.connect() as conn:
        conn.execute(
            "UPDATE acquired SET payload=? WHERE id=?",
            (json.dumps(replacement), track["id"]),
        )
        conn.execute(
            "INSERT INTO quality_cleanup VALUES (?,?,?)",
            ("quality-request", track["id"], json.dumps(old)),
        )
    store.cleanup_replacements()
    assert original.exists()
    exporter.publish(replacement)
    store.cleanup_replacements()
    assert not original.exists()
    assert (
        root / ".pig-music-trash" / "quality-request" / old["export_relative_path"]
    ).read_bytes() == b"validated"
    assert track_file(store, track["id"]).read_bytes() == b"new source"


def test_existing_library_original_is_retained_until_requested_replacement_is_published(
    tmp_path, monkeypatch
):
    root = tmp_path / "nas"
    root.mkdir()
    monkeypatch.setenv("PIG_MUSIC_LIBRARY", str(root))
    original = root / "original.mp3"
    original.write_bytes(b"existing source")
    stat = original.stat()
    store = Store(tmp_path / "data")
    track = {
        "id": "original",
        "title": "Song",
        "artist": "Artist",
        "album_artist": "Artist",
        "album": "Album",
        "year": 2020,
        "disc_number": 1,
        "track_number": 1,
        "duration": 200,
        "codec": "mp3",
        "bitrate": 320,
        "relative_path": "original.mp3",
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }
    (store.directory / "library-snapshot.json").write_text(
        json.dumps({"tracks": [track]})
    )
    store.refresh_library()
    store.save_download_settings({"enabled": True, "sources": ["freemp3cloud"]})
    store.quality_override("album", store.library["albums"][0]["id"], "lossless")
    job = store.claim_request()
    store.request_state(job["id"], "validating")
    staging = store.directory / "downloads" / "staging"
    staging.mkdir(parents=True)
    source = staging / "new.flac"
    source.write_bytes(b"validated FLAC source")
    target = store.directory / "downloads" / "library" / "new.flac"
    store.publish_request(
        job,
        source,
        target,
        {"codec": "flac", "bitrate_kbps": 900, "duration": 200},
        hashlib.sha256(source.read_bytes()).hexdigest(),
    )
    assert original.exists()
    exporter = Exporter(store, root)
    exporter.publish(exporter.records()[0])
    store.cleanup_replacements()
    assert not original.exists()
    assert (
        root / ".pig-music-trash" / job["id"] / "original.mp3"
    ).read_bytes() == b"existing source"
    assert track_file(store, track["id"]).read_bytes() == b"validated FLAC source"


def test_direct_smb_storage_reuses_existing_folder_and_publishes_without_copy(
    tmp_path, monkeypatch
):
    root = tmp_path / "nas"
    root.mkdir()
    store = Store(tmp_path / "data")
    (root / ".pig-music-library.json").write_text(json.dumps({"library_id": "test"}))
    (store.directory / "library-export.json").write_text(
        json.dumps(
            {
                "enabled": True,
                "library_id": "test",
                "marker_path": ".pig-music-library.json",
            }
        )
    )
    monkeypatch.setenv("PIG_MUSIC_LIBRARY", str(root))
    monkeypatch.setenv("PIG_MUSIC_DOWNLOAD_ROOT", str(root / ".pig-music-work"))
    from server.storage import library_root

    track, _ = acquired(store)
    local = store.directory / "downloads" / "library" / track["relative_path"]
    source = library_root(store) / track["relative_path"]
    source.parent.mkdir(parents=True)
    local.replace(source)
    (store.directory / "library-snapshot.json").write_text(
        json.dumps(
            {
                "tracks": [
                    {
                        **track,
                        "id": "existing",
                        "relative_path": "Artist/Album (2020 Deluxe)/existing.mp3",
                    }
                ]
            }
        )
    )
    exporter = Exporter(store, root)
    monkeypatch.setattr(
        "server.exporter.shutil.copyfileobj",
        lambda *_: (_ for _ in ()).throw(
            AssertionError("Same-NAS publication should not copy audio")
        ),
    )
    exporter.publish(track)
    exported = exporter.records()[0]
    assert (
        exported["export_relative_path"]
        == "Artist/Album (2020 Deluxe)/Artist - Album - 01 - Song.mp3"
    )
    assert (
        track_file(store, "song").read_bytes() == b"validated" and not source.exists()
    )
    exporter.process()
    assert (
        store.read_document("library-export-status")["status"] == "completed"
    ), store.read_document("library-export-status")


def test_direct_storage_refuses_missing_mount_marker(tmp_path, monkeypatch):
    store = Store(tmp_path / "data")
    root = tmp_path / "nas"
    monkeypatch.setenv("PIG_MUSIC_LIBRARY", str(root))
    monkeypatch.setenv("PIG_MUSIC_DOWNLOAD_ROOT", str(root / ".pig-music-work"))
    from server.storage import library_root

    with pytest.raises(ValueError):
        library_root(store)
    assert not root.exists()


def nas_with(tmp_path, monkeypatch, existing):
    """A music folder with a marker, publication enabled, and `existing`
    songs (album folder, disc) as the last scan saw them."""
    root = tmp_path / "nas"
    root.mkdir()
    store = Store(tmp_path / "data")
    (root / ".pig-music-library.json").write_text(json.dumps({"library_id": "test"}))
    (store.directory / "library-export.json").write_text(
        json.dumps(
            {
                "enabled": True,
                "library_id": "test",
                "marker_path": ".pig-music-library.json",
            }
        )
    )
    monkeypatch.setenv("PIG_MUSIC_LIBRARY", str(root))
    tracks = []
    for index, (folder, disc) in enumerate(existing):
        tracks.append(
            {
                "id": f"existing-{index}",
                "title": f"Existing {index}",
                "artist": "Artist",
                "album_artist": "Artist",
                "album": "Album",
                "year": 2020,
                "track_number": index + 1,
                "relative_path": f"{folder}/{index:02} Existing.mp3",
                **({"disc_number": disc} if disc else {}),
            }
        )
    (store.directory / "library-snapshot.json").write_text(
        json.dumps({"tracks": tracks})
    )
    return store, root


def download(store, identity, disc=1, album="Album"):
    track, path = acquired(store, identity.encode(), identity)
    # Finished a minute ago: the exporter leaves files written moments ago
    # for its next pass.
    old = time.time() - 60
    os.utime(path, (old, old))
    track = {**track, "disc_number": disc, "album": album, "title": identity}
    with store.connect() as conn:
        conn.execute(
            "UPDATE acquired SET payload=? WHERE id=?", (json.dumps(track), identity)
        )
    return track


@pytest.mark.parametrize("recorded", [True, False])
def test_a_song_goes_into_the_disc_folder_of_its_disc(tmp_path, monkeypatch, recorded):
    # "Prince/Ultimate (2006)/CD 01" and "CD 02": one album, two disc folders.
    store, root = nas_with(
        tmp_path,
        monkeypatch,
        [
            ("Artist/Album (2020)/CD 01", 1 if recorded else None),
            ("Artist/Album (2020)/CD 02", 2 if recorded else None),
        ],
    )
    exporter = Exporter(store, root)
    assert exporter.relative(download(store, "first", disc=1)).startswith(
        "Artist/Album (2020)/CD 01/"
    )
    assert exporter.relative(download(store, "second", disc=2)).startswith(
        "Artist/Album (2020)/CD 02/"
    )
    # A disc the album has no folder for still needs a decision.
    with pytest.raises(ValueError, match="destination decision"):
        exporter.relative(download(store, "third", disc=3))


def test_different_album_folders_still_need_a_decision(tmp_path, monkeypatch):
    store, root = nas_with(
        tmp_path,
        monkeypatch,
        [("Artist/Album (2020)", 1), ("Artist/Album (2020 Remaster)", 1)],
    )
    with pytest.raises(ValueError, match="destination decision"):
        Exporter(store, root).relative(download(store, "song"))


def test_a_song_needing_a_decision_does_not_hold_up_the_rest(tmp_path, monkeypatch):
    store, root = nas_with(
        tmp_path,
        monkeypatch,
        [("Artist/Album (2020)", 1), ("Artist/Album (2020 Remaster)", 1)],
    )
    download(store, "stuck")
    download(store, "other", album="Other Album")
    Exporter(store, root).process()
    status = store.read_document("library-export-status")
    assert status["status"] == "partial" and status["files"] == 1
    (review,) = status["needs_review"]
    assert review["id"] == "stuck" and "destination decision" in review["error"]
    assert "1 song" in status["error"]
    published = {
        t["id"]: t.get("export_relative_path") for t in Exporter(store, root).records()
    }
    assert published["stuck"] is None
    assert (root / published["other"]).is_file()
    # Once nothing is left waiting, the status is completed again.
    with store.connect() as conn:
        conn.execute("DELETE FROM acquired WHERE id='stuck'")
    Exporter(store, root).process()
    assert store.read_document("library-export-status")["status"] == "completed"


def test_export_plan_lists_a_song_needing_a_decision_beside_the_rest(
    tmp_path, monkeypatch
):
    # Two folders for one album (not disc folders) plus a valid other album:
    # the background export publishes the valid song, and the plan shows both.
    from fastapi.testclient import TestClient
    from server.app import create_app

    store, root = nas_with(
        tmp_path,
        monkeypatch,
        [("Artist/Album (2020)", 1), ("Artist/Album (2020 Remaster)", 1)],
    )
    download(store, "stuck")
    download(store, "other", album="Other Album")
    exporter = Exporter(store, root)
    exporter.process()
    assert store.read_document("library-export-status")["status"] == "partial"
    plan = exporter.plan()
    items = {item["id"]: item for item in plan["items"]}
    assert items["stuck"]["destination"] is None
    assert "destination decision" in items["stuck"]["error"]
    assert items["other"]["exported"] and "error" not in items["other"]
    assert (root / items["other"]["destination"]).is_file()
    assert [item["id"] for item in plan["needs_review"]] == ["stuck"]
    assert plan["status"]["status"] == "partial"
    with TestClient(create_app(store.directory)) as client:
        response = client.get("/api/v1/admin/library/export-plan")
    assert response.status_code == 200
    served = {item["id"]: item for item in response.json()["items"]}
    assert "destination decision" in served["stuck"]["error"]
    assert served["other"]["exported"] is True
    assert response.json()["status"]["status"] == "partial"


def test_published_songs_are_rechecked_hourly_not_every_pass(tmp_path, monkeypatch):
    """#63/#74: every 15 s the pass re-verified every published song (about
    ten NAS checks, a lock and a database connection each): 35-40 s of work
    at ~30% of a core for 205 songs live, forever, with nothing changing."""
    store, root = nas_with(tmp_path, monkeypatch, [])
    for name in ("one", "two", "three"):
        download(store, name, album=f"Album {name}")
    exporter = Exporter(store, root)
    checked = []
    publish = exporter.publish
    monkeypatch.setattr(
        exporter, "publish", lambda track: checked.append(track["id"]) or publish(track)
    )
    exporter.process()
    assert sorted(checked) == ["one", "three", "two"]
    status = store.read_document("library-export-status")
    assert status["status"] == "completed" and status["files"] == 3
    # Next pass: nothing changed, so no published song is touched again...
    checked.clear()
    exporter.process()
    assert checked == []
    status = store.read_document("library-export-status")
    assert status["status"] == "completed" and status["files"] == 3
    # ...but a new download is published at once.
    download(store, "four", album="Album four")
    exporter.process()
    assert checked == ["four"]
    # The hourly full check verifies every published song again; one whose
    # NAS copy went missing is then retried and reported every pass.
    published = {t["id"]: t["export_relative_path"] for t in exporter.records()}
    (root / published["two"]).unlink()
    exporter.verified_at -= 3601
    checked.clear()
    exporter.process()
    assert sorted(checked) == ["four", "one", "three", "two"]
    checked.clear()
    exporter.process()
    assert checked == ["two"]
    status = store.read_document("library-export-status")
    assert status["status"] == "partial" and status["files"] == 3
    assert [r["id"] for r in status["needs_review"]] == ["two"]


def test_a_failed_cleanup_after_publishing_is_retried_every_pass(tmp_path, monkeypatch):
    """Codex round 1 on 45d0f3b: publish() records the NAS path before
    removing the download. If that removal fails, the song must be retried
    (and reported) on the next passes, not skipped until the hourly check."""
    store, root = nas_with(tmp_path, monkeypatch, [])
    download(store, "song")
    exporter = Exporter(store, root)
    source, _, _ = exporter.paths(exporter.records()[0])
    unlink = Path.unlink

    def refuse(self, *args, **kwargs):
        if self == source:
            raise PermissionError("busy")
        return unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", refuse)
    for _ in range(2):
        exporter.process()
        status = store.read_document("library-export-status")
        assert status["status"] == "partial", status
        assert [r["id"] for r in status["needs_review"]] == ["song"]
    assert exporter.records()[0].get("export_relative_path") and source.is_file()
    monkeypatch.setattr(Path, "unlink", unlink)
    exporter.process()  # Recovered on the next ordinary pass.
    assert store.read_document("library-export-status")["status"] == "completed"
    assert not source.exists()
