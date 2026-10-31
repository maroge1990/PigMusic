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
    assert sorted(searched) == ["freemp3cloud", "zvu4no"]
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
    assert len(searched) == 2


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
        stale = directory / f"{name * 32}.zvu4no.mp3"
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
            if source != "freemp3cloud":
                raise ValueError("No recording matched.")
            return {"source": source}

        def download(self, source, track, candidate):
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.directory / f"{self.name(track)}.freemp3cloud.mp3"
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
    assert found["source"] == "freemp3cloud"
    # One search per source, shared by the preparation and the play.
    assert sorted(calls) == ["freemp3cloud", "zvu4no"]
    assert resolver.relay(found["relay"], "member")[1] == "audio/mpeg"


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
    assert sorted(asked) == ["freemp3cloud", "jiosaavn", "zvu4no"]
    started = time.monotonic()
    with pytest.raises(ValueError):
        asyncio.run(play())
    assert time.monotonic() - started < 1 and len(asked) == 3
    # Once the miss has expired, the sources are asked again.
    for name, (_, kind, message) in list(resolver.misses.items()):
        resolver.misses[name] = (time.monotonic() - 1, kind, message)
    with pytest.raises(ValueError):
        asyncio.run(play())
    assert len(asked) == 6


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
    monkeypatch.setattr(remote, "SEARCH_SECONDS", 0.3)
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

    monkeypatch.setattr(remote, "SEARCH_SECONDS", 0.2)
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


@pytest.mark.parametrize("status", [408, 429, 503])
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
    assert sorted(relayed) == ["freemp3cloud", "zvu4no"]
    ((expiry, kind, _),) = resolver.misses.values()
    assert kind is TimeoutError
    assert expiry - time.monotonic() <= remote.FAILURE_SECONDS


@pytest.mark.parametrize("failure", ["access", "timeout"])
def test_repeated_source_failures_cool_down_without_remembering_false_misses(
    tmp_path, monkeypatch, caplog, failure
):
    import time
    from server import remote
    from server.providers.contracts import ProviderError

    # One limit for every relayed source: long enough for the instant
    # answers, short enough that the slow fixture search times out.
    monkeypatch.setattr(remote, "SEARCH_SECONDS", 0.1)
    calls = []
    restored = False
    loop_errors = []

    def find(source, track):
        calls.append(source)
        if source == "freemp3cloud" and not restored:
            if failure == "timeout":
                time.sleep(0.3)
                raise ValueError("late no match")
            raise ProviderError("access_required")
        raise ValueError("No recording matched.")

    resolver = RemoteAudio(lambda *_: [], directory=tmp_path / "relay")
    monkeypatch.setattr(resolver, "find", find)

    async def run():
        nonlocal restored
        asyncio.get_running_loop().set_exception_handler(
            lambda loop, context: loop_errors.append(context)
        )
        async with httpx.AsyncClient() as client:
            for index in range(3):
                song = {"artist": "Fixture", "title": str(index), "album": "Fixture"}
                with pytest.raises(TimeoutError):
                    await resolver.shared(client, song)
            assert calls.count("freemp3cloud") == 2
            assert all(kind is TimeoutError for _, kind, _ in resolver.misses.values())
            failures, _ = resolver.source_health[("freemp3cloud", "search")]
            resolver.source_health[("freemp3cloud", "search")] = (
                failures,
                time.monotonic() - 1,
            )
            restored = True
            with pytest.raises(ValueError, match="No source has"):
                await resolver.shared(
                    client, {"artist": "Fixture", "title": "retry", "album": "Fixture"}
                )
            assert calls.count("freemp3cloud") == 3
            assert ("freemp3cloud", "search") not in resolver.source_health
            # Let the old timed-out threads finish; their late failures are consumed.
            await asyncio.sleep(0.4)

    with caplog.at_level("INFO", logger="pig_music.remote"):
        asyncio.run(run())
    assert "freemp3cloud cooling down" in caplog.text
    assert "freemp3cloud healthy again" in caplog.text
    assert "exception was never retrieved" not in caplog.text.lower()
    assert not loop_errors


