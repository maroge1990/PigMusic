import pytest
from fastapi.testclient import TestClient
from server.app import create_app
from server.transport import public_origin, proxy_trust


@pytest.mark.parametrize(
    "value",
    [
        "http://private.ts.net",
        "https://user:password@private.ts.net",
        "https://private.ts.net/path",
        "https://private.ts.net?token=x",
        "https://private.ts.net#fragment",
    ],
)
def test_public_origin_rejects_unsafe_configuration(monkeypatch, value):
    monkeypatch.setenv("PIG_MUSIC_PUBLIC_ORIGIN", value)
    with pytest.raises(ValueError):
        public_origin()


def test_https_origin_and_health_preserve_lan_transition(tmp_path, monkeypatch):
    monkeypatch.setenv("PIG_MUSIC_PUBLIC_ORIGIN", "https://private.ts.net")
    with TestClient(create_app(tmp_path)) as client:
        assert client.get("/api/health").json()["https"] is False
        assert (
            client.get("/api/health", headers={"X-Forwarded-Proto": "https"}).json()[
                "https"
            ]
            is False
        )
        assert (
            client.post(
                "/does-not-exist", headers={"Origin": "https://private.ts.net"}
            ).status_code
            == 404
        )
        assert (
            client.post(
                "/does-not-exist", headers={"Origin": "http://testserver"}
            ).status_code
            == 404
        )
        assert (
            client.post(
                "/does-not-exist", headers={"Origin": "https://evil.example"}
            ).status_code
            == 403
        )
    with TestClient(create_app(tmp_path), base_url="https://private.ts.net") as client:
        assert client.get("/api/health").json()["https"] is True


@pytest.mark.parametrize("value", ["*", "0.0.0.0/0", "10.0.0.0/24", "localhost", ""])
def test_proxy_trust_rejects_wildcard_and_broad_networks(monkeypatch, value):
    monkeypatch.setenv("PIG_MUSIC_TRUSTED_PROXIES", value)
    with pytest.raises(ValueError):
        proxy_trust()


def test_proxy_trust_accepts_exact_private_proxy(monkeypatch):
    monkeypatch.setenv("PIG_MUSIC_TRUSTED_PROXIES", "127.0.0.1,10.0.0.2")
    assert proxy_trust() == "127.0.0.1,10.0.0.2"


def test_trusted_tunnel_proxy_reveals_the_visitor_not_a_spoofed_forwarded_address(
    monkeypatch,
):
    """Login rate limits key on request.client.host. Behind a tunnel, Uvicorn must
    take the address the trusted proxy appended, never one the visitor supplied."""
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    monkeypatch.setenv("PIG_MUSIC_TRUSTED_PROXIES", "127.0.0.1,172.25.0.9")
    seen = []

    async def app(scope, receive, send):
        seen.append(scope["client"][0])
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def request(peer, forwarded):
        scope = {
            "type": "http",
            "scheme": "http",
            "client": (peer, 50000),
            "headers": [(b"x-forwarded-for", forwarded.encode())],
        }

        async def receive():
            return {"type": "http.request"}

        async def send(_):
            pass

        await ProxyHeadersMiddleware(app, trusted_hosts=proxy_trust())(
            scope, receive, send
        )

    import asyncio

    asyncio.run(request("172.25.0.9", "6.6.6.6, 203.0.113.7"))
    asyncio.run(request("198.51.100.4", "203.0.113.7"))
    assert seen == ["203.0.113.7", "198.51.100.4"]


def test_framework_docs_and_schema_are_not_public(tmp_path):
    with TestClient(create_app(tmp_path), base_url="https://private.ts.net") as client:
        for path in ("/docs", "/redoc", "/openapi.json"):
            response = client.get(path)
            assert response.status_code != 200 or "openapi" not in response.text
