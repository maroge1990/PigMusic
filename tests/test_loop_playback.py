"""LOOP1: live events, playback and artist artwork never hold up the event loop
while SQLite waits behind a writer or the NAS is slow."""

import asyncio
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import server.app as app_module
from server.auth import Auth
from server.catalogue import Catalogue
from server.enrichment import Enrichment
from server.routes.native import native_router
from test_event_loop import library
from test_loop_store import SLOW, longest_stall
from test_mixes import cached, member, ready, track
from test_native_sync import fixture
from test_security import PASSWORD, protected

pytestmark = pytest.mark.loop_timing

RELEASE = "11111111-1111-1111-1111-111111111111"
RECORDING = "22222222-2222-2222-2222-222222222222"


def slow_connect(store, monkeypatch):
    """Every SQLite connection waits SLOW seconds, as behind a busy writer."""
    connect = store.connect

    @contextmanager
    def slow():
        time.sleep(SLOW)
        with connect() as conn:
            yield conn

    monkeypatch.setattr(store, "connect", slow)


def slowed(monkeypatch, module, name):
    """Make `module.name` take SLOW seconds; the event is set once it starts."""
    started = threading.Event()
    original = getattr(module, name)

    def slow(*args, **kwargs):
        started.set()
        time.sleep(SLOW)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, name, slow)
    return started


def health_while(client, started, call):
    """How long /api/health takes while `call` runs its slowed dependency."""
    answer = {}
    caller = threading.Thread(target=lambda: answer.update(response=call()))
    caller.start()
    try:
        assert started.wait(5), "the slowed dependency was never reached"
        began = time.monotonic()
        assert client.get("/api/health").status_code == 200
        waited = time.monotonic() - began
    finally:
        caller.join(10)
    return waited, answer["response"]


def owner_client(app):
    token = app.state.auth.login("mark", PASSWORD, "device", "phone", "ios", "f")[
        "token"
    ]
    return TestClient(
        app,
        base_url="https://private.ts.net",
        headers={"Authorization": "Bearer " + token, "X-Pig-Request": "1"},
    )


def test_event_streams_check_their_session_off_the_loop(tmp_path, monkeypatch):
    store, _, _ = fixture(tmp_path)
    auth = Auth(store)
    token = auth.login(
        "member", "native fixture password", "device", "fixture", "ios", "fixture"
    )["token"]
    user, session = auth.authenticate(token)
    endpoint = next(
        route.endpoint
        for route in native_router(store, auth).routes
        if route.path == "/api/v1/events"
    )

    async def connected():
        return False

    async def run():
        request = SimpleNamespace(
            state=SimpleNamespace(user=user, session=session),
            is_disconnected=connected,
        )
        response = await endpoint(request)
        try:
            slow_connect(store, monkeypatch)
            stall, message = await longest_stall(lambda: anext(response.body_iterator))
            assert message.startswith("event: revision")
            assert stall < 0.2, stall
        finally:
            await response.body_iterator.aclose()

    asyncio.run(run())


def test_saved_catalogue_song_resolves_its_file_off_the_loop(tmp_path, monkeypatch):
    library(tmp_path)

    async def catalogue_get(self, client, path, **params):
        return {
            "id": RELEASE,
            "title": "Album",
            "artist-credit": [{"name": "Artist", "joinphrase": ""}],
            "media": [
                {
                    "position": 1,
                    "tracks": [
                        {
                            "id": "track-one",
                            "position": 1,
                            "title": "Song",
                            "recording": {"id": RECORDING, "title": "Song"},
                        }
                    ],
                }
            ],
        }

    def presence(self, tracks):
        # The household already has this song as the snapshot's "song".
        return [{**t, "saved_id": "song"} for t in tracks]

    monkeypatch.setattr(Catalogue, "get", catalogue_get)
    monkeypatch.setattr(Catalogue, "presence", presence)
    app, _ = protected(tmp_path, monkeypatch)
    # A slow NAS: resolving and checking the saved file takes SLOW seconds.
    started = slowed(monkeypatch, app_module, "track_file")
    with owner_client(app) as client:
        waited, played = health_while(
            client,
            started,
            lambda: client.post(
                "/api/v1/remote-playback/catalogue",
                json={"release_id": RELEASE, "track_id": "track-one"},
            ),
        )
        assert played.status_code == 200, played.text
        assert played.json()["source"] == "Saved library"
        assert waited < 0.2, waited
        # A missing saved file still answers 404 from the worker thread.
        (tmp_path / "nas" / "song.mp3").unlink()
        missing = client.post(
            "/api/v1/remote-playback/catalogue",
            json={"release_id": RELEASE, "track_id": "track-one"},
        )
        assert missing.status_code == 404, missing.text


def test_artist_artwork_finds_its_portrait_off_the_loop(tmp_path, monkeypatch):
    library(tmp_path)
    # No provider traffic: this test is only about the request itself.
    monkeypatch.setattr(Enrichment, "submit", lambda self, key, factory, **_: None)
    app, _ = protected(tmp_path, monkeypatch)
    started = slowed(monkeypatch, app_module, "portrait_target")
    with owner_client(app) as client:
        artist = client.get("/api/v1/library/artists").json()["items"][0]["id"]
        waited, artwork = health_while(
            client,
            started,
            lambda: client.get(
                "/api/v1/library-artwork/artists/" + artist, follow_redirects=False
            ),
        )
        assert artwork.status_code == 404, artwork.text  # No portrait fetched yet.
        assert waited < 0.2, waited


@pytest.fixture
def mixes_minimum(monkeypatch):
    from server import mixes as mix_module

    monkeypatch.setattr(mix_module, "MIN_MIX", 1)


def test_saved_mix_restore_reads_entry_and_day_off_the_loop(
    tmp_path, monkeypatch, mixes_minimum
):
    app, owner = protected(tmp_path, monkeypatch)
    mixes = app.state.store.mixes
    user, _ = member(app, owner)
    song = track()
    cached(mixes, song)
    mixes.save(user, ready(mixes, user, [song]))
    store = mixes.store
    entry, read_document = mixes.entry, store.read_document

    def slow_entry(*args):
        time.sleep(SLOW)
        return entry(*args)

    def slow_read_document(name):
        time.sleep(SLOW)
        return read_document(name)

    # The saved song's entry waits behind a writer.
    monkeypatch.setattr(mixes, "entry", slow_entry)
    stall, identity = asyncio.run(longest_stall(lambda: mixes.restore(user, song)))
    assert identity and identity.startswith("mix:")
    assert stall < 0.2, stall
    # So does the mix-settings document that dates the edition.
    monkeypatch.setattr(mixes, "entry", entry)
    monkeypatch.setattr(store, "read_document", slow_read_document)
    stall, identity = asyncio.run(longest_stall(lambda: mixes.restore(user, song)))
    assert identity and identity.startswith("mix:")
    assert stall < 0.2, stall
