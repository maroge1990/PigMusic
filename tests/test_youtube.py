"""YouTube for instant listening, the one-week trial (#193, D-91, D-95)."""

import asyncio
import hashlib
import io
import json
import time
import zipfile

import httpx
import pytest

from server import youtube as youtube_module
from server.providers.contracts import ProviderError
from server.youtube import YouTube, YouTubeStream, release_version

VIDEO = "abcdefghijk"


def address(url="https://rr1---sn-x.googlevideo.com/videoplayback?expire={}&id=1"):
    return {
        "url": url.format(int(time.time()) + 6 * 3600),
        "expires": int(time.time()) + 6 * 3600,
        "size": 4096,
        "headers": {"User-Agent": "fixture"},
        "version": "2026.08.19",
    }


def test_an_address_is_kept_until_shortly_before_it_expires_and_shared(tmp_path):
    youtube = YouTube(tmp_path)
    calls = []

    def resolve(video_id):
        calls.append(video_id)
        time.sleep(0.05)
        return address()

    youtube.resolve = resolve

    async def scenario():
        first = await asyncio.gather(*(youtube.address(VIDEO) for _ in range(3)))
        assert len(calls) == 1  # One lookup for three listeners.
        assert all(a is first[0] for a in first)
        assert await youtube.address(VIDEO) is first[0]
        assert len(calls) == 1
        # Within the margin before expiry it is looked up again.
        until, found = youtube.addresses[VIDEO]
        youtube.addresses[VIDEO] = (time.time() - 1, found)
        await youtube.address(VIDEO)
        assert len(calls) == 2
        await youtube.address(VIDEO, fresh=True)
        assert len(calls) == 3
        with pytest.raises(ValueError):
            await youtube.address("../../etc")

    asyncio.run(scenario())


def test_an_address_already_past_its_margin_is_never_kept(tmp_path):
    youtube = YouTube(tmp_path)
    youtube.resolve = lambda video_id: {**address(), "expires": int(time.time()) + 60}

    async def scenario():
        with pytest.raises(TimeoutError):
            await youtube.address(VIDEO)
        assert youtube.addresses == {}

    asyncio.run(scenario())


def test_refusals_purge_the_cache_first_then_ask_for_an_update(tmp_path):
    youtube = YouTube(tmp_path)
    counted = []
    youtube.performance = type(
        "Counter", (), {"record": lambda self, *event: counted.append(event)}
    )()
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "player.json").write_text("{}")
    youtube.refused_now()
    assert cache.exists() and not youtube.wants_update  # One refusal: nothing.
    youtube.refused_now()
    assert not cache.exists() and not youtube.wants_update  # Purged first.
    cache.mkdir()
    youtube.refused_now()
    assert cache.exists() and youtube.wants_update  # Purged recently: update.
    assert counted == [("remote-source", "youtube", "refused")] * 3  # In Admin.


def zipapp(version):
    body = io.BytesIO()
    with zipfile.ZipFile(body, "w") as archive:
        archive.writestr("yt_dlp/version.py", f"__version__ = '{version}'\n")
        archive.writestr("__main__.py", "")
    return body.getvalue()


@pytest.fixture(scope="module")
def releases():
    """yt-dlp zipapps built from the installed package: `working(version)`
    imports and runs; `broken(version)` fails on import."""
    import functools
    from pathlib import Path

    import yt_dlp

    package = Path(yt_dlp.__file__).parent

    @functools.cache
    def build(version, broken=False):
        body = io.BytesIO()
        with zipfile.ZipFile(body, "w", zipfile.ZIP_STORED) as archive:
            for path in package.rglob("*.py"):
                name = "yt_dlp/" + path.relative_to(package).as_posix()
                text = path.read_bytes()
                if name == "yt_dlp/version.py":
                    text = f"__version__ = '{version}'\nRELEASE_GIT_HEAD = ''\nVARIANT = None\nUPDATE_HINT = None\nCHANNEL = 'stable'\nORIGIN = 'yt-dlp/yt-dlp'\n_pkg_version = '{version}'\n".encode()
                if broken and name == "yt_dlp/__init__.py":
                    text = b"raise ImportError('incompatible release')\n"
                archive.writestr(name, text)
            archive.writestr("__main__.py", "")
        return body.getvalue()

    return build


