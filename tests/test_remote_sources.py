import asyncio
import httpx
import pytest
from server import request_timing
from server.remote import RemoteAudio


def test_fast_remote_requires_release_context_and_caches_verified_urls():
    calls = []
    target = {
        "artist": "Artist",
        "title": "Song",
        "album": "Album",
        "duration": 180,
        "isrcs": ["ABC"],
    }
    candidate = {
        "channel": "Artist",
        "title": "Song",
        "album": "Album",
        "duration": 180,
        "source_url": "https://aac.saavncdn.com/music.m4a",
    }

    async def run():
        def search(*args):
            calls.append("search")
            return [candidate, candidate]

        def transport(req):
            calls.append("probe")
            return httpx.Response(206, headers={"content-type": "audio/mp4"})

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            resolver = RemoteAudio(search)
            assert await resolver.resolve(client, target) == candidate["source_url"]
            assert await resolver.resolve(client, target) == candidate["source_url"]
            resolver.cache.clear()
            for change in [
                {"album": "Different album"},
                {"isrc": "WRONG"},
                {"source_url": "https://127.0.0.1/private"},
            ]:
                resolver.search = lambda *_: [{**candidate, **change}]
                with pytest.raises(ValueError):
                    await resolver.resolve(client, target)

    asyncio.run(run())
    assert calls == ["search", "probe"]


def test_remote_falls_back_in_order_to_a_relayed_lossy_source(
    tmp_path, monkeypatch, caplog
):
    import shutil
    import subprocess
    from server.providers import network

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg is required for the relay probe")
    fixture = tmp_path / "fixture.mp3"
    subprocess.run(
        [ffmpeg, "-v", "error", "-f", "lavfi", "-i", "sine=duration=3", str(fixture)],
        check=True,
    )
    target = {"artist": "Artist", "title": "Song", "album": "Album", "duration": 3}
    candidate = {
        "channel": "Artist",
        "title": "Song",
        "duration": 3,
        "source_url": "https://zvu4it.org/song.mp3",
    }
    searched = []

    def relay_search(source, track):
        searched.append(source)
        if source == "zvu4no":
            # A live version is not the requested recording; the next source is tried.
            return [{**candidate, "title": "Song (Live)"}]
        # Several copies of one recording: the 320 kbps one is relayed.
        return [
            {
                **candidate,
                "source_url": "https://meln.top/low.mp3",
                "quality": "128kbps",
            },
            {
                **candidate,
                "source_url": "https://meln.top/song.mp3",
                "quality": "320kbps",
            },
        ]

    def transfer(url, hosts, path, context):
        assert url == "https://meln.top/song.mp3" and "meln.top" in hosts
        shutil.copyfile(fixture, path)

    monkeypatch.setattr(network, "transfer", transfer)
    resolver = RemoteAudio(
        lambda *_: [], directory=tmp_path / "relay", relay_search=relay_search
    )

    traces = []

    async def run():
        # As inside a request: the steps also become Server-Timing phases.
        trace = request_timing.Trace("remote-test")
        traces.append(trace)
        token = request_timing._trace.set(trace)
        try:
            async with httpx.AsyncClient() as client:
                return await resolver.stream(client, target, "member")
        finally:
            request_timing._trace.reset(token)

    with caplog.at_level("INFO", logger="pig_music.remote"):
        found = asyncio.run(run())
    # Every source is searched at once; the best match in order is relayed.
    assert found["source"] == "freemp3cloud"
    # The server log says where the wait went.
    (timing,) = [
        r.getMessage() for r in caplog.records if r.getMessage().startswith("Remote")
    ]
    (rejected,) = [
        r.getMessage() for r in caplog.records if r.getMessage().startswith("JioSaavn")
    ]
    assert rejected.endswith("0 results, 0 addresses matched; no results")
    assert timing.startswith("Remote playback: Artist - Song from freemp3cloud in ")
    for step in ("jiosaavn no match", "zvu4no no match", "freemp3cloud found"):
        assert step in timing
    assert "freemp3cloud transfer " in timing
    assert sorted(searched) == ["freemp3cloud", "youtube", "zvu4no"]
    phases = [name for name, _ in traces[0].phases]
    for name in ("relay-cache", "jiosaavn-miss", "zvu4no-miss", "freemp3cloud-found"):
        assert name in phases, phases
    assert "freemp3cloud-transfer" in phases
    # Played again, it comes from the relay cache without asking any source.
    with caplog.at_level("INFO", logger="pig_music.remote"):
        again = asyncio.run(run())
    assert again["source"] == "freemp3cloud"
    assert [name for name, _ in traces[1].phases] == ["relay-cache"]
    path, media_type = resolver.relay(found["relay"], "member")
    # The same audio, with at most a length header added (never re-encoded).
    assert media_type == "audio/mpeg"
    assert abs(path.stat().st_size - fixture.stat().st_size) < 4096
    # A relay belongs to the person who asked for it.
    assert resolver.relay(found["relay"], "someone-else") is None
    # The fetched file is reused for the same recording.
    assert asyncio.run(run())["source"] == "freemp3cloud"
    assert len(searched) == 3


