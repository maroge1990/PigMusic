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


def resolver_with(youtube, jiosaavn, tmp_path, asked=None):
    from server.remote import RemoteAudio

    def relay_search(source, track):
        asked.append(source) if asked is not None else None
        return []

    return RemoteAudio(
        jiosaavn,
        directory=tmp_path / "relay",
        relay_search=relay_search,
        youtube=youtube,
        relays=(),
    )


def test_a_tap_jiosaavn_misses_plays_from_youtube_through_the_server(
    tmp_path, monkeypatch
):
    asked = []
    youtube = FakeYouTube([ON_YOUTUBE])
    resolver = resolver_with(youtube, lambda *_: [], tmp_path, asked)

    async def scenario():
        async with httpx.AsyncClient() as client:
            found = await resolver.stream(client, SONG, "member")
            assert found["source"] == "youtube" and "relay" in found
            stream = resolver.relay(found["relay"], "member")
            assert isinstance(stream, YouTubeStream) and stream.video_id == VIDEO
            assert resolver.relay(found["relay"], "someone else") is None
            assert asked == []  # FreeMp3Cloud and zvu4no are not asked (D-89).
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
    assert remote.relays == ()  # FreeMp3Cloud and zvu4no out of instant (D-89).
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