def test_an_update_installs_only_a_newer_release_matching_its_checksum(
    tmp_path, monkeypatch, releases
):
    youtube = YouTube(tmp_path)
    newer = releases("2099.01.02")
    published = {"sum": hashlib.sha256(newer).hexdigest(), "body": newer}
    fetched = []

    def download(session, url, limit):
        fetched.append(url.rsplit("/", 1)[1])
        if url.endswith("SHA2-256SUMS"):
            return f"{published['sum']}  yt-dlp\n{'0' * 64}  yt-dlp.exe\n".encode()
        return published["body"]

    monkeypatch.setattr(youtube_module, "download", download)
    # A body that does not match the published checksum is refused.
    published["body"] = releases("2099.01.03")
    assert youtube.update() is None
    assert youtube.update_path() is None
    # At most every 6 hours.
    published["body"] = newer
    assert youtube.update() is None and fetched == ["SHA2-256SUMS", "yt-dlp"]
    (tmp_path / "update" / "checked").unlink()
    assert youtube.update() == "2099.01.02"
    installed = youtube.update_path()
    assert installed.name == "yt-dlp-2099.01.02.zip"
    assert installed.read_bytes() == newer
    # The worker runs it.
    assert youtube.ask({"operation": "version"}, installed) == {"version": "2099.01.02"}
    # A changed file on disk is not used.
    installed.write_bytes(b"tampered")
    assert youtube.update_path() is None
    # An older release than the pinned one is never installed.
    (tmp_path / "update" / "checked").unlink()
    older = releases("2001.01.01")
    published.update(sum=hashlib.sha256(older).hexdigest(), body=older)
    assert youtube.update() is None


def test_a_release_that_does_not_run_in_the_worker_is_never_activated(
    tmp_path, monkeypatch, releases
):
    """Codex, sources round 1 (high): a checksum-valid release that breaks on
    import must never replace the pinned version."""
    youtube = YouTube(tmp_path)
    broken = releases("2099.02.03", broken=True)

    def download(session, url, limit):
        if url.endswith("SHA2-256SUMS"):
            return f"{hashlib.sha256(broken).hexdigest()}  yt-dlp\n".encode()
        return broken

    monkeypatch.setattr(youtube_module, "download", download)
    assert youtube.update() is None
    assert youtube.update_path() is None
    assert youtube.quarantined() == {"2099.02.03"}
    assert not list((tmp_path / "update").glob("*.zip"))
    assert not list((tmp_path / "update").glob(".yt-dlp-*"))


def test_an_installed_update_that_breaks_is_set_aside_for_the_pinned_one(
    tmp_path, releases
):
    """Codex, sources round 1 (high): an update that fails where the pinned
    version works is quarantined for good, also for a new YouTube instance."""
    folder = tmp_path / "update"
    folder.mkdir()
    broken = releases("2099.03.04", broken=True)
    (folder / "yt-dlp-2099.03.04.zip").write_bytes(broken)
    (folder / "verified.json").write_text(
        json.dumps(
            {
                "version": "2099.03.04",
                "sha256": hashlib.sha256(broken).hexdigest(),
                "file": "yt-dlp-2099.03.04.zip",
            }
        )
    )
    youtube = YouTube(tmp_path)
    assert youtube.update_path() is not None
    asked = []

    def ask(request, update):
        asked.append(update)
        if update is not None:  # The real worker with the broken release.
            return YouTube.ask(youtube, {"operation": "version"}, update)
        return address()

    youtube.ask = ask
    assert youtube.resolve(VIDEO)["url"].startswith("https://")
    assert asked[0] is not None and asked[1] is None
    assert YouTube(tmp_path).update_path() is None  # Persisted.


def test_a_cancelled_search_keeps_its_slot_until_its_thread_ends(tmp_path):
    """Codex, sources round 1 (medium): a search whose caller stopped waiting
    (JioSaavn won) still holds its slot, and look-ahead searches with its own
    client and slot, so taps stay bounded and never queue behind it."""
    import threading

    youtube = YouTube(tmp_path)
    release = threading.Event()
    running = []

    class Searcher:
        def songs(self, query):
            running.append(query)
            release.wait(5)
            return []

    youtube_module_searcher = youtube_module.GuardedSearch
    youtube_module.GuardedSearch = Searcher
    try:

        async def scenario():
            taps = [
                asyncio.ensure_future(youtube.songs({"artist": "A", "title": str(n)}))
                for n in range(2)
            ]
            ahead = asyncio.ensure_future(
                youtube.songs({"artist": "A", "title": "ahead"}, speculative=True)
            )
            await asyncio.sleep(0.1)
            for task in taps:
                task.cancel()  # JioSaavn won.
            await asyncio.sleep(0.05)
            late = asyncio.ensure_future(
                youtube.songs({"artist": "A", "title": "late"})
            )
            await asyncio.sleep(0.1)
            assert sorted(running) == ["A 0", "A 1", "A ahead"]  # Still bounded.
            release.set()
            assert await late == []
            assert await ahead == []
            assert "A late" in running

        asyncio.run(scenario())
    finally:
        release.set()
        youtube_module.GuardedSearch = youtube_module_searcher


