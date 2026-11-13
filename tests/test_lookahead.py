"""Look up the songs on screen before the tap (#192)."""

import asyncio
import time

import pytest

from fastapi.testclient import TestClient

from server import lookahead as lookahead_module
from server.catalogue import Catalogue, release_tracks
from server.lookahead import LookAhead
from server.media import save_settings
from server.remote import RemoteAudio
from test_catalogue import ARTIST, RELEASE, TRACK, OTHER, fixture_release
from test_security import PASSWORD, protected


class Remote:
    """Records lookups; each waits until released."""

    def __init__(self):
        self.started = []
        self.gate = asyncio.Event()

    async def match(self, client, track):
        self.started.append(track["title"])
        await self.gate.wait()


def jobs(*titles):
    def job(title):
        async def run():
            return {"title": title}

        return run

    return [job(title) for title in titles]


def test_two_at_a_time_and_a_new_screen_replaces_what_has_not_started():
    async def scenario():
        remote = Remote()
        ahead = LookAhead(remote, lambda: None, lambda: True)
        assert ahead.screen("mark", jobs("a", "b", "c", "d", "e", "f")) == 5
        await asyncio.sleep(0.05)
        assert remote.started == ["a", "b"]  # At most two at once.
        ahead.screen("mark", jobs("x", "y"))  # c, d and e never start.
        ahead.screen("sam", jobs("s"))  # Each person has their own two.
        await asyncio.sleep(0.05)
        assert remote.started == ["a", "b", "s"]
        remote.gate.set()
        await asyncio.sleep(0.05)
        assert remote.started[:3] == ["a", "b", "s"]
        assert sorted(remote.started[3:]) == ["x", "y"]
        assert ahead.people == {}  # Nothing kept once done.
        await ahead.close()

    asyncio.run(scenario())


def test_a_screen_in_parts_adds_up_and_a_search_waits_for_typing_to_settle():
    async def scenario():
        remote = Remote()
        ahead = LookAhead(remote, lambda: None, lambda: True)
        remote.gate.set()
        lookahead_module.PER_PERSON, saved = 0, lookahead_module.PER_PERSON
        try:  # Nothing starts: what is pending shows how screens combine.
            ahead.screen("mark", jobs("p1", "p2", "p3"), name="home")
            ahead.screen("mark", jobs("r1", "r2", "r3"), name="home")
            assert [len(ahead.people["mark"].pending)] == [5]
            ahead.screen("mark", jobs("s1"))
            assert len(ahead.people["mark"].pending) == 1
        finally:
            lookahead_module.PER_PERSON = saved
        ahead.people.clear()
        ahead.screen("mark", jobs("typ"), delay=0.1)
        ahead.screen("mark", jobs("typing"), delay=0.1)
        await asyncio.sleep(0.05)
        assert remote.started == []
        await asyncio.sleep(0.15)
        assert remote.started == ["typing"]  # The settled search only.
        await ahead.close()

    asyncio.run(scenario())


def test_nothing_is_looked_up_while_remote_playback_is_off():
    async def scenario():
        remote = Remote()
        remote.gate.set()
        ahead = LookAhead(remote, lambda: None, lambda: False)
        ahead.screen("mark", jobs("a"))
        await asyncio.sleep(0.05)
        assert remote.started == []
        await ahead.close()

    asyncio.run(scenario())


def test_close_stops_running_lookups_and_takes_no_more():
    async def scenario():
        remote = Remote()
        ahead = LookAhead(remote, lambda: None, lambda: True)
        ahead.screen("mark", jobs("a", "b", "c"))
        await asyncio.sleep(0.05)
        await ahead.close()
        assert ahead.screen("mark", jobs("d")) == 0
        remote.gate.set()
        await asyncio.sleep(0.05)
        assert remote.started == ["a", "b"]

    asyncio.run(scenario())


