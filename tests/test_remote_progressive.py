"""#23: a cold relayed song starts before its whole file has arrived, with the
relay's identity, length, range, cancellation and failure rules kept."""

import asyncio
import shutil
import subprocess
import threading
import time

import httpx
import pytest
from starlette.requests import ClientDisconnect

from server import remote, request_timing
from server.providers import network
from server.providers.contracts import ProviderError
from server.remote import RemoteAudio

CHUNK = 65536
TRACK = {"artist": "Artist", "title": "Song", "album": "Album", "duration": 30}


def mp3(tmp_path, name="fixture.mp3", *options, seconds=30):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg is required for relayed MP3s")
    path = tmp_path / name
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=duration={seconds}",
            *(options or ("-b:a", "192k")),
            str(path),
        ],
        check=True,
    )
    return path.read_bytes()


class Drip(httpx.SyncByteStream):
    """A slow source: one 64 KiB chunk per `pause`, optionally held at a gate
    or failing part way."""

    def __init__(self, data, pause=0.05, hold_after=None, fail_after=None):
        self.data, self.pause = data, pause
        self.hold_after, self.fail_after = hold_after, fail_after
        self.gate = threading.Event()
        self.sent = 0
        self.finished = None

    def __iter__(self):
        for chunk, at in enumerate(range(0, len(self.data), CHUNK)):
            if self.fail_after is not None and chunk >= self.fail_after:
                raise ProviderError("access_required")
            if self.hold_after is not None and chunk >= self.hold_after:
                assert self.gate.wait(10)
            self.sent += 1
            yield self.data[at : at + CHUNK]
            time.sleep(self.pause)
        self.finished = time.monotonic()


def relayed(tmp_path, monkeypatch, data, declared=True, **drip):
    """A resolver whose only match is FreeMp3Cloud, served by a dripping source
    through the real transfer code."""
    source = Drip(data, **drip)

    def handler(request):
        assert request.url.host == "meln.top"
        return httpx.Response(
            200,
            headers=(
                {"content-length": str(len(data) if declared is True else declared)}
                if declared
                else {}
            ),
            stream=source,
        )

    monkeypatch.setattr(
        network, "client", lambda: httpx.Client(transport=httpx.MockTransport(handler))
    )

    def relay_search(name, track):
        if name != "freemp3cloud":
            return []
        return [
            {
                "channel": "Artist",
                "title": "Song",
                "duration": track["duration"],
                "source_url": "https://meln.top/song.mp3",
                "quality": "320kbps",
            }
        ]

    resolver = RemoteAudio(
        lambda *_: [], directory=tmp_path / "relay", relay_search=relay_search
    )
    return resolver, source


async def play(resolver, track=TRACK):
    trace = request_timing.Trace("progressive-test")
    token = request_timing._trace.set(trace)
    try:
        async with httpx.AsyncClient() as client:
            found = await resolver.stream(client, track, "member")
    finally:
        request_timing._trace.reset(token)
    return found, [name for name, _ in trace.phases]


async def listen(response, gone_after=None):
    """Drive an ASGI response; the listener leaves after `gone_after` body
    chunks. Returns (status, headers, [(time, body)], completed)."""
    started, chunks, ended = {}, [], []

    async def receive():
        await asyncio.Event().wait()

    async def send(message):
        if message["type"] == "http.response.start":
            started.update(message)
        elif message.get("more_body"):
            if gone_after is not None and len(chunks) >= gone_after:
                raise OSError("The listener went away.")
            chunks.append((time.monotonic(), message["body"]))
        else:
            chunks.append((time.monotonic(), message["body"]))
            ended.append(True)

    scope = {"type": "http", "asgi": {"spec_version": "2.4"}, "method": "GET"}
    try:
        await response(scope, receive, send)
    except ClientDisconnect:
        pass
    headers = {k.decode(): v.decode() for k, v in started.get("headers", [])}
    return started.get("status"), headers, chunks, bool(ended)


async def settled(resolver):
    for _ in range(500):
        if not resolver.transfers:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("The relay transfer never ended.")


def body(chunks):
    return b"".join(data for _, data in chunks)