def test_relay_cache_survives_restarts_expires_and_keeps_recent_plays(
    tmp_path, monkeypatch
):
    import os
    import time
    from server import remote

    target = {"artist": "Artist", "title": "Song", "album": "Album", "duration": 3}
    directory = tmp_path / "relay"
    directory.mkdir()
    resolver = RemoteAudio(directory=directory)
    path = directory / f"{resolver.name(target)}.zvu4no.mp3"
    path.write_bytes(b"x" * 10)
    # A new resolver (a restarted server) finds the copy by recording.
    assert RemoteAudio(directory=directory).cached(target) == (path, "zvu4no")
    old = time.time() - remote.RELAY_SECONDS - 60
    os.utime(path, (old, old))
    assert resolver.cached(target) is None
    resolver.prune()
    assert not path.exists()
    # Over budget, the least recently played copy goes first.
    monkeypatch.setattr(remote, "RELAY_BYTES", 15)
    for name, age in (("a", 300), ("b", 200), ("c", 100)):
        stale = directory / f"{name * 32}.youtube.m4a"
        stale.write_bytes(b"x" * 10)
        os.utime(stale, (time.time() - age, time.time() - age))
    resolver.prune()
    assert sorted(p.name[0] for p in directory.iterdir()) == ["c"]


def test_playing_a_song_being_prepared_joins_that_work(tmp_path):
    import threading

    target = {"artist": "Artist", "title": "Song", "album": "Album", "duration": 3}
    calls = []
    release = threading.Event()

    class Resolver(RemoteAudio):
        async def resolve(self, client, track):
            raise ValueError("No direct stream")

        def find(self, source, track):
            calls.append(source)
            release.wait(5)
            if source != "youtube":
                raise ValueError("No recording matched.")
            return {"source": source}

        def download(self, source, track, candidate):
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.directory / f"{self.name(track)}.youtube.m4a"
            path.write_bytes(b"audio")
            return path

    resolver = Resolver(directory=tmp_path / "relay")

    async def run():
        assert resolver.prepare(None, [target]) == 1
        await asyncio.sleep(0.05)
        playing = asyncio.ensure_future(resolver.stream(None, target, "member"))
        await asyncio.sleep(0.05)
        release.set()
        return await playing

    found = asyncio.run(run())
    assert found["source"] == "youtube"
    # One search per source, shared by the preparation and the play.
    assert sorted(calls) == ["freemp3cloud", "youtube", "zvu4no"]
    assert resolver.relay(found["relay"], "member")[1] == "audio/mp4"


def test_a_relayed_copy_plays_before_any_provider_is_asked(tmp_path):
    asked = []

    async def search(*args, **kwargs):
        asked.append(args)
        raise AssertionError("provider asked despite a relayed copy")

    target = {"artist": "Artist", "title": "Song", "album": "Album", "duration": 3}
    directory = tmp_path / "relay"
    directory.mkdir()
    resolver = RemoteAudio(search=search, directory=directory)
    path = directory / f"{resolver.name(target)}.zvu4no.mp3"
    path.write_bytes(b"x" * 10)
    found = asyncio.run(resolver.locate(None, target))
    assert found == {"path": path, "source": "zvu4no"}
    assert asked == []


