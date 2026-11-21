import asyncio
import httpx
import pytest
from server import request_timing
from server.remote import RemoteAudio
from test_youtube import FakeYouTube


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
                {"isrc": "WRONG"},
                {"source_url": "https://127.0.0.1/private"},
            ]:
                resolver.search = lambda *_: [{**candidate, **change}]
                with pytest.raises(ValueError):
                    await resolver.resolve(client, target)
            # Another album is refused only for an edition chosen explicitly.
            resolver.search = lambda *_: [{**candidate, "album": "Different album"}]
            with pytest.raises(ValueError):
                await resolver.resolve(client, {**target, "explicit_edition": True})

    asyncio.run(run())
    assert calls == ["search", "probe"]


def test_playing_a_song_being_prepared_joins_that_work():
    target = {"artist": "Artist", "title": "Song", "album": "Album", "duration": 3}
    calls = []

    class Resolver(RemoteAudio):
        async def resolve(self, client, track, speculative=False):
            calls.append(track["title"])
            await self.release.wait()
            return "https://aac.saavncdn.com/song.m4a"

    resolver = Resolver()

    async def run():
        resolver.release = asyncio.Event()
        assert resolver.prepare(None, [target]) == 1
        await asyncio.sleep(0.05)
        playing = asyncio.ensure_future(resolver.stream(None, target, "member"))
        await asyncio.sleep(0.05)
        resolver.release.set()
        return await playing

    found = asyncio.run(run())
    assert found == {"url": "https://aac.saavncdn.com/song.m4a", "source": "jiosaavn"}
    # One lookup, shared by the preparation and the play.
    assert calls == ["Song"]


def test_a_song_no_source_has_is_remembered_as_a_miss():
    import time

    asked = []
    youtube = FakeYouTube([])
    resolver = RemoteAudio(lambda *_: asked.append("jiosaavn") or [], youtube=youtube)
    target = {"artist": "Artist", "title": "Creep (Live)", "album": "Album"}

    async def play():
        async with httpx.AsyncClient() as client:
            return await resolver.stream(client, target, "member")

    with pytest.raises(ValueError):
        asyncio.run(play())
    assert asked == ["jiosaavn"] and youtube.searched == ["Creep (Live)"]
    started = time.monotonic()
    with pytest.raises(ValueError):
        asyncio.run(play())
    assert time.monotonic() - started < 1
    assert len(asked) == 1 and len(youtube.searched) == 1
    # Once the miss has expired, the sources are asked again.
    for name, (_, kind, message) in list(resolver.misses.items()):
        resolver.misses[name] = (time.monotonic() - 1, kind, message)
    # JioSaavn's own no-match memory (#193) lasts as long.
    resolver.unmatched = {k: time.monotonic() - 1 for k in resolver.unmatched}
    with pytest.raises(ValueError):
        asyncio.run(play())
    assert len(asked) == 2 and len(youtube.searched) == 2


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


def test_jiosaavn_rejections_are_logged_with_their_reasons(caplog):
    target = {"artist": "Tame Impala", "title": "The Less I Know the Better"}
    target["album"] = "Currents"
    target["explicit_edition"] = True
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


@pytest.mark.parametrize("trouble", ["search timeout", "network error"])
def test_a_transient_failure_is_never_a_long_miss(trouble):
    import time

    from server import remote

    class Troubled(FakeYouTube):
        async def songs(self, track, speculative=False):
            if trouble == "search timeout":
                raise TimeoutError("YouTube did not answer")
            raise httpx.ConnectError("YouTube unreachable")

    resolver = RemoteAudio(lambda *_: [], youtube=Troubled([]))

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


def test_every_source_saying_no_is_a_long_miss():
    import time

    from server import remote

    resolver = RemoteAudio(lambda *_: [], youtube=FakeYouTube([]))

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
def test_a_provider_outage_is_never_a_long_miss(kind):
    import time

    from server import remote
    from server.providers.contracts import ProviderError

    class Down(FakeYouTube):
        async def songs(self, track, speculative=False):
            raise ProviderError(kind)

    resolver = RemoteAudio(lambda *_: [], youtube=Down([]))
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


def test_a_jiosaavn_outage_falls_back_to_youtube_and_is_remembered_briefly(
    monkeypatch,
):
    import time

    from server import remote
    from server.vendor import jiosaavn

    def offline(*args, **kwargs):
        raise httpx.ConnectError("JioSaavn unreachable")

    monkeypatch.setattr(jiosaavn.httpx, "get", offline)
    youtube = FakeYouTube([])
    resolver = RemoteAudio(youtube=youtube)
    target = {"artist": "Artist", "title": "Song", "album": "Album"}

    async def play():
        async with httpx.AsyncClient() as client:
            return await resolver.stream(client, target, "member")

    # The real vendored search: its wrapped failure still lets YouTube
    # answer, and the outcome is uncertain (503, minutes).
    with pytest.raises(TimeoutError):
        asyncio.run(play())
    assert youtube.searched == ["Song"]
    ((expiry, kind, _),) = resolver.misses.values()
    assert kind is TimeoutError
    assert expiry - time.monotonic() <= remote.FAILURE_SECONDS