def test_release_version_reads_the_zipapp():
    assert release_version(zipapp("2026.11.02.1")) == "2026.11.02.1"
    with pytest.raises(ProviderError):
        release_version(zipapp("not a version"))


def stream_with(responses, tmp_path):
    """A YouTubeStream whose CDN answers `responses` in turn."""
    asked = []

    def handler(request):
        asked.append((str(request.url), request.headers.get("range")))
        status = responses.pop(0)
        return httpx.Response(
            status,
            content=b"audio" if status in {200, 206} else b"",
            headers={"Content-Range": "bytes 0-4/4096"} if status == 206 else {},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    youtube = YouTube(tmp_path)
    fresh = []

    async def renewed(video_id, fresh_flag=False):
        fresh.append(fresh_flag)
        return address("https://rr2---sn-y.googlevideo.com/videoplayback?expire={}")

    youtube.address = lambda video_id, fresh=False: renewed(video_id, fresh)
    return YouTubeStream(youtube, VIDEO, address(), client), asked, fresh, client


def test_a_range_passes_through_and_a_refused_address_is_renewed_once(tmp_path):
    async def scenario():
        stream, asked, fresh, client = stream_with([206], tmp_path)
        reply = await stream.response("GET", "bytes=0-4")
        assert reply.status_code == 206
        assert reply.headers["content-range"] == "bytes 0-4/4096"
        assert asked[0][1] == "bytes=0-4" and fresh == []
        await client.aclose()

        stream, asked, fresh, client = stream_with([403, 206], tmp_path)
        reply = await stream.response("GET", "bytes=0-4")
        assert reply.status_code == 206 and fresh == [True]
        assert "rr2---" in asked[1][0]  # The fresh address.
        await client.aclose()

        stream, asked, fresh, client = stream_with([403, 403], tmp_path)
        reply = await stream.response("GET", None)
        assert reply.status_code == 503 and len(asked) == 2
        await client.aclose()

    asyncio.run(scenario())


def test_a_stream_never_follows_an_address_off_youtubes_cdn(tmp_path):
    async def scenario():
        stream, asked, fresh, client = stream_with([206], tmp_path)
        stream.address = {**address(), "url": "https://example.com/audio.m4a"}
        with pytest.raises(ProviderError):
            await stream.response("GET", "bytes=0-4")
        assert asked == []
        await client.aclose()

    asyncio.run(scenario())


def test_the_worker_refuses_anything_but_a_video_id():
    from server.providers.youtube_worker import resolve

    for value in (None, "", "https://evil.example/", "abc", "a" * 12):
        with pytest.raises(ProviderError):
            resolve(value, None)


def test_the_worker_runs_isolated_and_reports_errors_as_json(tmp_path):
    youtube = YouTube(tmp_path)
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"video_id": "not-an-id"}))
    result = youtube.run_worker(request, None)
    assert json.loads(result.stdout.decode().strip().splitlines()[-1]) == {
        "error": "invalid_response"
    }


class FakeYouTube:
    """YouTube's search answers `songs`; each address lookup is counted."""

    def __init__(self, songs, delay=0.0):
        self.songs_found = songs
        self.delay = delay
        self.searched = []
        self.looked_up = []

    async def songs(self, track, speculative=False):
        self.searched.append(track["title"])
        await asyncio.sleep(self.delay)
        return self.songs_found

    async def address(self, video_id, fresh=False):
        self.looked_up.append(video_id)
        return address()


SONG = {"artist": "Artist", "title": "Song", "album": "Album", "duration": 200}
ON_YOUTUBE = {
    "video_id": VIDEO,
    "channel": "Artist",
    "title": "Song",
    "album": "Album",
    "duration": 201,
    "explicit": False,
}


def resolver_with(youtube, jiosaavn, tmp_path):
    from server.remote import RemoteAudio

    return RemoteAudio(jiosaavn, youtube=youtube)