def test_a_tap_on_a_song_looked_up_from_its_release_page_asks_no_source(
    tmp_path, monkeypatch
):
    track = release_tracks(fixture_release())[0]
    candidate = {
        "channel": track["artist"],
        "title": track["title"],
        "album": track["album"],
        "duration": track["duration"],
        "isrc": track["isrcs"][0],
        "source_url": "https://aac.saavncdn.com/song.m4a",
    }
    searches = []

    def search(query, limit):
        searches.append(query)
        return [candidate]

    async def probe(client, url):
        return None

    async def provider(self, client, path, **params):
        credited = {"name": "Example", "artist": {"id": ARTIST, "name": "Example"}}
        return {**fixture_release(), "artist-credit": [credited]}

    monkeypatch.setattr(Catalogue, "get", provider)
    monkeypatch.setattr(RemoteAudio, "probe", staticmethod(probe))
    app, _ = protected(tmp_path, monkeypatch)
    save_settings(app.state.store, portraits=False, remote_playback=True)
    app.state.look_ahead.remote_audio.search = search
    token = app.state.auth.login("mark", PASSWORD, "device", "phone", "ios", "x")
    with TestClient(
        app,
        base_url="https://private.ts.net",
        headers={"Authorization": "Bearer " + token["token"]},
    ) as client:
        page = client.get(f"/api/v1/catalogue/releases/{RELEASE}")
        assert page.status_code == 200, page.text
        assert page.json()["tracks"][0]["server_state"] == "not_in_library"
        deadline = time.monotonic() + 5
        while not searches and time.monotonic() < deadline:
            time.sleep(0.02)
        assert len(searches) == 1  # Looked up before any tap.
        web = client.post(
            "/api/v1/remote-playback/catalogue",
            json={"release_id": RELEASE, "track_id": TRACK},
        )
        assert web.status_code == 200, web.text
        assert web.json()["url"] == candidate["source_url"]
        # The iPhone names the song by its IDs and sends no ISRCs.
        phone = client.post(
            "/api/v1/remote-playback",
            json={
                "title": track["title"],
                "artist": track["artist"],
                "album": track["album"],
                "duration": track["duration"],
                "musicbrainz_release_id": RELEASE,
                "musicbrainz_recording_id": OTHER,
            },
        )
        assert phone.status_code == 200, phone.text
        assert phone.json()["url"] == candidate["source_url"]
        assert not phone.json().get("isrcs")  # Answered as asked.
    assert len(searches) == 1  # Neither tap asked a source again.


def test_home_and_a_settled_search_look_up_their_songs(tmp_path, monkeypatch):
    from server import search_match
    from server.apple_search import AppleSearch
    from server.home import HomeDiscovery
    from server.routes import search as search_routes

    track = release_tracks(fixture_release())[0]
    looked_up = []

    async def match_ahead(self, client, wanted):
        looked_up.append(
            (wanted["musicbrainz_release_id"], wanted["musicbrainz_recording_id"])
        )

    async def provider(self, client, path, **params):
        return fixture_release()

    async def popular(self, client):
        return {
            "source": "fixture",
            "state": "ready",
            "unavailable": False,
            "title": "",
            "artists": [],
            "albums": [],
            "songs": [
                {
                    "kind": "songs",
                    "id": OTHER,
                    "release_id": RELEASE,
                    "name": track["title"],
                    "artist": "Example",
                    "artist_id": ARTIST,
                }
            ],
        }

    async def apple_search(self, client, query, region, known=frozenset()):
        return {
            "songs": [
                {"apple_id": n, "title": query, "artist": "Example"} for n in (1, 2, 3)
            ],
            "albums": [],
            "state": "ready",
        }

    async def apple_item(self, client, kind, apple_id):
        return {"apple_id": apple_id}

    async def match(store, catalogue, client, kind, item):
        return {"id": OTHER, "release_id": RELEASE}

    monkeypatch.setattr(Catalogue, "get", provider)
    monkeypatch.setattr(RemoteAudio, "match", match_ahead)
    monkeypatch.setattr(HomeDiscovery, "popular", popular)
    monkeypatch.setattr(AppleSearch, "search", apple_search)
    monkeypatch.setattr(AppleSearch, "item", apple_item)
    monkeypatch.setattr(search_match, "match", match)
    monkeypatch.setattr(search_routes, "SEARCH_SETTLE_SECONDS", 0.2)
    app, _ = protected(tmp_path, monkeypatch)
    save_settings(app.state.store, portraits=False, remote_playback=True)
    token = app.state.auth.login("mark", PASSWORD, "device", "phone", "ios", "x")

    def settle(count):
        deadline = time.monotonic() + 5
        while len(looked_up) < count and time.monotonic() < deadline:
            time.sleep(0.02)

    with TestClient(
        app,
        base_url="https://private.ts.net",
        headers={"Authorization": "Bearer " + token["token"]},
    ) as client:
        assert client.get("/api/v1/home/popular").status_code == 200
        settle(1)
        assert looked_up == [(RELEASE, OTHER)]
        # Type-ahead: only the last search, and only its top two songs.
        for query in ("so", "son", "song"):
            response = client.get("/api/v1/search/online", params={"q": query})
            assert response.status_code == 200, response.text
        time.sleep(0.1)
        assert len(looked_up) == 1  # Still typing.
        settle(3)
        time.sleep(0.2)
        assert looked_up == [(RELEASE, OTHER)] * 3


