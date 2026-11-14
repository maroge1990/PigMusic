"""The browser's real API surface, exercised with web sessions instead of the
legacy unauthenticated fixture mode."""

import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from server.security import api_routes
from source import SOURCE
from test_security import PASSWORD, protected

CLIENT = SOURCE / "client/src"
WRITE = {"X-Pig-Request": "1"}

# API paths in calls such as api("/v1/x/" + id + "/y") or apiFetch(`/api/${a}`).
CALL = re.compile(r"""\b(api|apiFetch)(?:<[^()]*?>)?\(\s*(["'`])(.*?)\2""", re.S)
JOIN = re.compile(r"""\s*\+\s*(?:(["'`])(.*?)\1|[\w.!]+(?:\([^()]*\))?)""", re.S)


def client_paths():
    for source in CLIENT.glob("*.ts*"):
        text = source.read_text()
        for call in CALL.finditer(text):
            kind, literal, position = call.group(1), call.group(3), call.end()
            while join := JOIN.match(text, position):
                literal += join.group(2) if join.group(1) else "X"
                position = join.end()
            path = re.sub(r"\$\{[^}]*\}", "X", literal).split("?")[0]
            if kind == "api":
                path = "/api" + path
            if path.startswith("/api/"):
                yield source.name, path


def route_paths(app):
    return [route.path.split("/") for route in api_routes(app)]


def segment_matches(client, route):
    if route.startswith("{"):
        return True
    # X marks a runtime value in the client; it may fill any segment text.
    pattern = ".*".join(map(re.escape, client.split("X")))
    return re.fullmatch(pattern, route) is not None


def matches(client_path, routes):
    parts = client_path.split("/")
    return any(
        len(parts) == len(route) and all(map(segment_matches, parts, route))
        for route in routes
    )


def test_every_client_api_path_exists(tmp_path, monkeypatch):
    app, _ = protected(tmp_path, monkeypatch)
    routes = route_paths(app)
    found = list(client_paths())
    assert len(found) > 40, "the scanner should see the client's API calls"
    missing = [(name, path) for name, path in found if not matches(path, routes)]
    assert not missing, missing


def sign_in(client, username):
    client.cookies.clear()
    response = client.post(
        "/api/v1/auth/login",
        headers=WRITE,
        json={"username": username, "password": PASSWORD},
    )
    assert response.status_code == 200, response.text


def household(tmp_path, monkeypatch):
    track = {
        "id": "song",
        "title": "One",
        "artist": "Artist",
        "album_artist": "Artist",
        "album": "Studio",
        "duration": 200,
        "relative_path": "song.mp3",
    }
    (tmp_path / "library-snapshot.json").write_text(json.dumps({"tracks": [track]}))
    app, owner = protected(tmp_path, monkeypatch)
    code = app.state.auth.setup_code(owner, "member", "Member")
    app.state.auth.activate(code["code"], PASSWORD, "fixture")
    return app


# Reads behind the Admin section; members must be refused every one.
ADMIN_READS = [
    "/api/v1/admin/library",
    "/api/v1/admin/migration",
    "/api/v1/admin/queue",
    "/api/v1/admin/settings/downloads",
    "/api/v1/admin/settings/soulseek",
    "/api/v1/admin/access",
    "/api/v1/admin/settings/media",
    "/api/v1/admin/library/trash",
    "/api/v1/admin/library/trash/purge?days=30",
    "/api/v1/admin/library/tidy",
    "/api/v1/people",
    "/api/v1/people/activity",
    "/api/v1/admin/unassigned",
]
PERSONAL_READS = [
    "/api/v1/me",
    "/api/v1/library",
    "/api/v1/library-pages/tracks?offset=0&limit=100",
    "/api/v1/playlists",
    "/api/v1/quality",
    "/api/v1/me/sessions",
]


def test_admin_section_is_owner_only_through_web_sessions(tmp_path, monkeypatch):
    app = household(tmp_path, monkeypatch)
    media = {"portraits": True, "remote_playback": False}
    with TestClient(app, base_url="https://private.ts.net") as client:
        sign_in(client, "mark")
        for path in ADMIN_READS:
            assert client.get(path).status_code == 200, path
        settings = client.get("/api/v1/admin/settings/downloads").json()
        assert (
            client.put("/api/v1/admin/settings/downloads", headers=WRITE, json=settings)
        ).status_code == 200
        assert (
            client.put("/api/v1/admin/settings/media", headers=WRITE, json=media)
        ).status_code == 200
        # Browser writes without the request header are refused for everyone.
        assert client.put("/api/v1/admin/settings/media", json=media).status_code == 403
        sign_in(client, "member")
        for path in ADMIN_READS:
            assert client.get(path).status_code == 403, path
        assert (
            client.put("/api/v1/admin/settings/media", headers=WRITE, json=media)
        ).status_code == 403