def test_a_tap_jiosaavn_misses_plays_from_youtube_through_the_server(
    tmp_path, monkeypatch
):
    youtube = FakeYouTube([ON_YOUTUBE])
    resolver = resolver_with(youtube, lambda *_: [], tmp_path)

    async def scenario():
        async with httpx.AsyncClient() as client:
            found = await resolver.stream(client, SONG, "member")
            assert found["source"] == "youtube" and "token" in found
            stream = resolver.streamed(found["token"], "member")
            assert isinstance(stream, YouTubeStream) and stream.video_id == VIDEO
            assert resolver.streamed(found["token"], "someone else") is None
            # A wrong song on YouTube is no match: the shared song rule decides.
            youtube.songs_found = [{**ON_YOUTUBE, "title": "Song (Live)"}]
            with pytest.raises(ValueError, match="No source has"):
                await resolver.stream(
                    client, {**SONG, "title": "Song", "artist": "Other"}, "member"
                )

    asyncio.run(scenario())


def test_jiosaavn_wins_when_both_have_the_song(tmp_path, monkeypatch):
    from server.remote import RemoteAudio

    async def probe(client, url):
        return None

    monkeypatch.setattr(RemoteAudio, "probe", staticmethod(probe))
    candidate = {
        "channel": "Artist",
        "title": "Song",
        "album": "Album",
        "duration": 200,
        "source_url": "https://aac.saavncdn.com/song.m4a",
    }
    youtube = FakeYouTube([ON_YOUTUBE], delay=0.5)
    resolver = resolver_with(youtube, lambda *_: [candidate], tmp_path)

    async def scenario():
        async with httpx.AsyncClient() as client:
            found = await resolver.stream(client, SONG, "member")
            assert found == {"url": candidate["source_url"], "source": "jiosaavn"}
            assert youtube.searched == ["Song"]  # Asked together (D-89),
            await asyncio.sleep(0.6)
            assert youtube.looked_up == []  # but dropped once JioSaavn answered.

    asyncio.run(scenario())


def test_look_ahead_warms_youtube_so_the_tap_waits_for_neither_source(tmp_path):
    jiosaavn = []
    youtube = FakeYouTube([ON_YOUTUBE])

    def search(query, limit):
        jiosaavn.append(query)
        return []

    resolver = resolver_with(youtube, search, tmp_path)

    async def scenario():
        async with httpx.AsyncClient() as client:
            found = await resolver.match(client, SONG)
            assert found["youtube"] == VIDEO
            assert len(jiosaavn) == 1 and youtube.searched == ["Song"]
            tapped = await resolver.stream(client, SONG, "member")
            assert tapped["source"] == "youtube"
            # Neither JioSaavn nor YouTube's search was asked again.
            assert len(jiosaavn) == 1 and youtube.searched == ["Song"]

    asyncio.run(scenario())


def test_a_youtube_refusal_leaves_the_tap_uncertain_not_a_miss(tmp_path):
    class Refusing(FakeYouTube):
        async def songs(self, track, speculative=False):
            raise ProviderError("access_required")

    resolver = resolver_with(Refusing([]), lambda *_: [], tmp_path)

    async def scenario():
        async with httpx.AsyncClient() as client:
            with pytest.raises(TimeoutError):
                await resolver.stream(client, SONG, "member")

    asyncio.run(scenario())


def test_a_tap_through_the_api_streams_youtubes_bytes_by_range(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from server.media import save_settings
    from test_security import PASSWORD, protected

    cdn = []

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"\x00\x00\x00\x18ftypM4A "

    async def open_cdn(self, method, range_header):
        cdn.append((method, range_header, self.address["url"]))
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    206,
                    stream=Body(),
                    headers={"Content-Range": "bytes 0-11/4096"},
                )
            )
        )
        request = client.build_request("GET", self.address["url"])
        return await client.send(request, stream=True)

    monkeypatch.setattr(YouTubeStream, "open", open_cdn)
    app, _ = protected(tmp_path, monkeypatch)
    save_settings(app.state.store, portraits=False, remote_playback=True)
    remote = app.state.look_ahead.remote_audio
    remote.search = lambda *_: []
    remote.youtube = FakeYouTube([ON_YOUTUBE])
    token = app.state.auth.login("mark", PASSWORD, "device", "phone", "ios", "x")
    with TestClient(
        app,
        base_url="https://private.ts.net",
        headers={"Authorization": "Bearer " + token["token"]},
    ) as client:
        tapped = client.post("/api/v1/remote-playback", json=SONG)
        assert tapped.status_code == 200, tapped.text
        assert tapped.json()["source"] == "Remote · YouTube"
        played = client.get(tapped.json()["url"], headers={"Range": "bytes=0-11"})
        assert played.status_code == 206
        assert played.headers["content-type"] == "audio/mp4"
        assert played.headers["content-range"] == "bytes 0-11/4096"
        assert played.content.startswith(b"\x00\x00\x00\x18ftyp")
        assert [(m, r) for m, r, _ in cdn] == [("GET", "bytes=0-11")]
        assert "googlevideo.com" in cdn[0][2]