def test_a_look_ahead_never_takes_the_relay_slots_a_tap_needs(tmp_path, monkeypatch):
    """Codex, #192 round 1 (high): a look-ahead stops at JioSaavn's match. It
    never searches the relays or starts a transfer, so songs nobody tapped
    cannot hold every relay slot while a real tap waits, and its JioSaavn miss
    is not remembered as a miss for the tap, which still asks the relays."""
    import threading

    import httpx

    resolver = RemoteAudio(lambda *_: [], directory=tmp_path / "relay")
    audio = tmp_path / "disposable.mp3"
    audio.write_bytes(b"mock transfer")
    found, fetched = [], []
    release = threading.Event()

    def find(source, track):
        found.append((source, track["title"]))
        return {"fixture": True}

    def download(source, track, candidate):
        fetched.append(track["title"])
        release.wait(5)
        return audio

    monkeypatch.setattr(resolver, "find", find)
    monkeypatch.setattr(resolver, "download", download)

    def song(title):
        return {"artist": "Fixture", "title": title, "album": "Fixture"}

    def screen(*titles):
        def job(title):
            async def run():
                return song(title)

            return run

        return [job(title) for title in titles]

    async def until(condition):
        deadline = time.monotonic() + 3
        while not condition() and time.monotonic() < deadline:
            await asyncio.sleep(0.02)

    async def scenario():
        async with httpx.AsyncClient() as client:
            ahead = LookAhead(resolver, lambda: client, lambda: True)
            ahead.screen("mark", screen("a", "b"))
            ahead.screen("sam", screen("c", "d"))
            await until(lambda: not ahead.people)
            await asyncio.sleep(0.2)
            assert found == [] and fetched == []  # No relay was asked.
            assert not ahead.people  # Every look-ahead has ended.
            assert resolver.relay_slots._value == 3
            assert resolver.misses == {}
            tap = asyncio.ensure_future(resolver.shared(client, song("tapped")))
            await until(lambda: fetched)
            assert fetched == ["tapped"]  # The tap's transfer started at once.
            release.set()
            assert (await tap)["source"] == "zvu4no"
            # A tap on a song looked up ahead still reaches the relays.
            assert (await resolver.shared(client, song("a")))["source"] == "zvu4no"
            await ahead.close()

    try:
        asyncio.run(scenario())
    finally:
        release.set()


def test_earlier_lookups_finishing_never_start_a_search_before_it_settles():
    """Codex, #192 round 1 (medium): an earlier lookup ending during a
    search's settle delay must not start that search's songs, nor those of a
    newer query that replaced it."""

    class Gated:
        def __init__(self):
            self.started = []
            self.gates = {}

        async def match(self, client, track):
            self.started.append(track["title"])
            gate = self.gates.setdefault(track["title"], asyncio.Event())
            await gate.wait()

    async def scenario():
        remote = Gated()
        ahead = LookAhead(remote, lambda: None, lambda: True)
        ahead.screen("mark", jobs("page"), name="release")
        await asyncio.sleep(0.05)
        assert remote.started == ["page"]
        ahead.screen("mark", jobs("so"), delay=0.3)
        remote.gates["page"].set()  # The page's lookup ends while typing.
        await asyncio.sleep(0.1)
        assert remote.started == ["page"]
        ahead.screen("mark", jobs("song"), delay=0.3)  # A newer query.
        await asyncio.sleep(0.1)
        assert remote.started == ["page"]
        await asyncio.sleep(0.3)
        assert remote.started == ["page", "song"]  # The settled query only.
        remote.gates["song"].set()
        await asyncio.sleep(0.05)
        assert ahead.people == {}
        await ahead.close()

    asyncio.run(scenario())


