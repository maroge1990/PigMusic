"""Provider contracts, network policy, identity, health and owner boundaries."""

import io
import json
import os
import shutil
import socket
import stat
import subprocess
import time
from pathlib import Path

import httpcore
import httpx
import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.downloader import Downloader, Cancelled, matches
from server.providers import REGISTRY
from server.providers.contracts import Context, ProviderError
from server.providers.extractor import Extractor, formats, recording
from server.providers.extractor_worker import (
    LimitedBody,
    GuardedRH,
    Quiet,
    canonical,
    candidate,
    execute,
)
from server.providers.health import Health
from server.providers.monochrome import Monochrome
from server.providers import network
from server.providers.runtime import Runtime
from server.audio import probe_file, remux_aac
from server.store import Store
from test_downloader import queued

TARGET = {
    "artist": "Artist",
    "title": "Song",
    "album": "Album",
    "duration": 200,
    "isrcs": ["AA1234567890"],
}
FORMAT = {
    "format_id": "audio-1",
    "ext": "m4a",
    "acodec": "mp4a.40.2",
    "vcodec": "none",
    "abr": 128,
    "protocol": "https",
    "url": "https://cdn.googlevideo.com/audio?secret=never-persist",
}
INFO = {
    "id": "abcdefghijk",
    "artist": "Artist",
    "track": "Song",
    "album": "Album",
    "duration": 200,
    "formats": [FORMAT],
}


def test_recording_evidence_is_independent_of_request_and_rejects_ambiguous_uploads():
    assert recording(INFO, "soundcloud")["channel"] == "Artist"
    assert recording({"title": "Song", "duration": 200}, "soundcloud") is None
    assert not matches(
        TARGET, recording({**INFO, "track": "Song (Live)"}, "soundcloud")
    )
    assert not matches(TARGET, recording({**INFO, "duration": 233}, "soundcloud"))
    assert not matches(TARGET, recording({**INFO, "duration": 30}, "soundcloud"))
    assert recording({**INFO, "has_drm": True}, "soundcloud") is None
    assert recording({**INFO, "is_live": True}, "soundcloud") is None


def test_format_policy_skips_video_storyboards_drm_and_codec_substitution():
    bad = [
        {**FORMAT, "acodec": "none"},
        {**FORMAT, "vcodec": "h264"},
        {**FORMAT, "has_drm": True},
        {**FORMAT, "url": "http://cdn.googlevideo.com/audio", "protocol": "http"},
    ]
    assert formats({"formats": bad}, "native_standard") == []
    assert formats(INFO, "mp3_320") == []
    assert formats(INFO, "aac_256") == []
    assert formats(INFO, "native_standard")[0]["extension"] == ".m4a"
    opus = {**FORMAT, "acodec": "opus", "ext": "webm", "abr": 130}
    assert formats({"formats": [opus]}, "native_standard")[0]["extension"] == ".opus"
    value = candidate(
        {**INFO, "webpage_url": "https://soundcloud.com/artist/song"},
        "soundcloud",
        "native_standard",
    )
    assert "source_url" not in value and "secret" not in json.dumps(value)
    # YouTube was removed as a source (D-60): its IDs are refused.
    with pytest.raises(ProviderError):
        canonical("youtube", "abcdefghijk")
    with pytest.raises(ProviderError):
        canonical("soundcloud", "artist/../../evil")


@pytest.mark.parametrize(
    "url",
    [
        "http://soundcloud.com/a",
        "https://soundcloud.com:444/a",
        "https://user@soundcloud.com/a",
        "https://soundcloud.com.evil.example/a",
        "https://127.0.0.1/a",
        "file:///tmp/audio",
        "https://soundcloud.com./a",
    ],
)
def test_url_boundary_rejects_unsafe_urls(url):
    with pytest.raises(ProviderError, match="network policy"):
        network.validate_url(url, ("soundcloud.com",))


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "::1",
        "::ffff:127.0.0.1",
        "192.0.2.1",
    ],
)
def test_dns_boundary_rejects_private_reserved_and_mapped_addresses(monkeypatch, ip):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))],
    )
    with pytest.raises(ProviderError):
        network.PublicBackend().connect_tcp("soundcloud.com", 443)