def test_only_the_update_that_failed_is_set_aside(tmp_path, releases):
    """Codex, sources confirmation (medium): an update activated while a
    lookup falls back to the pinned version must not be the one set aside."""
    folder = tmp_path / "update"
    folder.mkdir()

    def install(version, body):
        (folder / f"yt-dlp-{version}.zip").write_bytes(body)
        (folder / "verified.json").write_text(
            json.dumps(
                {
                    "version": version,
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "file": f"yt-dlp-{version}.zip",
                }
            )
        )

    install("2099.04.05", releases("2099.04.05", broken=True))
    youtube = YouTube(tmp_path)

    def ask(request, update):
        if update is not None:
            return {"error": "invalid_response"}
        # A healthy newer release is activated during the pinned retry.
        install("2099.05.06", releases("2099.05.06"))
        return address()

    youtube.ask = ask
    youtube.resolve(VIDEO)
    assert youtube.quarantined() == {"2099.04.05"}
    assert youtube.update_path().name == "yt-dlp-2099.05.06.zip"


def test_youtubes_answer_is_closed_however_the_listener_leaves(tmp_path):
    """Codex, sources round 2 (high): a listener who skips or seeks away
    must never leave the connection to YouTube's CDN open."""

    class Body(httpx.AsyncByteStream):
        def __init__(self):
            self.closed = False

        async def __aiter__(self):
            for _ in range(1000):
                yield b"a" * 1024
                await asyncio.sleep(0.01)

        async def aclose(self):
            self.closed = True

    def stream_of(body):
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(206, stream=body)
            )
        )
        youtube = YouTube(tmp_path)
        return YouTubeStream(youtube, VIDEO, address(), client)

    scope = {
        "type": "http",
        "method": "GET",
        "headers": [],
        "asgi": {"spec_version": "2.4"},
    }

    async def receive():
        await asyncio.sleep(3600)

    async def scenario():
        # The send fails while a chunk is going out.
        body = Body()
        reply = await stream_of(body).response("GET", "bytes=0-")
        sent = []

        async def failing(message):
            sent.append(message["type"])
            if message["type"] == "http.response.body":
                raise OSError("listener gone")

        with pytest.raises(Exception):
            await reply(scope, receive, failing)
        assert body.closed
        # The response is cancelled mid-stream (the listener sought away).
        body = Body()
        reply = await stream_of(body).response("GET", "bytes=0-")

        async def slow(message):
            await asyncio.sleep(0.05)

        task = asyncio.ensure_future(reply(scope, receive, slow))
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert body.closed

    asyncio.run(scenario())


def test_a_head_of_unknown_size_never_says_the_file_is_empty(tmp_path):
    """Codex, sources round 2 (medium): a HEAD with no known size must not
    answer Content-Length: 0; it learns the size from one byte's range."""

    def stream_with_headers(headers):
        asked = []

        def handler(request):
            asked.append(request.headers.get("range"))
            return httpx.Response(206, content=b"a", headers=headers)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        youtube = YouTube(tmp_path)
        return YouTubeStream(youtube, VIDEO, {**address(), "size": None}, client), asked

    async def scenario():
        stream, asked = stream_with_headers({"Content-Range": "bytes 0-0/4096"})
        reply = await stream.response("HEAD", None)
        assert reply.status_code == 200 and asked == ["bytes=0-0"]
        assert reply.headers["content-length"] == "4096"
        assert reply.headers["content-type"] == "audio/mp4"
        stream, asked = stream_with_headers({})
        reply = await stream.response("HEAD", None)
        assert "content-length" not in reply.headers
        assert reply.headers["content-type"] == "audio/mp4"

    asyncio.run(scenario())


class Recorder:
    def __init__(self):
        self.events = []

    def record(self, metric, label, outcome, ms=None, detail=None, at=None):
        self.events.append((metric, label, outcome, detail))