def test_relayed_mp3_gets_a_length_header_without_reencoding(tmp_path):
    import shutil
    import subprocess

    from server.remote import add_length_header

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg not installed")
    path = tmp_path / "song.mp3"
    made = subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            "-c:a",
            "libmp3lame",
            "-q:a",
            "4",
            "-write_xing",
            "0",
            "-y",
            str(path),
        ],
        capture_output=True,
    )
    if made.returncode:
        pytest.skip("ffmpeg has no MP3 encoder")
    before = path.read_bytes()
    assert b"Xing" not in before[:4096] and b"Info" not in before[:4096]
    add_length_header(path)
    after = path.read_bytes()
    assert b"Xing" in after[:4096] or b"Info" in after[:4096]
    # Audio frames are copied, not re-encoded: the size barely changes.
    assert abs(len(after) - len(before)) < 4096


def test_a_song_no_source_has_is_remembered_as_a_miss(tmp_path, monkeypatch):
    import time

    from server import remote

    asked = []

    def relay_search(source, track):
        asked.append(source)
        return []

    resolver = RemoteAudio(
        lambda *_: asked.append("jiosaavn") or [],
        directory=tmp_path / "relay",
        relay_search=relay_search,
    )
    target = {"artist": "Artist", "title": "Creep (Live)", "album": "Album"}

    async def play():
        async with httpx.AsyncClient() as client:
            return await resolver.stream(client, target, "member")

    with pytest.raises(ValueError):
        asyncio.run(play())
    assert sorted(asked) == ["freemp3cloud", "jiosaavn", "youtube", "zvu4no"]
    started = time.monotonic()
    with pytest.raises(ValueError):
        asyncio.run(play())
    assert time.monotonic() - started < 1 and len(asked) == 4
    # Once the miss has expired, the sources are asked again.
    for name, (_, kind, message) in list(resolver.misses.items()):
        resolver.misses[name] = (time.monotonic() - 1, kind, message)
    with pytest.raises(ValueError):
        asyncio.run(play())
    assert len(asked) == 8


def test_a_network_failure_is_remembered_only_briefly():
    from server import remote

    def search(*_):
        raise TimeoutError("JioSaavn did not answer")

    resolver = RemoteAudio(search)
    target = {"artist": "Artist", "title": "Song", "album": "Album"}

    async def play():
        async with httpx.AsyncClient() as client:
            return await resolver.stream(client, target, "member")

    with pytest.raises(TimeoutError):
        asyncio.run(play())
    ((expiry, kind, _),) = resolver.misses.values()
    assert kind is TimeoutError
    import time

    assert expiry - time.monotonic() <= remote.FAILURE_SECONDS
    with pytest.raises(TimeoutError):
        asyncio.run(play())  # From memory: the remote route answers 503.


def test_a_slow_source_gives_way_within_its_limit(tmp_path, monkeypatch):
    import shutil
    import subprocess
    import time

    from server import remote
    from server.providers import network

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg is required for the relay probe")
    fixture = tmp_path / "fixture.mp3"
    subprocess.run(
        [ffmpeg, "-v", "error", "-f", "lavfi", "-i", "sine=duration=3", str(fixture)],
        check=True,
    )
    monkeypatch.setattr(remote, "DEFAULT_SEARCH_SECONDS", 0.3)
    candidate = {
        "channel": "Artist",
        "title": "Song",
        "duration": 3,
        "source_url": "https://meln.top/song.mp3",
        "quality": "320kbps",
    }

    def relay_search(source, track):
        if source == "zvu4no":
            time.sleep(2)  # The first choice is stuck.
        return [candidate] if source == "freemp3cloud" else []

    monkeypatch.setattr(
        network,
        "transfer",
        lambda url, hosts, path, context: shutil.copyfile(fixture, path),
    )
    resolver = RemoteAudio(
        lambda *_: [], directory=tmp_path / "relay", relay_search=relay_search
    )

    async def play():
        started = time.monotonic()
        async with httpx.AsyncClient() as client:
            found = await resolver.stream(
                client,
                {"artist": "Artist", "title": "Song", "album": "Album", "duration": 3},
                "member",
            )
        # Answered before the stuck search ends (asyncio.run waits for it).
        return found, time.monotonic() - started

    found, seconds = asyncio.run(play())
    assert found["source"] == "freemp3cloud" and seconds < 1.5