def test_connection_pins_the_validated_ip(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **kw: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))
        ],
    )
    seen = []
    monkeypatch.setattr(
        httpcore.SyncBackend,
        "connect_tcp",
        lambda self, host, *a: seen.append(host) or object(),
    )
    network.PublicBackend().connect_tcp("soundcloud.com", 443)
    assert seen == ["8.8.8.8"]


def test_redirects_checked_before_connect_and_secrets_stripped(monkeypatch):
    seen = []

    def handle(request):
        seen.append((str(request.url), request.headers.get("cookie")))
        if len(seen) == 1:
            return httpx.Response(
                302, headers={"location": "https://other.soundcloud.com/file"}
            )
        return httpx.Response(302, headers={"location": "https://127.0.0.1/private"})

    with httpx.Client(transport=httpx.MockTransport(handle)) as session:
        with pytest.raises(ProviderError):
            network.stream(
                session,
                "GET",
                "https://soundcloud.com/file",
                ("soundcloud.com",),
                Context("native_standard"),
                headers={"Cookie": "private"},
            )
    assert seen == [
        ("https://soundcloud.com/file", "private"),
        ("https://other.soundcloud.com/file", None),
    ]


def test_stream_limits_incomplete_bytes_and_json_retry(monkeypatch, tmp_path):
    monkeypatch.setattr(
        network,
        "client",
        lambda: httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, content=b"12345")
            )
        ),
    )
    with pytest.raises(ProviderError, match="limits"):
        network.transfer(
            "https://soundcloud.com/audio",
            ("soundcloud.com",),
            tmp_path / "audio",
            Context("native_standard", max_bytes=4),
        )
    count = []

    def gateway(request):
        count.append(1)
        return (
            httpx.Response(503)
            if len(count) == 1
            else httpx.Response(200, json={"tracks": []})
        )

    monkeypatch.setattr(
        network, "client", lambda: httpx.Client(transport=httpx.MockTransport(gateway))
    )
    assert network.get_json(
        "https://soundcloud.com/search", ("soundcloud.com",), Context("native_standard")
    ) == {"tracks": []}
    assert len(count) == 2


def test_limited_extractor_reads_none_budgets_and_closes():
    response = httpx.Response(200, content=b"abcdef")
    body = LimitedBody(response, Context("native_standard"), [0])
    assert body.read(2) == b"ab"
    assert body.read(None) == b"cdef"
    body.close()
    assert response.is_closed
    budget = [10_000_000]
    with pytest.raises(ProviderError):
        LimitedBody(
            httpx.Response(200, content=b"12345"),
            Context("native_standard", max_bytes=4),
            budget,
        ).read()


def test_guarded_handler_enforces_policy_on_fragment_requests(monkeypatch):
    from yt_dlp.networking.common import Request
    from yt_dlp.networking.exceptions import TransportError

    handler = GuardedRH(
        source="soundcloud", context=Context("native_standard"), logger=Quiet()
    )
    try:
        with pytest.raises(TransportError):
            handler.send(Request("https://127.0.0.1/fragment"))
        with pytest.raises(TransportError):
            handler.send(Request("https://other.example/manifest"))
    finally:
        handler.close()


def test_monochrome_isrc_release_duration_and_provider_namespace(monkeypatch):
    raw = {
        "id": "123",
        "recordingId": "987",
        "title": "Song",
        "artistNames": ["Artist"],
        "duration": 200000,
        "releaseId": "456",
        "isrc": "AA1234567890",
        "playable": True,
    }
    observed = []

    def get(url, hosts, context):
        observed.append(url)
        return {"tracks": [raw]} if "/search/" in url else {"title": "Album"}

    monkeypatch.setattr("server.providers.monochrome.get_json", get)
    monkeypatch.setattr(
        "server.providers.monochrome.identify", lambda target, context: set()
    )
    adapter = Monochrome()
    results = adapter.search(TARGET, Context("lossless"))
    assert len(results) == 1
    assert results[0]["duration"] == 200
    assert results[0]["provider_recording_id"] == "987"
    assert "musicbrainz_recording_id" not in results[0]
    assert matches(TARGET, results[0])
    assert not adapter.search(
        {**TARGET, "isrcs": ["BB1234567890"]}, Context("lossless")
    )
    assert not adapter.search({**TARGET, "album": "Live"}, Context("lossless"))
    assert not adapter.search({**TARGET, "isrcs": [], "album": ""}, Context("lossless"))
    raw["duration"] = 30000
    assert not adapter.search(TARGET, Context("lossless"))