def test_a_no_match_records_what_each_source_found_and_why(tmp_path):
    """#282: YouTube answered no match for 41 of 52 lookups and nothing said
    why. Each source's no match keeps the query, the number of results and
    the first few with the reason each was not the song; never who asked."""
    rejected = [
        {**ON_YOUTUBE, "video_id": "a" * 11, "title": "Other Song"},
        {**ON_YOUTUBE, "video_id": "b" * 11, "album": "Live at Leeds"},
        {**ON_YOUTUBE, "video_id": "c" * 11, "duration": None},
        {**ON_YOUTUBE, "video_id": "d" * 11, "duration": 260},
        {**ON_YOUTUBE, "video_id": "e" * 11, "channel": "Somebody Else"},
    ]
    youtube = FakeYouTube(rejected)
    saavn = [{"channel": "Artist", "title": "Song", "album": "Album", "duration": ""}]
    resolver = resolver_with(youtube, lambda *_: saavn, tmp_path)
    resolver.performance = Recorder()

    async def scenario():
        async with httpx.AsyncClient() as client:
            with pytest.raises(ValueError, match="No source has"):
                await resolver.stream(client, SONG, "member")

    asyncio.run(scenario())
    misses = {e[1]: e[3] for e in resolver.performance.events if e[2] == "no match"}
    assert set(misses) == {"jiosaavn", "youtube"}
    found = json.loads(misses["youtube"])
    assert found["query"] == "Artist Song" and found["seconds"] == 200
    assert found["results"] == 5
    # Every result is counted by reason, the near misses below the first few too.
    assert found["reasons"] == {
        "title": 1,
        "version": 1,
        "no length": 1,
        "length": 1,
        "artist": 1,
    }
    assert [(r["title"], r["seconds"], r["why"]) for r in found["top"]] == [
        ("Other Song", 201, "title"),
        ("Song", 201, "version"),
        ("Song", None, "no length"),
    ]
    assert json.loads(misses["jiosaavn"])["top"][0]["why"] == "no length"
    assert "member" not in misses["youtube"] + misses["jiosaavn"]


def test_a_no_match_detail_is_whole_json_however_long_the_names(tmp_path):
    import sqlite3

    from server.performance import DETAIL_CHARS, Performance
    from server.remote import no_match_detail

    long = {**ON_YOUTUBE, "title": "Ş" * 500, "channel": "A" * 500, "album": "B" * 500}
    text = no_match_detail({**SONG, "title": "T" * 500}, "Q" * 500, [long] * 10)
    assert len(text) <= DETAIL_CHARS
    value = json.loads(text)
    assert value["results"] == 10 and len(value["top"]) == 3
    counters = Performance(tmp_path)
    counters.record("remote-source", "youtube", "no match", 1, text, at=time.time())
    counters.flush()
    with sqlite3.connect(counters.path) as conn:
        (stored,) = conn.execute("SELECT detail FROM events").fetchone()
    assert json.loads(stored) == value


def test_a_malformed_title_never_holds_back_the_counters(tmp_path):
    """Codex round 1 (high): a YouTube title with a lone surrogate made the
    miss unwritable, so every flush failed and an unrelated tap was never
    saved. Its detail is storable JSON, and both events are written."""
    import sqlite3

    from server.performance import DETAIL_CHARS, Performance
    from server.remote import no_match_detail

    youtube = FakeYouTube([{**ON_YOUTUBE, "title": "Bad \udcff title"}])
    resolver = resolver_with(youtube, lambda *_: [], tmp_path)
    resolver.performance = Performance(tmp_path)

    async def scenario():
        async with httpx.AsyncClient() as client:
            with pytest.raises(ValueError, match="No source has"):
                await resolver.stream(client, SONG, "member")

    asyncio.run(scenario())
    resolver.performance.record("tap", "Prepared mix", "played", 80)
    resolver.performance.flush()
    with sqlite3.connect(resolver.performance.path) as conn:
        rows = conn.execute("SELECT label,outcome,detail FROM events").fetchall()
    details = {label: detail for label, outcome, detail in rows}
    assert details.keys() == {"jiosaavn", "youtube", "Prepared mix"}
    assert json.loads(details["youtube"])["top"][0]["title"] == "Bad ? title"
    # Whole JSON within the limit even when escaping lengthens the query and
    # the requested length is absurd.
    text = no_match_detail(
        {**SONG, "duration": 1e300},
        "\x01" * 500,
        [{**ON_YOUTUBE, "title": "\x02" * 500}],
    )
    assert len(text) <= DETAIL_CHARS and json.loads(text)["seconds"] is None