def resolved(target, results):
    async def run():
        def transport(request):
            return httpx.Response(206, headers={"content-type": "audio/mp4"})

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            return await RemoteAudio(lambda *_: results).resolve(client, target)

    return asyncio.run(run())


def saavn(album, url, duration=170, title="Breathless", channel="Cyril"):
    return {
        "channel": channel,
        "title": title,
        "album": album,
        "duration": duration,
        "source_url": f"https://aac.saavncdn.com/{url}.m4a",
    }


def test_jiosaavn_plays_the_same_recording_from_another_release():
    """5 Nov 2026 (Mark's remote plays): JioSaavn had the recording, filed as
    the single or another release, and the exact-album rule refused it
    (CYRIL, Gracie Abrams, Olivia Rodrigo and more)."""
    single = {"artist": "CYRIL", "title": "Breathless", "duration": 171}
    single["album"] = "Breathless - Single"
    assert resolved(single, [saavn("Breathless", "a")]).endswith("/a.m4a")
    deluxe = {**single, "album": "Daughter from Hell (Deluxe)"}
    assert resolved(deluxe, [saavn("Breathless", "a")]).endswith("/a.m4a")
    # Still never a different recording: live, too long, or another artist.
    for wrong in (
        saavn("Breathless (Live)", "b", title="Breathless (Live)"),
        saavn("Breathless", "c", duration=230),
        saavn("Breathless", "d", channel="Someone Else"),
    ):
        with pytest.raises(ValueError):
            resolved(single, [wrong])


def test_jiosaavn_picks_one_copy_when_several_match():
    """Patient Zero and Nicole Kidman each matched two identical copies and
    the 'exactly one' rule refused both."""
    target = {"artist": "CYRIL", "title": "Breathless", "duration": 171}
    target["album"] = "Breathless - Single"
    copies = [
        saavn("Summer Hits 2026", "z"),
        saavn("Breathless", "m"),
        saavn("Dance Party", "a"),
    ]
    # The same album first (without " - Single"), then a fixed order.
    assert resolved(target, copies).endswith("/m.m4a")
    assert resolved(target, list(reversed(copies))).endswith("/m.m4a")
    others = [c for c in copies if c["album"] != "Breathless"]
    assert resolved(target, others).endswith("/a.m4a")
    assert resolved(target, list(reversed(others))).endswith("/a.m4a")


def test_jiosaavn_never_takes_a_remix_or_demo_album_for_the_studio_song():
    """Codex round 1 (5 Nov): with the album rule relaxed, an unchanged title
    on 'The Remixes' or 'Demos' passed (matches() knows only the singular)."""
    target = {"artist": "CYRIL", "title": "Breathless", "duration": 171}
    target["album"] = "Breathless - Single"
    for album in ("The Remixes", "Demos", "Live at Wembley", "Acoustic Sessions"):
        with pytest.raises(ValueError):
            resolved(target, [saavn(album, "x")])
    live = {**target, "album": "Live at Wembley"}
    assert resolved(live, [saavn("Live at Wembley", "l")]).endswith("/l.m4a")
    # Another concert is another recording, however close its length.
    with pytest.raises(ValueError):
        resolved(live, [saavn("Live at Leeds", "k", duration=173)])
    # Unless an ISRC proves it is the same performance.
    proven = {**saavn("Live at Leeds", "k"), "isrc": "LIVE1"}
    assert resolved({**live, "isrcs": ["LIVE1"]}, [proven]).endswith("/k.m4a")


def test_a_cached_answer_never_serves_a_stricter_request():
    target = {"artist": "CYRIL", "title": "Breathless", "duration": 171}
    target["album"] = "Breathless - Single"
    other_album = saavn("Summer Hits 2026", "s")

    async def run():
        def transport(request):
            return httpx.Response(206, headers={"content-type": "audio/mp4"})

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            resolver = RemoteAudio(lambda *_: [other_album])
            assert (await resolver.resolve(client, target)).endswith("/s.m4a")
            with pytest.raises(ValueError):
                await resolver.resolve(client, {**target, "explicit_edition": True})
            resolver.search = lambda *_: [{**other_album, "isrc": "OTHER"}]
            with pytest.raises(ValueError):
                await resolver.resolve(client, {**target, "isrcs": ["MINE"]})

    asyncio.run(run())