def test_circuit_breaker_ignores_candidate_misses_and_allows_one_probe():
    now = [1000]
    health = Health(lambda: now[0])
    for _ in range(6):
        health.outcome("soundcloud", error=ProviderError("no_match"))
    with health.operation("soundcloud"):
        pass
    for _ in range(3):
        health.outcome("soundcloud", error=ProviderError())
    assert health.snapshot("soundcloud")["next_retry_at"] == 1300
    with pytest.raises(ProviderError):
        with health.operation("soundcloud"):
            pass
    now[0] = 1300
    with health.operation("soundcloud"):
        with pytest.raises(ProviderError):
            with health.operation("soundcloud"):
                pass
        health.outcome("soundcloud", error=ProviderError())
    assert health.snapshot("soundcloud")["next_retry_at"] == 1900
    now[0] = 1900
    with health.operation("soundcloud"):
        health.outcome("soundcloud")
    assert health.snapshot("soundcloud")["next_retry_at"] is None
    assert health.snapshot("soundcloud")["last_transfer"] is None
    health.outcome("soundcloud", transfer=True)
    assert health.snapshot("soundcloud")["last_transfer"] == "verified"


def test_answered_probe_clears_expired_cooldown():
    now = [1000]
    health = Health(lambda: now[0])
    for _ in range(3):
        health.outcome("soundcloud", error=ProviderError())
    now[0] = 1300
    with health.operation("soundcloud"):
        # The probe reached the source; only the candidate was rejected.
        health.outcome("soundcloud", error=ProviderError("no_match"))
    assert health.snapshot("soundcloud")["next_retry_at"] is None
    with health.operation("soundcloud"), health.operation("soundcloud"):
        pass


COOKIE = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t2147483647\tfixture\tsecret-value\n"


def test_owner_source_routes_and_credentials_never_return_secrets(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("PIG_MUSIC_TEST_LEGACY_API", raising=False)
    app = create_app(tmp_path)
    auth = app.state.auth
    auth.create_owner("owner", "a fixture password")
    owner = auth.login(
        "owner", "a fixture password", "device", "phone", "ios", "fixture"
    )["token"]
    setup = auth.setup_code(
        auth.user(auth.authenticate(owner)[0]["id"])["id"], "member", "Member"
    )
    auth.activate(setup["code"], "a fixture password", "fixture")
    member = auth.login(
        "member", "a fixture password", "device", "phone", "ios", "fixture"
    )["token"]
    root = "/api/v1/admin/download-sources"
    client = TestClient(app)
    assert client.get(root).status_code == 401
    assert (
        client.get(root, headers={"Authorization": "Bearer " + member}).status_code
        == 403
    )
    headers = {"Authorization": "Bearer " + owner}
    statuses = client.get(root, headers=headers).json()
    assert len(statuses) == 6
    assert "youtube" not in {s["id"] for s in statuses}
    assert all("session_configured" not in s for s in statuses)
    # The YouTube session upload went with the source (D-60).
    assert (
        client.put(
            root + "/youtube/cookies", json={"cookies": COOKIE}, headers=headers
        ).status_code
        == 404
    )
    assert client.delete(root + "/youtube/cookies", headers=headers).status_code == 404
    assert client.post(root + "/unknown/check", headers=headers).status_code == 404


def test_aac_without_nominal_bitrate_is_measured_without_changing_audio(tmp_path):
    from server.vendor.audio_probe import probe_file as container_probe
    from server.quality import satisfies

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg and FFprobe required")
    path = tmp_path / "native.m4a"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "anoisesrc=duration=3:seed=1",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-write_btrt",
            "0",
            str(path),
        ],
        check=True,
        timeout=20,
    )
    # Model source-native MP4s whose decoder descriptor omits nominal bitrates.
    # The AAC packets and the independently measured duration stay unchanged.
    content = bytearray(path.read_bytes())
    descriptor = content.index(b"\x40\x15\x00\x00\x00", content.index(b"esds"))
    content[descriptor + 5 : descriptor + 13] = b"\x00" * 8
    path.write_bytes(content)
    assert container_probe(path)["bitrate_kbps"] == 0
    measured = probe_file(path)
    assert 112 <= measured["bitrate_kbps"] <= 140
    assert satisfies("native_standard", measured)
    assert not satisfies("aac_256", measured)
    assert path.read_bytes() == content