def test_a_tap_never_waits_behind_look_ahead_searches(tmp_path):
    """Codex, #192 round 2 (medium): look-ahead and taps shared JioSaavn's
    three search slots, so people opening screens could queue a real tap
    behind speculative searches. Look-ahead has its own budget; a tap starts
    its search at once, and overtakes a look-ahead of the same song that is
    still waiting for its turn."""
    import threading

    import httpx

    searched = []
    release = threading.Event()

    def search(query, limit):
        searched.append(query)
        release.wait(5)
        return []

    resolver = RemoteAudio(search)

    def song(title):
        return {"artist": "Fixture", "title": title, "album": "Fixture"}

    def screen(*titles):
        def job(title):
            async def run():
                return song(title)

            return run

        return [job(title) for title in titles]

    async def until(condition):
        deadline = time.monotonic() + 3
        while not condition() and time.monotonic() < deadline:
            await asyncio.sleep(0.02)

    async def scenario():
        async with httpx.AsyncClient() as client:
            ahead = LookAhead(resolver, lambda: client, lambda: True)
            for person, titles in (
                ("mark", ("a", "b")),
                ("sam", ("c", "d")),
                ("jo", ("e", "f")),
            ):
                ahead.screen(person, screen(*titles))
            await asyncio.sleep(0.2)
            speculative = len(searched)
            assert speculative < 3  # Look-ahead leaves the tap's slots free.
            tap = asyncio.ensure_future(resolver.resolve(client, song("tapped")))
            await until(lambda: "Fixture tapped" in searched)
            assert "Fixture tapped" in searched  # Started at once.
            queued = next(
                t for t in "abcdef" if f"Fixture {t}" not in searched
            )  # A look-ahead still waiting for its turn.
            overtaking = asyncio.ensure_future(resolver.resolve(client, song(queued)))
            await until(lambda: f"Fixture {queued}" in searched)
            assert f"Fixture {queued}" in searched
            release.set()
            for task in (tap, overtaking):
                with pytest.raises(ValueError):
                    await task
            await ahead.close()

    try:
        asyncio.run(scenario())
    finally:
        release.set()


def test_taps_on_one_song_share_one_search_even_when_overtaking():
    """Codex, #192 confirmation of round 2 (medium): taps scheduled together
    on one song must share a single search, also when they overtake a
    look-ahead of it still waiting for its turn."""
    import threading

    import httpx

    searched = []
    release = threading.Event()

    def search(query, limit):
        searched.append(query)
        release.wait(5)
        return []

    resolver = RemoteAudio(search)
    song = {"artist": "Fixture", "title": "one", "album": "Fixture"}
    other = {"artist": "Fixture", "title": "two", "album": "Fixture"}

    async def scenario():
        async with httpx.AsyncClient() as client:
            taps = [
                asyncio.ensure_future(resolver.resolve(client, song)) for _ in range(3)
            ]
            await asyncio.sleep(0.2)
            assert searched == ["Fixture one"]
            # A look-ahead waiting for its turn, then three taps at once.
            from server import remote

            blockers = [
                asyncio.ensure_future(
                    resolver.match(client, {**other, "title": f"busy{n}"})
                )
                for n in range(remote.LOOKAHEAD_SLOTS)
            ]
            await asyncio.sleep(0.1)
            waiting = asyncio.ensure_future(resolver.match(client, other))
            await asyncio.sleep(0.1)
            assert "Fixture two" not in searched
            taps += [
                asyncio.ensure_future(resolver.resolve(client, other)) for _ in range(3)
            ]
            await asyncio.sleep(0.2)
            assert searched.count("Fixture two") == 1
            release.set()
            for task in taps + blockers + [waiting]:
                with pytest.raises(ValueError):
                    await task
            assert searched.count("Fixture two") == 1  # Never searched again.

    try:
        asyncio.run(scenario())
    finally:
        release.set()


def test_overtaken_look_aheads_never_hold_the_budget_waiting():
    """Codex, #192 confirmation 3 (medium): an overtaken look-ahead waited
    for the lookup that replaced it while holding a look-ahead slot. Two of
    them, whose replacements were newer look-aheads, held both slots that
    those replacements needed, for ever."""
    import threading

    import httpx

    from server import remote

    release = threading.Event()
    searched = []

    def search(query, limit):
        searched.append(query)
        if "busy" in query:
            release.wait(5)
        return []

    resolver = RemoteAudio(search)

    def song(title):
        return {"artist": "Fixture", "title": title, "album": "Fixture"}

    async def scenario():
        async with httpx.AsyncClient() as client:
            busy = [
                asyncio.ensure_future(resolver.match(client, song(f"busy{n}")))
                for n in range(remote.LOOKAHEAD_SLOTS)
            ]
            await asyncio.sleep(0.1)
            old = [
                asyncio.ensure_future(resolver.match(client, song(t)))
                for t in ("a", "b")
            ]
            await asyncio.sleep(0.05)
            # Taps overtake both and miss at once.
            for title in ("a", "b"):
                with pytest.raises(ValueError):
                    await resolver.resolve(client, song(title))
            # Another person's look-ahead queues the same songs again.
            new = [
                asyncio.ensure_future(resolver.match(client, song(t)))
                for t in ("a", "b")
            ]
            await asyncio.sleep(0.05)
            release.set()
            results = await asyncio.wait_for(
                asyncio.gather(*busy, *old, *new, return_exceptions=True), 3
            )
            assert all(isinstance(r, ValueError) for r in results)
            assert resolver.speculative_slots._value == remote.LOOKAHEAD_SLOTS

    try:
        asyncio.run(scenario())
    finally:
        release.set()
