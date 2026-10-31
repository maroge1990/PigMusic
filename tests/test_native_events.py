from types import SimpleNamespace
import pytest
import asyncio
from fastapi import HTTPException
from server.routes import native
from server.routes.native import native_router
from test_native_sync import fixture
from server.auth import Auth


def test_scoped_events_bound_connections_and_revoke(tmp_path):
    asyncio.run(events_journey(tmp_path))


async def events_journey(tmp_path):
    store, owner, member = fixture(tmp_path)
    auth = Auth(store)
    token = auth.login(
        "member", "native fixture password", "device", "fixture", "ios", "fixture"
    )["token"]
    user, session = auth.authenticate(token)
    router = native_router(store, auth)
    endpoint = next(
        route.endpoint for route in router.routes if route.path == "/api/v1/events"
    )

    async def connected():
        return False

    request = SimpleNamespace(
        state=SimpleNamespace(user=user, session=session), is_disconnected=connected
    )
    responses = [await endpoint(request) for _ in range(4)]
    with pytest.raises(HTTPException) as failure:
        await endpoint(request)
    assert failure.value.status_code == 429
    for response in responses:
        message = await anext(response.body_iterator)
        assert message.startswith("event: revision")
    response = responses[0]
    queue = await anext(response.body_iterator)
    assert '"items":[]' in queue and "event: queue" in queue
    await anext(response.body_iterator)
    auth.revoke(member, session["public_id"])
    assert "event: revoked" in await anext(response.body_iterator)
    for response in responses:
        await response.body_iterator.aclose()
    # Closing a stream releases its reserved connection slot.
    final = await endpoint(request)
    assert "event: revoked" in await anext(final.body_iterator)
    await final.body_iterator.aclose()


def test_only_the_owner_receives_the_admin_event(tmp_path, monkeypatch):
    # The member's fourth message is the keepalive comment, which proves no
    # admin event followed; at the real 25 s interval this test took 26 s.
    monkeypatch.setattr(native, "KEEPALIVE_SECONDS", 0)
    asyncio.run(admin_journey(tmp_path))


async def admin_journey(tmp_path):
    store, owner, member = fixture(tmp_path)
    auth = Auth(store)
    calls = []

    def admin_snapshot():
        calls.append(1)
        return {"queue": {"items": [{"id": "q1"}]}, "scan": {"status": "scanning"}}

    def health():
        return {
            "storage_available": True,
            "soulseek": {"configured": True, "connected": False},
        }

    router = native_router(store, auth, health, admin_snapshot)
    endpoint = next(
        route.endpoint for route in router.routes if route.path == "/api/v1/events"
    )

    async def connected():
        return False

    async def messages(username, password, admin=True, count=4):
        token = auth.login(username, password, "device", "fixture", "ios", "f")["token"]
        user, session = auth.authenticate(token)
        request = SimpleNamespace(
            state=SimpleNamespace(user=user, session=session), is_disconnected=connected
        )
        response = await endpoint(request, admin=admin)
        found = [await anext(response.body_iterator) for _ in range(count)]
        await response.body_iterator.aclose()
        return found

    member_messages = await messages("member", "native fixture password")
    assert not any("event: admin" in m for m in member_messages)
    assert not any("soulseek" in m for m in member_messages)
    assert calls == []
    # The owner's iPhone (no Admin) does not subscribe and costs nothing.
    native = await messages("mark", "native fixture password", admin=False, count=3)
    assert not any("event: admin" in m for m in native)
    assert calls == []
    owner_messages = await messages("mark", "native fixture password")
    admin = next(m for m in owner_messages if m.startswith("event: admin"))
    assert '"q1"' in admin and '"scanning"' in admin
    assert '"soulseek":{"configured":true,"connected":false}' in admin
    assert calls == [1]
    # A second Admin page within the interval shares the same snapshot.
    await messages("mark", "native fixture password")
    assert calls == [1]
