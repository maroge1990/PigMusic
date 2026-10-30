import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.auth import AuthError
from server.request_limits import JsonBodyLimit

PASSWORD = "security hardening fixture"


def fixture(tmp_path, monkeypatch):
    monkeypatch.delenv("PIG_MUSIC_TEST_LEGACY_API", raising=False)
    monkeypatch.setenv("PIG_MUSIC_LIBRARY", str(tmp_path / "nas"))
    app = create_app(tmp_path)
    owner = app.state.auth.create_owner("mark", PASSWORD)
    bearer = app.state.auth.login(
        "mark", PASSWORD, "device", "fixture", "ios", "fixture"
    )["token"]
    return app, owner, bearer


def test_headers_errors_and_secret_validation(tmp_path, monkeypatch):
    app, _, bearer = fixture(tmp_path, monkeypatch)
    with TestClient(
        app,
        base_url="https://private.ts.net",
        headers={"Authorization": "Bearer " + bearer},
    ) as client:
        response = client.get("/api/health")
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        assert "script-src 'self'" in response.headers["content-security-policy"]
        assert response.headers["strict-transport-security"] == "max-age=31536000"
        assert response.headers["cross-origin-opener-policy"] == "same-origin"
        secret = "fixture secret " * 100
        for path, body in [
            ("/api/v1/me/password", {"current_password": secret, "password": secret})
        ]:
            response = client.post(path, json=body)
            assert response.status_code == 422
            assert secret not in response.text
            assert set(response.json()) == {"detail", "code"}
        response = client.post(
            "/api/v1/requests",
            content=b"x" * (1024 * 1024 + 1),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 413
        assert response.json()["code"] == "request_too_large"
        # Other JSON spellings and content types are bounded too.
        for content_type in ["application/vnd.api+json", "text/plain"]:
            response = client.post(
                "/api/v1/requests",
                content=b"x" * (1024 * 1024 + 1),
                headers={"Content-Type": content_type},
            )
            assert response.status_code == 413
    with TestClient(app, base_url="http://localhost") as client:
        assert "strict-transport-security" not in client.get("/api/health").headers


def test_chunked_body_is_bounded_before_json_parser():
    called, replies = [], []
    messages = iter(
        [
            {"type": "http.request", "body": b"abc", "more_body": True},
            {"type": "http.request", "body": b"def", "more_body": False},
        ]
    )

    async def receive():
        return next(messages)

    async def send(message):
        replies.append(message)

    async def inner(*args):
        called.append(True)

    asyncio.run(
        JsonBodyLimit(inner, 5)(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/v1/requests",
                "headers": [],
            },
            receive,
            send,
        )
    )
    assert not called
    assert replies[0]["status"] == 413


def test_disable_immediately_revokes_devices_and_setup_codes(tmp_path, monkeypatch):
    app, owner, bearer = fixture(tmp_path, monkeypatch)
    auth = app.state.auth
    setup = auth.setup_code(owner, "member", "Member")
    member = auth.activate(setup["code"], PASSWORD, "fixture")["id"]
    device = auth.login("member", PASSWORD, "device", "phone", "ios", "fixture")[
        "token"
    ]
    with TestClient(
        app,
        base_url="https://private.ts.net",
        headers={"Authorization": "Bearer " + bearer},
    ) as client:
        assert (
            client.put(
                f"/api/v1/people/{owner}/disabled", json={"disabled": True}
            ).status_code
            == 409
        )
        assert (
            client.put(
                f"/api/v1/people/{member}/disabled", json={"disabled": True}
            ).status_code
            == 200
        )
        assert (
            client.get(
                "/api/v1/me", headers={"Authorization": "Bearer " + device}
            ).status_code
            == 401
        )
        assert (
            client.put(
                f"/api/v1/people/{member}/disabled", json={"disabled": False}
            ).status_code
            == 200
        )
        assert (
            client.get(
                "/api/v1/me", headers={"Authorization": "Bearer " + device}
            ).status_code
            == 401
        )
        assert "account_disabled" in json.dumps(
            client.get("/api/v1/people/activity").json()
        )
    pending = auth.setup_code(owner, "newmember", "New Member")
    auth.disable(owner, pending["user_id"], True)
    auth.disable(owner, pending["user_id"], False)
    with pytest.raises(AuthError):
        auth.activate(pending["code"], PASSWORD, "fixture")


def test_a_renewed_sign_in_cookie_never_rides_a_shared_cacheable_response(
    tmp_path, monkeypatch
):
    """Audit S1: a web session over an hour old renews its cookie on the next
    request; artwork was `public` for a year, so a shared cache could have
    stored that cookie and served it to someone else."""
    import io

    from PIL import Image

    app, _, _ = fixture(tmp_path, monkeypatch)
    stream = io.BytesIO()
    Image.new("RGB", (64, 64), "purple").save(stream, "PNG")
    cover = "/api/v1/covers/" + app.state.store.covers.save(stream.getvalue()) + ".jpg"

    def age_sessions():
        with app.state.store.connect() as conn:
            conn.execute("UPDATE sessions SET last_seen_at=last_seen_at-7200")

    with TestClient(app, base_url="https://private.ts.net") as client:
        signed_in = client.post(
            "/api/v1/auth/login",
            json={"username": "mark", "password": PASSWORD},
            headers={"X-Pig-Request": "1"},
        )
        assert signed_in.status_code == 200
        for path in (cover, cover + "?w=128", cover + "/metadata"):
            age_sessions()
            response = client.get(path)
            assert response.status_code == 200, (path, response.text)
            directives = {
                d.strip().split("=")[0]
                for d in response.headers["cache-control"].split(",")
            }
            assert "public" not in directives, (path, directives)
            assert directives & {"private", "no-store"}, (path, directives)
            assert "set-cookie" in response.headers, path