def test_repeated_transfer_access_failures_also_cool_down_the_source(
    tmp_path, monkeypatch
):
    from server.providers.contracts import ProviderError

    resolver = RemoteAudio(lambda *_: [], directory=tmp_path / "relay")
    searched = []

    def find(source, track):
        searched.append(source)
        if source == "freemp3cloud":
            return {"fixture": True}
        raise ValueError("No match")

    def download(*args):
        raise ProviderError("access_required")

    monkeypatch.setattr(resolver, "find", find)
    monkeypatch.setattr(resolver, "download", download)

    async def run():
        async with httpx.AsyncClient() as client:
            for index in range(3):
                with pytest.raises(TimeoutError):
                    await resolver.shared(
                        client,
                        {"artist": "Fixture", "title": str(index), "album": "Fixture"},
                    )
        assert searched.count("freemp3cloud") == 2

    asyncio.run(run())


def test_successful_fallback_searches_break_the_timeout_streak(tmp_path, monkeypatch):
    """A fifth song only FreeMp3Cloud has stays playable after separated timeouts."""
    resolver = RemoteAudio(lambda *_: [], directory=tmp_path / "relay")
    searched = []
    fallback_finished = asyncio.Event()
    original_search = resolver.search_source
    audio = tmp_path / "disposable.mp3"
    audio.write_bytes(b"mock transfer")

    def find(source, track):
        index = int(track["title"])
        searched.append((source, index))
        if source == "freemp3cloud":
            if index in (0, 3):
                raise TimeoutError("fixture source timeout")
            return {"fixture": True}
        if source == "zvu4no" and index < 4:
            return {"fixture": True}
        raise ValueError("No recording matched.")

    async def observed_search(source, track):
        try:
            return await original_search(source, track)
        finally:
            if source == "freemp3cloud":
                fallback_finished.set()

    monkeypatch.setattr(resolver, "find", find)
    monkeypatch.setattr(resolver, "search_source", observed_search)
    monkeypatch.setattr(resolver, "download", lambda *args: audio)

    async def run():
        async with httpx.AsyncClient() as client:
            for index in range(5):
                fallback_finished.clear()
                found = await resolver.shared(
                    client,
                    {"artist": "Fixture", "title": str(index), "album": "Fixture"},
                )
                assert found["source"] == ("freemp3cloud" if index == 4 else "zvu4no")
                # The lower-priority result must be handled before the next song.
                await asyncio.wait_for(fallback_finished.wait(), 1)
        assert [
            index for source, index in searched if source == "freemp3cloud"
        ] == list(range(5))

    asyncio.run(run())


@pytest.mark.parametrize("late_outcome", ["candidate", "no match"])
def test_old_search_success_cannot_clear_a_newer_cooldown(monkeypatch, late_outcome):
    import threading

    resolver = RemoteAudio()
    release = threading.Event()
    calls = []

    async def run():
        entered = asyncio.Event()
        loop = asyncio.get_running_loop()

        def find(source, track):
            calls.append(track["title"])
            if track["title"] == "old":
                loop.call_soon_threadsafe(entered.set)
                assert release.wait(3), "fixture search was not released"
                if late_outcome == "candidate":
                    return {"fixture": True}
                raise ValueError("No recording matched.")
            raise TimeoutError("fixture source timeout")

        monkeypatch.setattr(resolver, "find", find)
        old = asyncio.create_task(
            resolver.search_source("freemp3cloud", {"title": "old"})
        )
        try:
            await asyncio.wait_for(entered.wait(), 1)
            for title in ("new-one", "new-two"):
                with pytest.raises(TimeoutError):
                    await resolver.search_source("freemp3cloud", {"title": title})
            release.set()
            if late_outcome == "candidate":
                assert await old == {"fixture": True}
            else:
                with pytest.raises(ValueError, match="No recording matched"):
                    await old
            with pytest.raises(TimeoutError, match="cooling down"):
                await resolver.search_source("freemp3cloud", {"title": "blocked"})
            assert "blocked" not in calls
        finally:
            release.set()
            await asyncio.gather(old, return_exceptions=True)

    asyncio.run(run())
