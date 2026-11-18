import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from server.auth import Auth
from server.personal_removal import PersonalRemoval, RemovalChanged
from server.playlists import Playlists
from server.recovery import Recovery
from test_recovery import setup
from test_security import PASSWORD


def household(tmp_path, monkeypatch):
    store, root, path, track = setup(tmp_path, monkeypatch)
    auth = Auth(store)
    owner = auth.create_owner("mark", PASSWORD)
    code = auth.setup_code(owner, "member", "Member")
    auth.activate(code["code"], PASSWORD, "fixture")
    with store.connect() as conn:
        member = conn.execute(
            "SELECT id FROM users WHERE username='member'"
        ).fetchone()[0]
    removal = PersonalRemoval(store, Recovery(store))
    return store, root, path, track, owner, member, removal


def remove(removal, user, ids):
    preview = removal.preview(user, ids)
    return removal.remove(user, ids, preview["confirmation"])


def test_shared_then_final_removal_and_private_undo(tmp_path, monkeypatch):
    store, root, path, track, owner, member, removal = household(tmp_path, monkeypatch)
    store.user_libraries.add(member, ["saved"])
    preview = removal.preview(owner, ["saved"])
    assert preview["delete_count"] == 0 and preview["items"][0]["kept_for"] == [
        "Member"
    ]
    first = remove(removal, owner, ["saved"])
    assert path.exists() and not store.user_libraries.contains(owner, "saved")
    assert store.user_libraries.contains(member, "saved")
    second = remove(removal, member, ["saved"])
    assert not path.exists() and not store.library["albums"]
    with pytest.raises(LookupError):
        removal.undo(owner, second["id"])
    removal.undo(member, second["id"])
    assert path.read_bytes() == b"original music"
    assert store.user_libraries.contains(member, "saved")
    removal.undo(owner, first["id"])
    assert store.user_libraries.contains(owner, "saved")
    assert removal.undo(owner, first["id"]) == {"restored": 0}


def test_confirmation_is_for_its_own_member_only(tmp_path, monkeypatch):
    # Codex (#248): two members who own the same song got the same token, so
    # one member's removal could be sent with the other's preview.
    store, _, path, track, owner, member, removal = household(tmp_path, monkeypatch)
    store.user_libraries.add(member, ["saved"])
    mine = removal.preview(owner, ["saved"])["confirmation"]
    assert removal.preview(member, ["saved"])["confirmation"] != mine
    with pytest.raises(RemovalChanged):
        removal.remove(member, ["saved"], mine)
    assert store.user_libraries.contains(member, "saved")


def test_playlist_does_not_retain_file_and_owner_restore_restores_membership(
    tmp_path, monkeypatch
):
    store, _, path, track, owner, member, removal = household(tmp_path, monkeypatch)
    playlist = Playlists(store, owner).create("Keep metadata")
    Playlists(store, owner).add(playlist["id"], track)
    result = remove(removal, owner, ["saved"])
    assert result["deleted"] == 1 and not path.exists()
    assert (
        Playlists(store, owner).detail(playlist["id"])["entries"][0]["status"]
        != "downloaded"
    )
    recovery_id = store.read_document("library-removal:" + result["id"])[
        "recovery_ids"
    ][0]
    removal.recovery.restore(recovery_id)
    assert store.user_libraries.contains(owner, "saved") and path.exists()


def test_pending_other_request_preserves_file_and_stale_confirmation(
    tmp_path, monkeypatch
):
    store, _, path, track, owner, member, removal = household(tmp_path, monkeypatch)
    preview = removal.preview(owner, ["saved"])
    store.submit_catalogue([{**track, "status": "missing"}], user_id=member)
    with pytest.raises(RemovalChanged):
        removal.remove(owner, ["saved"], preview["confirmation"])
    result = remove(removal, owner, ["saved"])
    assert result["kept"] == 1 and path.exists()
    assert store.queue()["items"][0]["status"] == "waiting_for_sources"


def test_partial_album_removal_undo_expiry_and_cancelled_work(tmp_path, monkeypatch):
    store, root, path, track, owner, member, removal = household(tmp_path, monkeypatch)
    second_path = root / "second.mp3"
    second_path.write_bytes(b"second music")
    second = {**track, "id": "second", "title": "Second", "relative_path": "second.mp3"}
    (store.directory / "library-snapshot.json").write_text(
        json.dumps({"tracks": [track, second]})
    )
    store.refresh_library()
    store.user_libraries.add(owner, ["second"])
    store.user_libraries.add(member, ["saved"])
    store.submit_catalogue([{**second, "status": "missing"}], user_id=owner)
    result = remove(removal, owner, ["saved", "second"])
    assert result["deleted"] == 1 and result["kept"] == 1
    assert path.exists() and not second_path.exists()
    assert store.queue()["items"][0]["status"] == "cancelled"
    removal.clock = lambda: result["undo_until"] + 1
    with pytest.raises(ValueError, match="expired"):
        removal.undo(owner, result["id"])
    assert not second_path.exists()


def test_add_remove_race_never_deletes_a_members_file(tmp_path, monkeypatch):
    store, _, path, _, owner, member, removal = household(tmp_path, monkeypatch)
    preview = removal.preview(owner, ["saved"])

    def add():
        try:
            store.user_libraries.add(member, ["saved"])
        except ValueError:
            pass  # Removal committed first: callers must re-request the missing file.

    def delete():
        try:
            removal.remove(owner, ["saved"], preview["confirmation"])
        except RemovalChanged:
            pass  # Add committed first: a fresh preview must show the other member.

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda fn: fn(), (add, delete)))
    assert not store.user_libraries.contains(member, "saved") or path.exists()