def test_aac_hls_remux_retains_compressed_packets(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg required")
    source, output = tmp_path / "audio.ts", tmp_path / "audio.m4a"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "anoisesrc=duration=3:seed=1",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-f",
            "mpegts",
            str(source),
        ],
        check=True,
    )
    remux_aac(source, output)
    assert probe_file(output)["codec"] == "aac"

    def packets(path):
        value = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_packets",
                "-show_data_hash",
                "sha256",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            check=True,
        )
        return [p["data_hash"] for p in json.loads(value.stdout)["packets"]]

    # TS AAC packets include ADTS framing, removed by the container muxer.
    # Compare streams after the same read-only bitstream filter into ADTS.
    hashes = []
    for path in (source, output):
        value = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(path),
                "-c:a",
                "copy",
                "-f",
                "adts",
                "-",
            ],
            capture_output=True,
            check=True,
        )
        import hashlib

        hashes.append(hashlib.sha256(value.stdout).hexdigest())
    assert hashes[0] == hashes[1]
    with pytest.raises(ValueError):
        remux_aac(source, output)


@pytest.mark.parametrize(
    "codec,container,protocol",
    [
        ("aac", "m4a", "https"),
        ("opus", "webm", "https"),
        ("aac", "m4a", "m3u8_native"),
        ("mp3", "mp3", "m3u8_native"),
    ],
)
def test_actual_worker_downloads_synthetic_progressive_and_hls_through_guarded_handler(
    tmp_path, monkeypatch, codec, container, protocol
):
    """Use yt-dlp's real downloaders against synthetic payloads, with HTTP fixtures.

    This exercises manifest/fragment handling and exposes native-HLS fallback
    behavior without relying on third-party media or internet access.
    """
    from yt_dlp import YoutubeDL
    from server.providers import extractor_worker as worker

    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg required")
    root = tmp_path / "payload"
    root.mkdir()
    args = {
        "aac": ["-c:a", "aac", "-b:a", "192k"],
        "opus": ["-c:a", "libopus", "-b:a", "160k", "-vbr", "off"],
        "mp3": ["-c:a", "libmp3lame", "-b:a", "128k"],
    }[codec]
    if protocol == "m3u8_native":
        payload = root / "audio.m3u8"
        suffix = [
            "-f",
            "hls",
            "-hls_time",
            "1",
            "-hls_segment_filename",
            str(root / "fragment-%d.ts"),
        ]
    else:
        payload = root / ("audio." + container)
        suffix = []
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "anoisesrc=duration=3:seed=1",
            *args,
            *suffix,
            str(payload),
        ],
        check=True,
    )
    fmt = {
        **FORMAT,
        "acodec": codec,
        "ext": container,
        "protocol": protocol,
        "url": "https://audio.sndcdn.com/" + payload.name,
        "abr": 192 if codec == "aac" else 160 if codec == "opus" else 128,
    }
    info = {
        **INFO,
        "id": "123",
        "title": "Song",
        "duration": 3,
        "formats": [fmt],
        "webpage_url": "https://soundcloud.com/artist/song",
        "extractor": "soundcloud",
        "extractor_key": "Soundcloud",
        "requested_formats": [{"url": "https://evil.example/bypass"}],
    }
    monkeypatch.setattr(YoutubeDL, "extract_info", lambda *args, **kwargs: dict(info))
    urls = []

    def request(req):
        urls.append(str(req.url))
        return httpx.Response(
            200, content=(root / req.url.path.lstrip("/")).read_bytes()
        )

    monkeypatch.setattr(
        worker, "client", lambda: httpx.Client(transport=httpx.MockTransport(request))
    )
    output = tmp_path / "output"
    output.mkdir()
    result = execute(
        {
            "source": "soundcloud",
            "profile": "native_standard",
            "operation": "transfer",
            "provider_track_id": "artist/song",
            "format_id": fmt["format_id"],
            "directory": str(output),
        }
    )
    assert (output / result["filename"]).is_file()
    assert urls and all(url.startswith("https://audio.sndcdn.com/") for url in urls)
    if protocol == "m3u8_native":
        assert len(urls) > 1
    assert result["remuxed"] == (container == "webm" or protocol == "m3u8_native")