def test_a_jiosaavn_lookup_never_waits_behind_relays():
    candidate = {
        "channel": "Artist",
        "title": "Song",
        "album": "Album",
        "duration": 180,
        "source_url": "https://aac.saavncdn.com/music.m4a",
    }
    target = {"artist": "Artist", "title": "Song", "album": "Album", "duration": 180}

    async def run():
        resolver = RemoteAudio(lambda *_: [candidate])
        for _ in range(3):  # Three songs transferring from relayed sources.
            await resolver.relay_slots.acquire()
        transport = httpx.MockTransport(
            lambda request: httpx.Response(206, headers={"content-type": "audio/mp4"})
        )
        async with httpx.AsyncClient(transport=transport) as client:
            return await asyncio.wait_for(resolver.resolve(client, target), 1)

    assert asyncio.run(run()) == candidate["source_url"]


def test_jiosaavn_rejections_are_logged_with_their_reasons(caplog):
    target = {"artist": "Tame Impala", "title": "The Less I Know the Better"}
    target["album"] = "Currents"
    results = [
        {
            "channel": "Tame Impala",
            "title": "The Less I Know the Better",
            "album": "Currents (B-Sides)",
            "duration": 216,
            "source_url": "https://aac.saavncdn.com/a.m4a",
        },
        {
            "channel": "Someone Else",
            "title": "The Less I Know the Better",
            "album": "Covers",
            "duration": 200,
            "source_url": "https://aac.saavncdn.com/b.m4a",
        },
    ]

    async def run():
        async with httpx.AsyncClient() as client:
            await RemoteAudio(lambda *_: results).resolve(client, target)

    with caplog.at_level("INFO", logger="pig_music.remote"):
        with pytest.raises(ValueError):
            asyncio.run(run())
    (line,) = [r.getMessage() for r in caplog.records]
    assert "2 results, 0 addresses matched" in line
    assert "album 'Currents (B-Sides)'" in line and "not the recording" in line


@pytest.mark.parametrize("trouble", ["search timeout", "provider error", "transfer"])
def test_a_transient_failure_is_never_a_long_miss(tmp_path, monkeypatch, trouble):
    import time

    from server import remote
    from server.providers import network

    monkeypatch.setattr(remote, "DEFAULT_SEARCH_SECONDS", 0.2)
    monkeypatch.setattr(remote, "SEARCH_SECONDS", {})
    candidate = {
        "channel": "Artist",
        "title": "Song",
        "duration": 3,
        "source_url": "https://meln.top/song.mp3",
    }
    asked = []

    def relay_search(source, track):
        asked.append(source)
        if trouble == "search timeout":
            time.sleep(0.5)
        if trouble == "provider error":
            raise httpx.ConnectError("provider down")
        return [candidate] if trouble == "transfer" and source == "freemp3cloud" else []

    def transfer(url, hosts, path, context):
        raise httpx.ReadTimeout("transfer stalled")

    monkeypatch.setattr(network, "transfer", transfer)

    def jiosaavn(*_):
        raise TimeoutError("JioSaavn did not answer")

    resolver = RemoteAudio(
        jiosaavn, directory=tmp_path / "relay", relay_search=relay_search
    )

    async def play():
        async with httpx.AsyncClient() as client:
            return await resolver.stream(
                client,
                {"artist": "Artist", "title": "Song", "album": "Album"},
                "member",
            )

    # Uncertain: the remote route answers 503, and the sources are asked again
    # minutes later, not half an hour.
    with pytest.raises(TimeoutError):
        asyncio.run(play())
    ((expiry, kind, _),) = resolver.misses.values()
    assert kind is TimeoutError
    assert expiry - time.monotonic() <= remote.FAILURE_SECONDS


def test_every_source_saying_no_is_a_long_miss(tmp_path):
    import time

    from server import remote

    resolver = RemoteAudio(
        lambda *_: [], directory=tmp_path / "relay", relay_search=lambda *_: []
    )

    async def play():
        async with httpx.AsyncClient() as client:
            return await resolver.stream(
                client,
                {"artist": "Artist", "title": "Song", "album": "Album"},
                "member",
            )

    with pytest.raises(ValueError):
        asyncio.run(play())
    ((expiry, kind, _),) = resolver.misses.values()
    assert kind is ValueError
    assert expiry - time.monotonic() > remote.FAILURE_SECONDS