def test_shared_sections_work_for_both_people(tmp_path, monkeypatch):
    app = household(tmp_path, monkeypatch)
    with TestClient(app, base_url="https://private.ts.net") as client:
        for username in ("mark", "member"):
            sign_in(client, username)
            for path in PERSONAL_READS:
                assert client.get(path).status_code == 200, (username, path)
            created = client.post(
                "/api/v1/playlists", headers=WRITE, json={"name": "Road trip"}
            )
            assert created.status_code == 200, created.text
            listed = client.get("/api/v1/playlists").json()["playlists"]
            assert "Road trip" in [p["name"] for p in listed]


def test_a_stale_native_edit_answers_conflict_not_bad_request(tmp_path, monkeypatch):
    # The phone's outbox keeps a 409 for review ("changed on the server") but
    # marks a 400 as an edit the server could not apply (#176's contract test).
    import uuid

    app = household(tmp_path, monkeypatch)
    with TestClient(app, base_url="https://private.ts.net") as client:
        sign_in(client, "member")

        def mutate(action, **values):
            body = {"operation_id": str(uuid.uuid4()), "action": action, **values}
            return client.post("/api/v1/mutations", headers=WRITE, json=body)

        created = mutate("create", name="Offline")
        assert created.status_code == 200, created.text
        playlist = created.json()["playlist_id"]
        renamed = mutate("rename", playlist_id=playlist, revision=1, name="Fresh")
        assert renamed.status_code == 200, renamed.text
        stale = mutate("rename", playlist_id=playlist, revision=1, name="Stale")
        assert stale.status_code == 409, stale.text
        invalid = mutate("rename", playlist_id=playlist, revision=2, name=" ")
        assert invalid.status_code == 400, invalid.text


def test_a_member_reports_first_sound_like_the_owner(tmp_path, monkeypatch):
    # #191: the counters take every player's starts; a member's web player
    # and phone were refused with owner_only.
    app = household(tmp_path, monkeypatch)
    with TestClient(app, base_url="https://private.ts.net") as client:
        for username in ("member", "mark"):
            sign_in(client, username)
            heard = client.post(
                "/api/v1/performance/first-sound",
                headers=WRITE,
                json={"ms": 900, "source": "library", "action": "play"},
            )
            assert heard.status_code == 204, (username, heard.text)
        sign_in(client, "member")
        assert client.get("/api/v1/admin/performance").status_code == 403


def test_admin_access_reports_public_origin_to_owner_only(tmp_path, monkeypatch):
    monkeypatch.setenv("PIG_MUSIC_PUBLIC_ORIGIN", "https://music.example.test")
    app = household(tmp_path, monkeypatch)
    with TestClient(app, base_url="https://music.example.test") as client:
        assert client.get("/api/v1/admin/access").status_code == 401
        sign_in(client, "mark")
        assert client.get("/api/v1/admin/access").json() == {
            "public_origin": "https://music.example.test"
        }
        sign_in(client, "member")
        assert client.get("/api/v1/admin/access").status_code == 403


def test_admin_access_without_configured_origin(tmp_path, monkeypatch):
    monkeypatch.delenv("PIG_MUSIC_PUBLIC_ORIGIN", raising=False)
    app = household(tmp_path, monkeypatch)
    with TestClient(app, base_url="https://private.ts.net") as client:
        sign_in(client, "mark")
        assert client.get("/api/v1/admin/access").json() == {"public_origin": None}


def test_interface_uses_the_system_typeface_and_a_static_sign_in():
    base = (CLIENT / "Base.css").read_text()
    stack = re.search(r"font-family:([^;]+);", base).group(1)
    assert "Inter" not in stack
    assert stack.split(",")[0].strip() == "system-ui"
    shell = (CLIENT / "AppShell.tsx").read_text()
    assert shell.count("pig-splash") == 1
    assert shell.index("pig-splash") < shell.index("if (!user && firstRun)")
    assert "pig-splash" not in (CLIENT / "SignIn.tsx").read_text()


def test_trash_purge_needs_the_reviewed_list(tmp_path, monkeypatch):
    app = household(tmp_path, monkeypatch)
    with TestClient(app, base_url="https://private.ts.net") as client:
        sign_in(client, "mark")
        plan = client.get("/api/v1/admin/library/trash/purge?days=30").json()
        assert plan["items"] == [] and plan["days"] == 30
        purge = "/api/v1/admin/library/trash/purge"
        wrong = {"days": 30, "fingerprint": "0" * 64}
        assert client.post(purge, headers=WRITE, json=wrong).status_code == 409
        right = {"days": 30, "fingerprint": plan["fingerprint"]}
        reply = client.post(purge, headers=WRITE, json=right)
        assert reply.json() == {"purged": 0, "kept": 0}
        sign_in(client, "member")
        assert client.post(purge, headers=WRITE, json=right).status_code == 403