def test_a_cold_relay_plays_before_its_transfer_ends(tmp_path, monkeypatch, caplog):
    data = mp3(tmp_path)
    assert len(data) > 6 * CHUNK
    resolver, source = relayed(tmp_path, monkeypatch, data, pause=0.15)

    async def run():
        began = time.monotonic()
        found, phases = await play(resolver)
        answered = time.monotonic() - began
        transfer = resolver.relay(found["relay"], "member")
        assert type(transfer).__name__ == "RelayTransfer"
        response = await transfer.response("GET", None)
        status, headers, chunks, completed = await listen(response)
        await settled(resolver)
        again, cached_phases = await play(resolver)
        return (
            answered,
            phases,
            status,
            headers,
            chunks,
            completed,
            found,
            again,
            cached_phases,
        )

    with caplog.at_level("INFO", logger="pig_music.remote"):
        began = time.monotonic()
        (answered, phases, status, headers, chunks, completed, found, again, cached) = (
            asyncio.run(run())
        )
    transfer_seconds = source.finished - began
    first_byte = chunks[0][0] - began
    print(
        f"address {answered:.2f} s, first byte {first_byte:.2f} s, "
        f"transfer {transfer_seconds:.2f} s"
    )
    # The address and the first bytes come long before the transfer ends.
    assert answered < transfer_seconds / 2
    assert first_byte < transfer_seconds / 2
    assert chunks[0][0] < source.finished
    assert "freemp3cloud-streaming" in phases
    # The whole song, with its length declared before the first byte.
    assert status == 200 and completed
    assert headers["content-length"] == str(len(data))
    assert headers["accept-ranges"] == "bytes"
    assert headers["content-type"] == "audio/mpeg"
    assert body(chunks) == data
    # Published under the recording's name with the same bytes (a listener's
    # later range must find the audio it was given), and nothing unfinished.
    path, media_type = resolver.relay(found["relay"], "member")
    assert media_type == "audio/mpeg" and path.read_bytes() == data
    assert [p.name for p in (tmp_path / "relay").iterdir()] == [path.name]
    # Played again it is a cached copy, as before (#23 keeps repeats).
    assert again["relay"] != found["relay"] and cached == ["relay-cache"]
    assert resolver.relay(again["relay"], "member")[0] == path
    assert resolver.source_health == {}
    assert any("arrived in" in r.getMessage() for r in caplog.records)


def test_ranges_are_served_as_far_as_the_relay_has_arrived(tmp_path, monkeypatch):
    data = mp3(tmp_path)
    resolver, source = relayed(tmp_path, monkeypatch, data, pause=0, hold_after=2)
    monkeypatch.setattr(remote, "WAIT_SECONDS", 0.3)

    async def run():
        found, _ = await play(resolver)
        transfer = resolver.relay(found["relay"], "member")
        while transfer.arrived < 2 * CHUNK:
            await asyncio.sleep(0.01)
        results = {}
        # HEAD: the length and range support, without waiting for anything.
        head = await transfer.response("HEAD", None)
        results["head"] = (head.status_code, dict(head.headers))
        # A player's probe inside the frame that proved the length: whole at once.
        assert 100 < transfer.checked < 2000
        probed = time.monotonic()
        results["probe"] = await listen(await transfer.response("GET", "bytes=0-1"))
        results["probe_seconds"] = time.monotonic() - probed
        # Arrived but beyond that frame: served, except its own last byte,
        # which waits for the validated file (here, out of time: ends short).
        results["inside"] = await listen(
            await transfer.response("GET", "bytes=1000-1099")
        )
        # Past it: a bounded wait, then a clean retryable answer, no bytes.
        waited = time.monotonic()
        late = await transfer.response("GET", f"bytes={4 * CHUNK}-{4 * CHUNK + 99}")
        results["late"] = (
            late.status_code,
            dict(late.headers),
            time.monotonic() - waited,
        )
        # Past it, while the bytes arrive: it waits and then serves them.
        remote.WAIT_SECONDS = 5
        asyncio.get_running_loop().call_later(0.2, source.gate.set)
        results["waited"] = await listen(
            await transfer.response("GET", f"bytes={4 * CHUNK}-{4 * CHUNK + 99}")
        )
        # A range outside the declared length.
        outside = await transfer.response("GET", f"bytes={len(data)}-")
        results["outside"] = (outside.status_code, dict(outside.headers))
        await settled(resolver)
        return results

    results = asyncio.run(run())
    status, headers = results["head"]
    assert status == 200 and headers["content-length"] == str(len(data))
    status, headers, chunks, completed = results["probe"]
    assert status == 206 and completed and body(chunks) == data[:2]
    assert results["probe_seconds"] < 0.2
    status, headers, chunks, completed = results["inside"]
    assert status == 206 and not completed and body(chunks) == data[1000:1099]
    assert headers["content-range"] == f"bytes 1000-1099/{len(data)}"
    status, headers, seconds = results["late"]
    assert status == 503 and headers["retry-after"] == "2"
    assert 0.25 < seconds < 2
    status, headers, chunks, completed = results["waited"]
    assert status == 206 and completed
    assert body(chunks) == data[4 * CHUNK : 4 * CHUNK + 100]
    status, headers = results["outside"]
    assert status == 416 and headers["content-range"] == f"bytes */{len(data)}"