@pytest.mark.parametrize("kind", ["unavailable", "limit"])
def test_a_provider_outage_is_never_a_long_miss(tmp_path, kind):
    import time

    from server import remote
    from server.providers.contracts import ProviderError

    def relay_search(source, track):
        raise ProviderError(kind)

    resolver = RemoteAudio(
        lambda *_: [], directory=tmp_path / "relay", relay_search=relay_search
    )
    only_jiosaavn = RemoteAudio(lambda *_: (_ for _ in ()).throw(ProviderError(kind)))
    target = {"artist": "Artist", "title": "Song", "album": "Album"}

    async def play(audio):
        async with httpx.AsyncClient() as client:
            return await audio.stream(client, target, "member")

    for audio in (resolver, only_jiosaavn):
        with pytest.raises(TimeoutError):
            asyncio.run(play(audio))
        ((expiry, cached, _),) = audio.misses.values()
        assert cached is TimeoutError
        assert expiry - time.monotonic() <= remote.FAILURE_SECONDS


@pytest.mark.parametrize("status", [429, 503])
def test_a_busy_jiosaavn_cdn_is_never_a_long_miss(status):
    import time

    from server import remote

    candidate = {
        "channel": "Artist",
        "title": "Song",
        "album": "Album",
        "duration": 180,
        "source_url": "https://aac.saavncdn.com/music.m4a",
    }
    target = {"artist": "Artist", "title": "Song", "album": "Album", "duration": 180}
    resolver = RemoteAudio(lambda *_: [candidate])

    async def play():
        transport = httpx.MockTransport(lambda request: httpx.Response(status))
        async with httpx.AsyncClient(transport=transport) as client:
            return await resolver.stream(client, target, "member")

    with pytest.raises(TimeoutError):
        asyncio.run(play())
    ((expiry, cached, _),) = resolver.misses.values()
    assert cached is TimeoutError
    assert expiry - time.monotonic() <= remote.FAILURE_SECONDS


def test_a_miss_holds_only_for_the_same_constraints():
    candidate = {
        "channel": "Artist",
        "title": "Song",
        "album": "Album",
        "duration": 180,
        "isrc": "GOOD",
        "source_url": "https://aac.saavncdn.com/music.m4a",
    }
    resolver = RemoteAudio(lambda *_: [candidate])
    base = {"artist": "Artist", "title": "Song", "album": "Album", "duration": 180}

    async def play(target):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(206, headers={"content-type": "audio/mp4"})
        )
        async with httpx.AsyncClient(transport=transport) as client:
            return await resolver.stream(client, target, "member")

    with pytest.raises(ValueError):
        asyncio.run(play({**base, "isrcs": ["OTHER"]}))
    assert (
        asyncio.run(play({**base, "isrcs": ["GOOD"]}))["url"] == candidate["source_url"]
    )


def test_a_jiosaavn_outage_falls_back_to_relays_and_is_remembered_briefly(
    tmp_path, monkeypatch
):
    import time

    from server import remote
    from server.vendor import jiosaavn

    def offline(*args, **kwargs):
        raise httpx.ConnectError("JioSaavn unreachable")

    monkeypatch.setattr(jiosaavn.httpx, "get", offline)
    relayed = []
    resolver = RemoteAudio(
        directory=tmp_path / "relay",
        relay_search=lambda source, track: relayed.append(source) or [],
    )
    target = {"artist": "Artist", "title": "Song", "album": "Album"}

    async def play():
        async with httpx.AsyncClient() as client:
            return await resolver.stream(client, target, "member")

    # The real vendored search: its wrapped failure still lets the relays
    # answer, and the outcome is uncertain (503, minutes).
    with pytest.raises(TimeoutError):
        asyncio.run(play())
    assert sorted(relayed) == ["freemp3cloud", "youtube", "zvu4no"]
    ((expiry, kind, _),) = resolver.misses.values()
    assert kind is TimeoutError
    assert expiry - time.monotonic() <= remote.FAILURE_SECONDS
