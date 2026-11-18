"""Codex round 3 (#250): the web client sends every write naming the session
it was started under, except writes the server takes before anyone signs in
(activating an invite or a password reset, signing in, the first owner).
Those are listed once for the client in client/src/publicWrites.ts; this
test walks every write route the server has, asks each without signing
in, and fails if the client's list and the server's open routes differ, so
a new public route can't be missed."""

import re

from fastapi.testclient import TestClient
import pytest

from server.app import create_app
from server.security import api_routes
from source import SOURCE

CLIENT_LIST = SOURCE / "client" / "src" / "publicWrites.ts"
WRITES = {"POST", "PUT", "PATCH", "DELETE"}


def client_public_writes():
    if not CLIENT_LIST.is_file():
        pytest.skip("the web client is not part of this checkout")
    text = CLIENT_LIST.read_text()
    return set(re.findall(r'"((?:POST|PUT|PATCH|DELETE) /api/[^"]+)"', text))


def test_the_client_knows_every_write_the_server_takes_signed_out(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("PIG_MUSIC_TEST_LEGACY_API", raising=False)
    monkeypatch.setenv("PIG_MUSIC_LIBRARY", str(tmp_path / "nas"))
    app = create_app(tmp_path)
    app.state.auth.create_owner("mark", "public writes fixture password")
    open_writes = set()
    with TestClient(
        app, base_url="https://private.ts.net", headers={"X-Pig-Request": "1"}
    ) as client:
        for route in api_routes(app):
            path = route.path
            for name in route.param_convertors:
                path = path.replace("{" + name + "}", "fixture")
            for method in sorted(route.methods & WRITES):
                answer = client.request(method, path, json={})
                # Anything but "sign in first" means the route is open.
                if answer.status_code != 401:
                    open_writes.add(f"{method} {route.path}")
    assert open_writes, "no public write found: the walk is broken"
    assert client_public_writes() == open_writes