def test_metadata_success_does_not_mask_repeated_transfer_outages():
    health = Health(lambda: 1000)
    for _ in range(3):
        health.outcome("soundcloud")
        health.outcome("soundcloud", error=ProviderError(), transfer=True)
    assert health.snapshot("soundcloud")["next_retry_at"] == 1300


def test_deezer_cannot_invent_identity_or_rescue_conflicting_known_isrc(monkeypatch):
    from server.providers import deezer

    raw = {
        "id": 123,
        "artist": {"name": "Artist"},
        "title": "Song",
        "album": {"title": "Album"},
        "duration": 200,
    }
    requests = []

    def get(url, hosts, context):
        requests.append(url)
        return {"data": [raw]} if "/search?" in url else {"isrc": "AA1234567890"}

    monkeypatch.setattr(deezer, "get_json", get)
    assert deezer.identify(TARGET, Context("lossless")) == {"AA1234567890"}
    assert not deezer.identify({**TARGET, "album": "Live"}, Context("lossless"))
    requests.clear()
    monkeypatch.setattr(
        "server.providers.monochrome.get_json", lambda *a: {"tracks": []}
    )
    monkeypatch.setattr(
        "server.providers.monochrome.identify",
        lambda *a: pytest.fail("Known ISRC must not use rescue"),
    )
    assert not Monochrome().search(TARGET, Context("lossless"))
    assert not requests


def test_retained_parser_transport_bounds_and_cookie_form_roundtrip(monkeypatch):
    from server.providers import legacy_worker

    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            200,
            content=b"ok",
            headers={"set-cookie": "session=fixture; Secure; Path=/"},
        )

    monkeypatch.setattr(
        legacy_worker,
        "client",
        lambda: httpx.Client(transport=httpx.MockTransport(handle)),
    )
    with legacy_worker.ParserClient(
        ("freemp3cloud.com",), Context("native_standard")
    ) as parser:
        landing = parser.get("https://freemp3cloud.com/")
        assert landing.text == "ok"
        result = parser.post(
            "https://freemp3cloud.com/search", data={"query": "Artist Song"}
        )
        assert result.text == "ok"
        with pytest.raises(ProviderError):
            parser.get("https://evil.example/")
    assert requests[1].headers["cookie"] == "session=fixture"
    assert requests[1].content == b"query=Artist+Song"


def test_worker_blocks_ffmpeg_network_fallback_and_plugins():
    from yt_dlp.downloader.external import FFmpegFD
    from yt_dlp.globals import plugin_dirs

    # A worker execution initializes the guard even when its operation is invalid.
    with pytest.raises(ProviderError):
        execute(
            {
                "source": "soundcloud",
                "profile": "native_standard",
                "operation": "invalid",
            }
        )
    assert plugin_dirs.value == []
    with pytest.raises(ProviderError, match="network policy"):
        FFmpegFD.real_download(None, "output", {"url": "https://evil.example/manifest"})


