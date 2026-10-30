"""The server's test-only switch for the T3 rig (#15): off unless set, never
forwards provider credentials, accepts only the rig's fake."""

import asyncio

import httpx
import pytest

from server import test_services


def test_the_switch_is_off_unless_the_rig_sets_it(monkeypatch):
    monkeypatch.delenv("PIG_MUSIC_TEST_SERVICES", raising=False)
    assert test_services.transport() is None


def test_provider_credentials_never_reach_the_fake_and_only_the_rig_is_accepted():
    """Codex round 2: the switch forwarded the ListenBrainz token and Fanart
    key to whatever address it named; a mistaken setting would leak them."""
    seen = []

    class Inner(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            seen.append(request)
            return httpx.Response(200, json={})

    async def run():
        transport = test_services.FakeServices(
            "http://pig-music-test-fake:8790", inner=Inner()
        )
        async with httpx.AsyncClient(transport=transport) as client:
            await client.get(
                "https://api.listenbrainz.org/1/x",
                headers={"Authorization": "Token secret", "api-key": "secret"},
                cookies={"session": "secret"},
            )

    asyncio.run(run())
    assert "secret" not in str(seen[0].headers) and seen[0].url.host == (
        "pig-music-test-fake"
    )
    for wrong in ("https://example.com", "http://198.51.100.10:8790", "file:///x"):
        with pytest.raises(ValueError):
            test_services.FakeServices(wrong)