def test_a_detail_that_cannot_be_made_never_changes_the_answer(tmp_path, monkeypatch):
    """Codex round 1 (medium): an oversized length on a rejected result
    raised while the miss was described, turning a no match (404, kept 30
    minutes) into a failure (503, kept 2 minutes). Diagnostics are best
    effort: whatever they meet, the answer and its memory are the same."""
    from server import remote

    rejected = {**ON_YOUTUBE, "title": "Other Song", "duration": 10**400}

    def outcome(resolver):
        async def scenario():
            async with httpx.AsyncClient() as client:
                with pytest.raises(ValueError, match="No source has"):
                    await resolver.stream(client, SONG, "member")

        asyncio.run(scenario())
        until, kind, _ = resolver.misses[resolver.miss_key(SONG)]
        assert kind is ValueError
        assert until - time.monotonic() > remote.FAILURE_SECONDS
        return [e for e in resolver.performance.events if e[1] == "youtube"]

    resolver = resolver_with(FakeYouTube([rejected]), lambda *_: [], tmp_path)
    resolver.performance = Recorder()
    (event,) = outcome(resolver)
    assert json.loads(event[3])["top"][0]["seconds"] is None

    def broken(*args):
        raise RuntimeError("the detail builder broke")

    monkeypatch.setattr(remote, "no_match_detail", broken)
    resolver = resolver_with(FakeYouTube([rejected]), lambda *_: [], tmp_path)
    resolver.performance = Recorder()
    (event,) = outcome(resolver)
    assert event[3] == remote.UNRECORDED

    class Failing:
        def record(self, *args, **kwargs):
            raise RuntimeError("the counters broke")

    resolver = resolver_with(FakeYouTube([rejected]), lambda *_: [], tmp_path)
    resolver.performance = Failing()
    resolver.performance.events = []
    outcome(resolver)


def test_the_reason_given_agrees_with_the_song_rule():
    """`why` explains `acceptable`; it must never call a rejected result
    matched, or a matched one rejected."""
    from server.remote import acceptable, why

    tracks = [
        SONG,
        {**SONG, "album": "Live at Leeds"},
        {**SONG, "duration": None},
        {**SONG, "explicit_edition": True},
        {**SONG, "isrcs": ["GBAAA0000001"]},
        {**SONG, "title": "Song (Remastered 2011)"},
        {**SONG, "title": "Song (Slowed)"},
    ]
    candidates = [
        ON_YOUTUBE,
        {**ON_YOUTUBE, "album": "Other Album"},
        {**ON_YOUTUBE, "album": "Live at Leeds"},
        {**ON_YOUTUBE, "title": "Song (Remix)"},
        {**ON_YOUTUBE, "title": "Song (Slowed)"},
        {**ON_YOUTUBE, "title": "Song - 2011 Remaster"},
        {**ON_YOUTUBE, "channel": "Artist, Guest"},
        {**ON_YOUTUBE, "channel": "Other"},
        {**ON_YOUTUBE, "duration": 230},
        {**ON_YOUTUBE, "duration": None},
        {**ON_YOUTUBE, "isrc": "GBAAA0000002"},
        {**ON_YOUTUBE, "isrc": "GBAAA0000001", "album": "Other Album"},
    ]
    for track in tracks:
        for c in candidates:
            reason = why(track, c)
            assert (reason == "matched") == acceptable(track, c), (track, c, reason)


def issuing(resolver):
    """`stream` with the lookup already answered by YouTube."""

    async def shared(client, track):
        return {"youtube": VIDEO, "address": address(), "source": "youtube"}

    resolver.shared = shared
    return lambda user: asyncio.run(resolver.stream(None, SONG, user))["token"]


def test_stream_tokens_drop_only_the_expired_and_stay_within_their_caps(
    tmp_path, monkeypatch
):
    """Codex, #201 round 1 (medium): each YouTube token rebuilt the whole
    token map, which had no cap. Expired tokens now go from the front only,
    and each person, and everyone together, keep at most a fixed number:
    when full, the oldest goes, and only its owner's."""
    from server import remote

    monkeypatch.setattr(remote, "STREAMS_PER_PERSON", 3)
    monkeypatch.setattr(remote, "STREAMS", 5)
    resolver = resolver_with(FakeYouTube([]), lambda *_: [], tmp_path)
    issue = issuing(resolver)
    mark = [issue("mark") for _ in range(4)]
    assert resolver.streamed(mark[0], "mark") is None  # Mark's oldest went.
    assert all(resolver.streamed(t, "mark") for t in mark[1:])
    sam = [issue("sam") for _ in range(3)]
    # Five in all: the oldest overall (Mark's) went, Sam's stay.
    assert len(resolver.streams) == 5
    assert resolver.streamed(mark[1], "mark") is None
    assert all(resolver.streamed(t, "sam") for t in sam)
    assert {u: len(h) for u, h in resolver.held.items()} == {"mark": 2, "sam": 3}
    # Expired tokens leave from the front when the next is issued.
    now = time.monotonic()
    monkeypatch.setattr(remote.time, "monotonic", lambda: now + remote.TOKEN_SECONDS)
    fresh = issue("sam")
    assert list(resolver.streams) == [fresh]
    assert {u: list(h) for u, h in resolver.held.items()} == {"sam": [fresh]}


