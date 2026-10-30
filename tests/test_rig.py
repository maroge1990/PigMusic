"""The T3 rig's fake services and the server's test-only switch (#15)."""

import asyncio
import threading
from http.server import ThreadingHTTPServer

import httpx
import pytest

from scripts import fake_services
from server import test_services
from server.media import save_settings
from server.popularity import Popularity
from server.store import Store

ARTIST = "70248960-cb53-4ea4-943a-edb18f7d336f"
TOKEN = "00000000-0000-4000-8000-000000000000"


@pytest.fixture
def fake():
    server = ThreadingHTTPServer(("127.0.0.1", 0), fake_services.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    fake_services.STATS.clear()
    yield "http://127.0.0.1:%d" % server.server_address[1]
    server.shutdown()


def test_the_switch_is_off_unless_the_rig_sets_it(monkeypatch):
    monkeypatch.delenv("PIG_MUSIC_TEST_SERVICES", raising=False)
    assert test_services.transport() is None


def test_the_rig_never_reaches_a_real_provider(fake):
    async def run():
        async with httpx.AsyncClient(
            transport=test_services.FakeServices(fake)
        ) as client:
            with pytest.raises(httpx.ConnectError):
                await client.get("https://example.com/anything")
            found = await client.get(
                "https://musicbrainz.org/ws/2/release-group/" + ARTIST
            )
            return found.json()

    detail = asyncio.run(run())
    assert detail["primary-type"] == "Album" and detail["id"] == ARTIST
    assert fake_services.STATS == {"musicbrainz.org ws/2/release-group": 1}


def test_popularity_runs_end_to_end_against_the_fake(tmp_path, fake):
    store = Store(tmp_path)
    save_settings(store, True, True, listenbrainz_token=TOKEN)

    async def run():
        async with httpx.AsyncClient(
            transport=test_services.FakeServices(fake)
        ) as client:
            return await Popularity(store).get(client, ARTIST)

    result = asyncio.run(run())
    assert len(result["songs"]) == 20 and len(result["albums"]) == 10


def test_every_rig_command_keeps_to_its_own_containers_and_limits():
    """D-53: pig-music-test-* only, the app capped at 2 CPUs and 2 GB,
    --restart no, data under its own appdata folder; never the production
    container, its appdata or the NAS."""
    from scripts import test_rig

    image = "ghcr.io/maroge1990/pig-music@sha256:" + "0" * 64
    commands = (
        test_rig.up_commands(image)
        + [test_rig.run_command("check", "code")]
        + test_rig.reset_commands()
    )
    runs = [c for c in commands if c.startswith("docker run -d")]
    assert len(runs) == 2 and all("--restart no" in c for c in runs)
    # No internet for the rig, and only an image with the switch (31 Oct: an
    # image without it reached the real metadata services).
    assert any(
        c.startswith("docker network create --internal pig-music-test")
        for c in commands
    )
    probe = commands.index(
        next(c for c in commands if "import server.test_services" in c)
    )
    assert probe < commands.index(runs[0])
    app = next(c for c in runs if "--name pig-music-test-app" in c)
    assert "--cpus 2 " in app and "--memory 2g " in app
    assert "-p " not in app and "--publish" not in app
    for command in commands:
        assert "pig-music-preview" not in command  # production appdata
        assert "/mnt/user/" not in command.replace(test_rig.ROOT, "")
        for name in ("--name ", "docker rm -f "):
            if name in command:
                assert command.split(name, 1)[1].startswith("pig-music-test-")
        if command.startswith("docker exec "):
            assert " pig-music-test-app python " in command
    assert " PigMusic" not in " ".join(commands)


def test_the_rig_runs_only_an_exact_image_digest():
    """D-53 (Codex round 1): pulling a tag such as :main moves it in the
    Docker daemon production shares; a digest moves no tag."""
    from scripts import test_rig

    for image in (
        "ghcr.io/maroge1990/pig-music:main",
        "ghcr.io/maroge1990/pig-music:sha-" + "0" * 40,
        "ghcr.io/maroge1990/pig-music@sha256:" + "0" * 63,
        "",
    ):
        with pytest.raises(SystemExit):
            test_rig.up_commands(image)