def test_interrupted_removal_blocks_add_and_is_recovered(tmp_path, monkeypatch):
    store, root, path, _, owner, member, removal = household(tmp_path, monkeypatch)
    original = removal.recovery.finish
    monkeypatch.setattr(
        removal.recovery,
        "finish",
        lambda *args: (_ for _ in ()).throw(OSError("interrupted")),
    )
    with pytest.raises(OSError):
        remove(removal, owner, ["saved"])
    assert path.exists()
    with pytest.raises(ValueError):
        store.user_libraries.add(member, ["saved"])
    monkeypatch.setattr(removal.recovery, "finish", original)
    removal.recovery.recover()
    assert not path.exists()
    record = removal.recovery.list()[0]
    removal.recovery.restore(record["id"])
    assert path.exists() and store.user_libraries.contains(owner, "saved")


def test_removal_detaches_caller_from_shared_pending_request(tmp_path, monkeypatch):
    store, _, path, track, owner, member, removal = household(tmp_path, monkeypatch)
    store.submit_catalogue([{**track, "status": "missing"}], user_id=owner)
    store.submit_catalogue([{**track, "status": "missing"}], user_id=member)
    result = remove(removal, owner, ["saved"])
    job = store.queue()["items"][0]
    assert result["kept"] == 1 and path.exists()
    assert job["requested_by"] == [member]
    with store.connect() as conn:
        store._grant_request_membership(conn, json.dumps(job), "saved")
    assert store.user_libraries.contains(member, "saved")
    assert not store.user_libraries.contains(owner, "saved")


def test_changed_mount_or_file_rejects_removal_without_membership_loss(
    tmp_path, monkeypatch
):
    store, root, path, _, owner, member, removal = household(tmp_path, monkeypatch)
    preview = removal.preview(owner, ["saved"])
    path.write_bytes(b"changed music")
    with pytest.raises(RemovalChanged):
        removal.remove(owner, ["saved"], preview["confirmation"])
    (store.directory / "library-export.json").write_text(
        json.dumps({"library_id": "expected"})
    )
    preview = removal.preview(owner, ["saved"])
    with pytest.raises(ValueError, match="mount"):
        removal.remove(owner, ["saved"], preview["confirmation"])
    assert path.exists() and store.user_libraries.contains(owner, "saved")
    assert removal.recovery.list() == []


def test_member_api_preview_remove_undo_omits_private_paths(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from server.app import create_app

    store, _, path, _, owner, member, _ = household(tmp_path, monkeypatch)
    monkeypatch.delenv("PIG_MUSIC_TEST_LEGACY_API", raising=False)
    app = create_app(store.directory)
    token = app.state.auth.login(
        "member", PASSWORD, "device", "phone", "ios", "fixture"
    )["token"]
    with TestClient(
        app,
        base_url="https://private.ts.net",
        headers={"Authorization": "Bearer " + token},
    ) as client:
        body = {"track_ids": ["saved"]}
        assert (
            client.post("/api/v1/library/removal-preview", json=body).status_code == 404
        )
        app.state.store.user_libraries.add(member, ["saved"])
        response = client.post("/api/v1/library/removal-preview", json=body)
        assert response.status_code == 200
        assert (
            "relative_path" not in response.text and str(tmp_path) not in response.text
        )
        result = client.post(
            "/api/v1/library/remove",
            json={**body, "confirmation": response.json()["confirmation"]},
        )
        assert result.status_code == 200 and path.exists()
        assert str(tmp_path) not in result.text
        assert client.post("/api/v1/library/removals/unknown/undo").status_code == 404
        assert (
            client.post(
                "/api/v1/library/removals/" + result.json()["id"] + "/undo"
            ).status_code
            == 200
        )


def test_cancelled_publication_cannot_recreate_removed_membership(
    tmp_path, monkeypatch
):
    import hashlib
    from server.storage import library_root

    store, _, path, track, owner, _, removal = household(tmp_path, monkeypatch)
    store.submit_catalogue([{**track, "status": "missing"}], user_id=owner)
    store.save_download_settings({"enabled": True})
    job = store.claim_request()
    job["library_track_id"] = "saved"
    with store.connect() as conn:
        conn.execute(
            "UPDATE queue SET status='validating',payload=? WHERE id=?",
            (json.dumps(job), job["id"]),
        )
    remove(removal, owner, ["saved"])
    source = store.directory / "candidate.mp3"
    source.write_bytes(b"replacement")
    (store.directory / "downloads" / "staging").mkdir(parents=True, exist_ok=True)
    target = library_root(store) / "replacement.mp3"
    store.publish_request(
        job,
        source,
        target,
        {"codec": "mp3", "bitrate_kbps": 320, "duration": 100},
        hashlib.sha256(source.read_bytes()).hexdigest(),
    )
    assert not target.exists() and not path.exists()
    assert not store.user_libraries.contains(owner, "saved")


def test_duplicate_physical_aliases_require_review_before_mutation(
    tmp_path, monkeypatch
):
    store, _, path, track, owner, _, removal = household(tmp_path, monkeypatch)
    alias = {**track, "id": "alias", "title": "Alias"}
    (store.directory / "library-snapshot.json").write_text(
        json.dumps({"tracks": [track, alias]})
    )
    store.refresh_library()
    store.user_libraries.add(owner, ["alias"])
    with pytest.raises(ValueError, match="share a file"):
        remove(removal, owner, ["saved", "alias"])
    with pytest.raises(ValueError, match="share a file"):
        remove(removal, owner, ["saved"])
    assert path.exists() and store.user_libraries.contains(owner, "saved")
    assert store.user_libraries.contains(owner, "alias")
    assert removal.recovery.list() == []