@pytest.mark.loop_timing
def test_issuing_a_token_with_the_map_full_is_quick(tmp_path):
    from server import remote

    resolver = resolver_with(FakeYouTube([]), lambda *_: [], tmp_path)
    issuing(resolver)

    async def fill_and_time():
        for n in range(remote.STREAMS):
            await resolver.stream(None, SONG, f"person-{n % 10}")
        assert len(resolver.streams) == remote.STREAMS
        slowest = 0
        for _ in range(50):
            began = time.perf_counter()
            await resolver.stream(None, SONG, "person-0")
            slowest = max(slowest, time.perf_counter() - began)
        return slowest

    slowest = asyncio.run(fill_and_time())
    assert len(resolver.streams) == remote.STREAMS
    assert slowest < 0.005, slowest


def test_a_tap_on_a_chart_song_with_no_length_plays_from_another_album(
    tmp_path, monkeypatch
):
    """D-130 (#282): live, "Foo Fighters - Everlong" (no known length) was
    refused from The Colour And The Shape; it now plays from JioSaavn, which
    still wins over YouTube (D-89)."""
    from server.remote import RemoteAudio

    async def probe(client, url):
        return None

    monkeypatch.setattr(RemoteAudio, "probe", staticmethod(probe))
    everlong = {"artist": "Foo Fighters", "title": "Everlong", "album": "Greatest Hits"}
    on_saavn = {
        "channel": "Foo Fighters",
        "title": "Everlong",
        "album": "The Colour And The Shape",
        "duration": "250",
        "source_url": "https://aac.saavncdn.com/everlong_320.mp4",
    }
    youtube = FakeYouTube(
        [
            {
                **ON_YOUTUBE,
                "channel": "Foo Fighters",
                "title": "Everlong",
                "duration": 250,
            }
        ]
    )
    resolver = resolver_with(youtube, lambda *_: [on_saavn], tmp_path)

    async def scenario():
        async with httpx.AsyncClient() as client:
            found = await resolver.stream(client, everlong, "member")
            assert found == {"url": on_saavn["source_url"], "source": "jiosaavn"}

    asyncio.run(scenario())


def test_a_tap_with_only_karaoke_and_tribute_results_is_still_no_match(tmp_path):
    """D-130 relaxes the album, never the artist or the version: karaoke,
    tribute and lullaby copies stay refused, and the no match says why."""
    everlong = {"artist": "Foo Fighters", "title": "Everlong", "album": "Greatest Hits"}
    karaoke = [
        {
            "channel": "ZZang KARAOKE",
            "title": "Everlong",
            "album": "Everlong (Karaoke Version)",
            "duration": "250",
            "source_url": "https://aac.saavncdn.com/k_320.mp4",
        },
        {
            "channel": "Foo Fighters",
            "title": "Everlong",
            "album": "Lullaby Versions of Foo Fighters",
            "duration": "240",
            "source_url": "https://aac.saavncdn.com/l_320.mp4",
        },
        {
            "channel": "Foo Fighters",
            "title": "Everlong",
            "album": "The Karaoke Hits",
            "duration": "250",
            "source_url": "https://aac.saavncdn.com/h_320.mp4",
        },
    ]
    youtube = FakeYouTube(
        [
            {**ON_YOUTUBE, "channel": "String Tribute Players", "title": "Everlong"},
            {
                **ON_YOUTUBE,
                "channel": "Foo Fighters",
                "title": "Everlong (Acoustic Version)",
            },
        ]
    )
    resolver = resolver_with(youtube, lambda *_: karaoke, tmp_path)
    resolver.performance = Recorder()

    async def scenario():
        async with httpx.AsyncClient() as client:
            with pytest.raises(ValueError, match="No source has"):
                await resolver.stream(client, everlong, "member")

    asyncio.run(scenario())
    misses = {e[1]: e[3] for e in resolver.performance.events if e[2] == "no match"}
    assert json.loads(misses["jiosaavn"])["reasons"] == {
        "artist": 1,
        "album": 1,  # The lullaby album: not ordinary.
        "version": 1,  # "Karaoke" in the album.
    }
    assert json.loads(misses["youtube"])["reasons"] == {"artist": 1, "title": 1}