def test_jiosaavn_never_takes_a_sped_up_or_slowed_upload():
    """Codex round 2 (5 Nov): 'Song (Slowed)' or a sped-up album passed the
    relaxed rule; without a requested length any speed change would."""
    target = {"artist": "CYRIL", "title": "Breathless", "duration": 171}
    target["album"] = "Breathless - Single"
    for album in ("Breathless (Slowed)", "Breathless (Sped Up)", "Nightcore Hits"):
        with pytest.raises(ValueError):
            resolved(target, [saavn(album, "x", duration=174)])
    # Among several copies an altered one is never the pick.
    copies = [saavn("Breathless (Slowed)", "a"), saavn("Summer Hits 2026", "s")]
    assert resolved(target, copies).endswith("/s.m4a")
    # No requested length: only the requested album itself.
    unknown = {k: v for k, v in target.items() if k != "duration"}
    with pytest.raises(ValueError):
        resolved(unknown, [saavn("Summer Hits 2026", "s", duration=240)])
    same = saavn("Breathless - Single", "b", duration=240)
    assert resolved(unknown, [same]).endswith("/b.m4a")


def test_one_alteration_is_never_another():
    """Codex confirmation round (5 Nov): the check compared only 'altered or
    not', so a reverb request took the 8D upload."""
    target = {"artist": "CYRIL", "title": "Breathless", "duration": 171}
    target["album"] = "Breathless (Reverb)"
    for album in ("Breathless (8D)", "Breathless (Slowed + Reverb)", "Breathless"):
        with pytest.raises(ValueError):
            resolved(target, [saavn(album, "x")])
    # An altered version is one upload: only the requested album itself.
    with pytest.raises(ValueError):
        resolved(target, [saavn("Reverb Hits", "r")])
    assert resolved(target, [saavn("Breathless (Reverb)", "r")]).endswith("/r.m4a")


def test_a_match_lasts_hours_and_is_rechecked_with_one_range_request():
    """#192: a song looked up before the tap still plays from its match; an
    address older than PROBE_SECONDS is checked again, and looked up afresh
    only when the CDN no longer serves it."""
    from server import remote

    calls = []
    target = {"artist": "Artist", "title": "Song", "album": "Album", "duration": 180}
    candidate = {
        "channel": "Artist",
        "title": "Song",
        "album": "Album",
        "duration": 180,
        "source_url": "https://aac.saavncdn.com/music.m4a",
    }
    status = {"code": 206}

    async def run():
        def search(*args):
            calls.append("search")
            return [candidate]

        def transport(req):
            calls.append("probe")
            return httpx.Response(status["code"], headers={"content-type": "audio/mp4"})

        now = [1000.0]
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            resolver = RemoteAudio(search)
            resolver.clock = lambda: now[0]
            url = candidate["source_url"]
            assert await resolver.resolve(client, target) == url
            now[0] += remote.PROBE_SECONDS - 1
            assert await resolver.resolve(client, target) == url  # As is.
            now[0] += 3600
            assert await resolver.resolve(client, target) == url  # Re-checked.
            now[0] += 10
            assert await resolver.resolve(client, target) == url  # As is again.
            now[0] += 3600
            status["code"] = 404  # Gone from the CDN: looked up again.
            with pytest.raises(ValueError):
                await resolver.resolve(client, target)
            now[0] += remote.RESOLVED_SECONDS
            status["code"] = 206
            assert await resolver.resolve(client, target) == url  # Expired.

    asyncio.run(run())
    assert calls == [
        "search",
        "probe",
        "probe",
        "probe",
        "search",
        "probe",
        "search",
        "probe",
    ]


def test_the_match_cache_drops_the_least_recently_used():
    from server import remote

    resolver = RemoteAudio(lambda *_: [])
    for n in range(remote.RESOLVED_ENTRIES + 5):
        resolver.cache[n] = (float("inf"), str(n), 0)
        resolver.cache.move_to_end(n)
    resolver.cache.move_to_end(0)

    async def one_more():
        candidate = {
            "channel": "A",
            "title": "S",
            "album": "B",
            "duration": 100,
            "source_url": "https://aac.saavncdn.com/x.m4a",
        }
        resolver.search = lambda *_: [candidate]
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(206, headers={"content-type": "audio/mp4"})
            )
        ) as client:
            await resolver.resolve(
                client, {"artist": "A", "title": "S", "album": "B", "duration": 100}
            )

    asyncio.run(one_more())
    assert len(resolver.cache) == remote.RESOLVED_ENTRIES
    assert 0 in resolver.cache and 1 not in resolver.cache