def test_a_source_failing_mid_stream_ends_it_and_counts_as_a_transfer_failure(
    tmp_path, monkeypatch, caplog
):
    data = mp3(tmp_path)
    resolver, source = relayed(tmp_path, monkeypatch, data, pause=0.05, fail_after=3)

    async def run():
        found, _ = await play(resolver)
        transfer = resolver.relay(found["relay"], "member")
        heard = await listen(await transfer.response("GET", None))
        await settled(resolver)
        return found, heard

    with caplog.at_level("INFO", logger="pig_music.remote"):
        found, (status, headers, chunks, completed) = asyncio.run(run())
    # Started, then ended short of its declared length: never a whole song.
    assert status == 200 and headers["content-length"] == str(len(data))
    assert not completed and len(body(chunks)) <= 3 * CHUNK
    assert data.startswith(body(chunks))
    # A transfer failure for the source's own streak, as any transfer's.
    failures, _ = resolver.source_health[("freemp3cloud", "transfer")]
    assert failures == 1
    assert ("freemp3cloud", "search") not in resolver.source_health
    assert any("failed while playing" in r.getMessage() for r in caplog.records)
    # The link is dead and the unfinished file is gone.
    assert resolver.relay(found["relay"], "member") is None
    assert list((tmp_path / "relay").iterdir()) == []
    assert resolver.relay_slots._value == 3