@pytest.mark.parametrize(
    "source,codec",
    [("soundcloud", "opus"), ("soundcloud", "aac"), ("monochrome", "flac")],
)
def test_new_adapters_publish_verified_synthetic_audio_and_survive_restart(
    tmp_path, monkeypatch, source, codec
):
    from yt_dlp import YoutubeDL
    from server.providers import extractor_worker as worker
    from server.providers import monochrome

    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg required")
    raw = tmp_path / "raw"
    raw.mkdir()
    extension = {"opus": "webm", "aac": "m4a", "flac": "flac"}[codec]
    audio = raw / ("audio." + extension)
    args = {
        "opus": ["-c:a", "libopus", "-b:a", "160k", "-vbr", "off"],
        "aac": ["-c:a", "aac", "-b:a", "192k"],
        "flac": ["-c:a", "flac"],
    }[codec]
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "anoisesrc=duration=3:seed=1",
            *args,
            str(audio),
        ],
        check=True,
    )
    store = Store(tmp_path / "state")
    job = queued(store)
    profile = "lossless" if codec == "flac" else "native_standard"
    store.save_download_settings(
        {"enabled": True, "profile": profile, "sources": [source]}
    )
    job = {
        **job,
        "quality_profile": profile,
        "quality_policy": {
            "preferred": profile,
            "fallback": "none",
            "fallback_minutes": 10,
            "recheck_hours": 24,
        },
    }
    engine = Downloader(store)
    if source == "monochrome":
        monkeypatch.setattr(monochrome, "identify", lambda *a: set())

        def get(url, hosts, context):
            return (
                {
                    "tracks": [
                        {
                            "id": "123",
                            "recordingId": "987",
                            "title": job["title"],
                            "artistNames": [job["artist"]],
                            "duration": 3000,
                            "releaseId": "456",
                            "playable": True,
                        }
                    ]
                }
                if "/search/" in url
                else {"title": job["album"]}
            )

        monkeypatch.setattr(monochrome, "get_json", get)
        monkeypatch.setattr(
            network,
            "client",
            lambda: httpx.Client(
                transport=httpx.MockTransport(
                    lambda r: httpx.Response(200, content=audio.read_bytes())
                )
            ),
        )
    else:
        fmt = {
            **FORMAT,
            "acodec": codec,
            "ext": extension,
            "url": (
                "https://audio.sndcdn.com/audio.m4a"
                if source == "soundcloud"
                else "https://audio.googlevideo.com/audio.webm"
            ),
            "abr": 192 if codec == "aac" else 160,
        }
        info = {
            **INFO,
            "artist": job["artist"],
            "track": job["title"],
            "title": job["title"],
            "album": job["album"],
            "duration": 3,
            "formats": [fmt],
            "webpage_url": "https://soundcloud.com/artist/song",
            "extractor": source,
            "extractor_key": "Soundcloud",
        }
        monkeypatch.setattr(YoutubeDL, "extract_info", lambda *a, **kw: dict(info))

        def invoke(request, context):
            if request["operation"] == "search":
                return {"candidates": [candidate(info, source, profile)]}
            return execute(
                {
                    **request,
                    "source": source,
                    "profile": profile,
                    "max_bytes": context.max_bytes,
                }
            )

        monkeypatch.setattr(engine.runtime.adapters[source], "invoke", invoke)
        monkeypatch.setattr(
            worker,
            "client",
            lambda: httpx.Client(
                transport=httpx.MockTransport(
                    lambda r: httpx.Response(200, content=audio.read_bytes())
                )
            ),
        )
    engine.process(job)
    assert store.queue()["items"][0]["status"] == "completed", store.queue()
    with store.connect() as conn:
        payload = json.loads(conn.execute("SELECT payload FROM acquired").fetchone()[0])
    assert payload["codec"] == codec
    assert payload["acquisition"]["provider"] == source
    assert payload["acquisition"]["format_id"]
    assert payload["acquisition"]["validated_at"]
    assert "secret=" not in json.dumps(payload)
    assert engine.runtime.health.snapshot(source)["last_transfer"] == "verified"
    restored = Store(tmp_path / "state")
    assert restored.queue()["items"][0]["status"] == "completed"
    assert len(restored.library["albums"][0]["tracks"]) == 1
    assert not list(engine.staging.glob(".extractor-*"))
    assert not list(engine.staging.glob("*.part"))


def test_dns_wait_is_bounded_without_allowing_late_connections(monkeypatch):
    import threading

    unblock = threading.Event()
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: unblock.wait(1) or [])
    started = time.monotonic()
    try:
        with pytest.raises(ProviderError):
            network.PublicBackend().connect_tcp("soundcloud.com", 443, timeout=0.01)
        assert time.monotonic() - started < 0.5
    finally:
        unblock.set()


def test_original_soundcloud_downloads_are_tentative_until_measured_validation():
    original = {
        "format_id": "download",
        "format_note": "Original",
        "ext": "flac",
        "vcodec": "none",
        "protocol": "https",
    }
    assert formats({"formats": [original]}, "lossless")[0]["original_codec"] == "flac"
    assert not formats({"formats": [original]}, "mp3_320")
    assert not formats({"formats": [{**original, "ext": "wav"}]}, "native_standard")
    assert not formats({"formats": [{**original, "ext": "webm"}]}, "native_standard")
    mp3 = {**original, "ext": "mp3"}
    assert formats({"formats": [mp3]}, "mp3_320")[0]["declared_bitrate"] == 0