def test_a_listener_leaving_does_not_stop_a_transfer_another_uses(
    tmp_path, monkeypatch
):
    data = mp3(tmp_path)
    resolver, source = relayed(tmp_path, monkeypatch, data, pause=0.05)

    async def run():
        found, _ = await play(resolver)
        # The same song played again while it arrives joins its transfer.
        joined, phases = await play(resolver)
        assert "relay-joined" in phases
        first = resolver.relay(found["relay"], "member")
        second = resolver.relay(joined["relay"], "member")
        assert first is second
        gone, stayed = await asyncio.gather(
            listen(await first.response("GET", None), gone_after=1),
            listen(await second.response("GET", None)),
        )
        await settled(resolver)
        return found, gone, stayed

    found, gone, stayed = asyncio.run(run())
    assert not gone[3] and len(gone[2]) == 1
    status, _, chunks, completed = stayed
    assert status == 200 and completed and body(chunks) == data
    # The transfer finished once, and only its published copy remains.
    assert source.sent == -(-len(data) // CHUNK)
    path, _ = resolver.relay(found["relay"], "member")
    assert path.read_bytes() == data
    assert [p.name for p in (tmp_path / "relay").iterdir()] == [path.name]


def test_a_head_that_is_not_the_recording_stops_the_transfer_and_serves_nothing(
    tmp_path, monkeypatch
):
    data = mp3(tmp_path)
    resolver, source = relayed(tmp_path, monkeypatch, data, pause=0.05)
    # The source's file is 30 s; the recording asked for is 200 s.
    track = {**TRACK, "duration": 200}

    async def run():
        with pytest.raises(TimeoutError):
            await play(resolver, track)

    asyncio.run(run())
    # Stopped at its first chunk instead of transferring the whole file.
    assert source.sent == 1
    assert list((tmp_path / "relay").iterdir()) == []
    assert resolver.transfers == {} and resolver.relayed == {}
    assert resolver.relay_slots._value == 3


def test_an_mp3_head_proves_its_length_or_the_file_arrives_whole(tmp_path, monkeypatch):
    from server.remote import NotProgressive, mp3_head

    cbr = mp3(tmp_path, "cbr.mp3", "-b:a", "192k", "-write_xing", "0")
    info = mp3(tmp_path, "info.mp3", "-b:a", "192k")
    vbr = mp3(tmp_path, "vbr.mp3", "-q:a", "2", "-write_xing", "0")
    tagged = mp3(tmp_path, "tagged.mp3", "-metadata", "title=Song", "-q:a", "4")
    assert abs(mp3_head(info[:CHUNK], len(info)) - 30) < 0.1
    assert abs(mp3_head(tagged[:CHUNK], len(tagged)) - 30) < 0.1
    # Too few bytes yet: wait for more.
    assert mp3_head(info[:20], len(info)) is None

    # Without a length header nothing is proved: a constant bitrate so far
    # can change later (Codex #23: eight 192 kbps frames, then 32 kbps).
    def frame(bitrate_index, length):
        return bytes([0xFF, 0xFB, bitrate_index << 4, 0xC0]) + bytes(length - 4)

    changing = frame(11, 626) * 8 + frame(1, 104) * 600
    with pytest.raises(NotProgressive):
        mp3_head(changing, 720000)
    for headerless in (cbr, vbr):
        with pytest.raises(NotProgressive):
            mp3_head(headerless[:CHUNK], len(headerless))
    with pytest.raises(NotProgressive):
        mp3_head(b"<html>" + b" " * 200, 1000)
    # A constant-bitrate header whose size disagrees with the source's.
    with pytest.raises(NotProgressive):
        mp3_head(info[:CHUNK], 2 * len(info))

    # A headerless file is relayed as before: whole, validated, then played.
    resolver, source = relayed(tmp_path, monkeypatch, cbr, pause=0)
    found, phases = asyncio.run(play(resolver))
    assert "freemp3cloud-transfer" in phases
    assert "freemp3cloud-streaming" not in phases
    assert resolver.relay(found["relay"], "member")[0].is_file()

    # A source that does not declare its size gives no length to declare:
    # such a file is relayed as before, whole and validated, then played.
    resolver, source = relayed(
        tmp_path / "undeclared", monkeypatch, info, declared=False, pause=0
    )
    found, phases = asyncio.run(play(resolver))
    assert "freemp3cloud-transfer" in phases
    assert "freemp3cloud-streaming" not in phases
    path, _ = resolver.relay(found["relay"], "member")
    assert path.is_file()


def test_the_last_byte_waits_for_the_validated_file(tmp_path, monkeypatch, caplog):
    """Codex #23: every byte had arrived, then the whole-file probe rejected
    it; the body must not have completed."""
    from server import audio

    data = mp3(tmp_path)
    resolver, source = relayed(tmp_path, monkeypatch, data, pause=0.02)

    def rejecting_probe(path, check=lambda: None):
        time.sleep(0.3)
        return None

    monkeypatch.setattr(audio, "probe_file", rejecting_probe)

    async def run():
        found, _ = await play(resolver)
        transfer = resolver.relay(found["relay"], "member")
        probe = await listen(await transfer.response("GET", "bytes=0-1"))
        whole, tail, inside = await asyncio.gather(
            listen(await transfer.response("GET", None)),
            listen(await transfer.response("GET", f"bytes={len(data) - 10}-")),
            # Beyond the proving frame (Codex #23: a bounded range completed
            # before a rejection).
            listen(await transfer.response("GET", "bytes=0-2999")),
        )
        await settled(resolver)
        return found, whole, tail, (probe, inside)

    with caplog.at_level("INFO", logger="pig_music.remote"):
        found, whole, tail, (probe, inside) = asyncio.run(run())
    assert probe[3] and body(probe[2]) == data[:2]
    status, _, chunks, completed = inside
    assert status == 206 and not completed and body(chunks) == data[:2999]
    status, _, chunks, completed = whole
    assert status == 200 and not completed
    assert body(chunks) == data[: len(body(chunks))]
    assert len(body(chunks)) < len(data)
    status, _, chunks, completed = tail
    assert not completed and len(body(chunks)) < 10
    assert resolver.relay(found["relay"], "member") is None
    assert list((tmp_path / "relay").iterdir()) == []


def test_a_source_sending_more_than_it_declared_fails_the_transfer(
    tmp_path, monkeypatch
):
    data = mp3(tmp_path)
    declared = len(data) - 1000
    resolver, source = relayed(tmp_path, monkeypatch, data, declared=declared)

    async def run():
        found, _ = await play(resolver)
        transfer = resolver.relay(found["relay"], "member")
        heard = await listen(await transfer.response("GET", None))
        await settled(resolver)
        return found, heard

    found, (status, headers, chunks, completed) = asyncio.run(run())
    assert status == 200 and headers["content-length"] == str(declared)
    assert not completed and len(body(chunks)) < declared
    assert resolver.relay(found["relay"], "member") is None
    assert list((tmp_path / "relay").iterdir()) == []


@pytest.mark.loop_timing
def test_serving_a_growing_relay_keeps_the_event_loop_free(tmp_path, monkeypatch):
    data = mp3(tmp_path, seconds=120)
    resolver, _ = relayed(tmp_path, monkeypatch, data, pause=0.01)
    longest = [0.0]

    async def run():
        done = asyncio.Event()

        async def tick():
            last = time.monotonic()
            while not done.is_set():
                await asyncio.sleep(0.01)
                now = time.monotonic()
                longest[0] = max(longest[0], now - last)
                last = now

        ticker = asyncio.create_task(tick())
        found, _ = await play(resolver, {**TRACK, "duration": 120})
        transfer = resolver.relay(found["relay"], "member")
        heard = await asyncio.gather(
            *[listen(await transfer.response("GET", None)) for _ in range(3)]
        )
        await settled(resolver)
        done.set()
        await ticker
        return heard

    heard = asyncio.run(run())
    assert all(body(chunks) == data for _, _, chunks, _ in heard)
    assert longest[0] < 0.2, longest[0]


def test_a_growing_relay_is_served_through_its_signed_link(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from server.app import create_app

    data = mp3(tmp_path)
    source = Drip(data, pause=0, hold_after=2)

    def handler(request):
        return httpx.Response(
            200, headers={"content-length": str(len(data))}, stream=source
        )

    async def no_direct_stream(self, client, track):
        raise ValueError("No unique remote recording matched this release.")

    def find(self, name, track):
        if name != "zvu4no":
            raise ValueError("No recording matched.")
        return {"source_url": "https://zvu4it.org/song.mp3"}

    monkeypatch.setattr(
        network, "client", lambda: httpx.Client(transport=httpx.MockTransport(handler))
    )
    monkeypatch.setattr(RemoteAudio, "resolve", no_direct_stream)
    monkeypatch.setattr(RemoteAudio, "find", find)
    monkeypatch.setattr(remote, "WAIT_SECONDS", 0.3)
    monkeypatch.delenv("PIG_MUSIC_TEST_LEGACY_API", raising=False)
    monkeypatch.setenv("PIG_MUSIC_LIBRARY", str(tmp_path / "nas"))
    app = create_app(tmp_path / "data")
    password = "correct horse battery staple 23"
    app.state.auth.create_owner("mark", password)
    token = app.state.auth.login("mark", password, "device", "phone", "ios", "fixture")[
        "token"
    ]
    with TestClient(app, base_url="https://private.ts.net") as client:
        played = client.post(
            "/api/v1/remote-playback",
            json=TRACK,
            headers={"Authorization": "Bearer " + token},
        )
        assert played.status_code == 200, played.text
        url = played.json()["url"]
        early = client.get(url, headers={"Range": "bytes=0-99"})
        assert early.status_code == 206 and early.content == data[:100]
        assert early.headers["content-range"] == f"bytes 0-99/{len(data)}"
        late = client.get(url, headers={"Range": f"bytes={len(data) - 100}-"})
        assert late.status_code == 503
        # Signed-link checks are unchanged for a growing relay.
        assert client.get(url + "x").status_code == 403
        assert client.get(url.split("?")[0]).status_code == 401
        source.gate.set()
        for _ in range(250):
            whole = client.get(url)
            if whole.status_code == 200 and "content-range" not in whole.headers:
                if len(whole.content) == len(data):
                    break
            time.sleep(0.02)
        assert whole.content == data
