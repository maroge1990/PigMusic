import json
import time
from types import SimpleNamespace

import httpx
import pytest

from fastapi.testclient import TestClient

from scripts import probe_playback
from server import mixes as mix_module
from test_mixes import cached, member, ready, track
from test_security import protected
from test_downloader import audio  # noqa: F401  Shared real MP3 fixture.


def test_probe_times_a_mix_song_like_the_phone_and_reports_failures(
    tmp_path, monkeypatch, audio, capsys
):
    monkeypatch.setattr(mix_module, "MIN_MIX", 1)
    app, owner = protected(tmp_path, monkeypatch)
    mixes = app.state.store.mixes
    user, token = member(app, owner)
    # Large enough that the probe also reads near the end, as a seek would.
    cached(mixes, track()).write_bytes(audio.read_bytes() * 4)
    identity = ready(mixes, user, [track()])
    output = tmp_path / "probe.jsonl"
    args = SimpleNamespace(
        server="https://private.ts.net",
        library=5,
        mix_songs=5,
        track=["missing-song"],
        full=True,
        repeat=1,
        interval=0,
        output=str(output),
    )
    with TestClient(
        app,
        base_url=args.server,
        headers={"Authorization": "Bearer " + token},
    ) as api:
        # Media requests carry the signed link only, as on the phone.
        media = TestClient(app, base_url=args.server)
        records = probe_playback.run(args, api=api, media=media)

    missing, song = records
    assert missing["error"] == "address: HTTP 404"
    assert song["id"] == f"mix:{identity}:0" and song["kind"] == "mix"
    assert "error" not in song, song
    assert song["address"]["server"]["app"] > 0
    assert "mix-lease" in song["address"]["server"]
    assert song["open"]["status"] == 206 and song["open"]["type"] == "audio/mpeg"
    assert song["open"]["opening_bytes"] >= probe_playback.OPENING
    assert song["seek"]["status"] == 206
    assert song["full"]["bytes"] == song["open"]["total"]
    # Saved records never hold the signed link or the token.
    saved = output.read_text()
    assert len(saved.splitlines()) == 2 and json.loads(saved.splitlines()[1])
    assert "sig=" not in saved and token not in saved
    printed = capsys.readouterr().out
    assert "failures: 1 of 2" in printed and song["address"]["request_id"] in printed


def test_server_timing_parses_repeated_and_malformed_entries():
    assert probe_playback.server_timing(
        "app;dur=10.5, auth-ready;dur=1, auth-ready;dur=2, bad;dur=x, nodur"
    ) == {"app": 10.5, "auth-ready": 3.0}
    assert probe_playback.server_timing(None) == {}


def test_token_file_must_be_private(tmp_path):
    path = tmp_path / "token"
    path.write_text("disposable\n")
    path.chmod(0o644)
    try:
        probe_playback.read_token(path)
    except SystemExit as exc:
        assert "chmod 600" in str(exc)
    else:
        raise AssertionError("a readable token file was accepted")
    path.chmod(0o600)
    assert probe_playback.read_token(path) == "disposable"


def test_login_refuses_plain_http_before_asking_for_a_password(tmp_path, monkeypatch):
    asked = []
    monkeypatch.setattr(probe_playback.getpass, "getpass", lambda _: asked.append(1))
    sent = []
    client = httpx.Client(
        base_url="http://review.invalid",
        transport=httpx.MockTransport(lambda request: sent.append(request)),
    )
    for server in ("http://review.invalid", "http://127.0.0.1:8765", "review.invalid"):
        args = SimpleNamespace(
            server=server, username="probe", token_file=str(tmp_path / "token")
        )
        with pytest.raises(SystemExit, match="HTTPS"):
            probe_playback.login(args, client=client)
        with pytest.raises(SystemExit, match="HTTPS"):
            probe_playback.run(SimpleNamespace(server=server, token_file="unused"))
    assert asked == [] and sent == []
    assert not (tmp_path / "token").exists()


def test_login_replaces_a_readable_token_file_with_a_private_one(tmp_path, monkeypatch):
    monkeypatch.setattr(probe_playback.getpass, "getpass", lambda _: "disposable")
    path = tmp_path / "token"
    path.write_text("old")
    path.chmod(0o644)
    client = httpx.Client(
        base_url="https://review.invalid",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200 if request.headers.get("x-pig-request") == "1" else 403,
                json={"token": "fresh-device-token"},
            )
        ),
    )
    args = SimpleNamespace(
        server="https://review.invalid", username="probe", token_file=str(path)
    )
    probe_playback.login(args, client=client)
    assert path.stat().st_mode & 0o777 == 0o600
    assert probe_playback.read_token(path) == "fresh-device-token"
    assert [p.name for p in tmp_path.iterdir()] == ["token"]


class FakeServer:
    """A server shaped like PigMusic for the probe's scenarios."""

    def __init__(self):
        self.started = time.time()
        self.asked = {}
        self.seen = []

    def __call__(self, request):
        path = request.url.path
        self.seen.append((request.method, path, dict(request.headers)))
        timing = {"server-timing": "app;dur=12.0, auth-ready;dur=2.0"}
        if path == "/api/v1/search":
            owned = "Mr. Brightside" in request.url.params["q"]
            track = {"title": "Mr. Brightside", "artist": "The Killers"}
            return httpx.Response(
                200, json={"household_tracks": [track] if owned else []}
            )
        if path == "/api/v1/remote-playback":
            found = {
                "url": "/api/v1/remote-playback/streams/relay-token",
                "source": "Remote · YouTube",
            }
            return httpx.Response(
                200,
                json=found,
                headers={"server-timing": "app;dur=15000, youtube-found;dur=12000"},
            )
        if path.startswith("/api/v1/remote-playback/streams/") or path.startswith(
            "/api/v1/playback/files/"
        ):
            body = b"\0" * (300 * 1024)
            return httpx.Response(
                206,
                content=body,
                headers={
                    "content-type": "audio/mpeg",
                    "content-range": f"bytes 0-{len(body) - 1}/{len(body)}",
                    **timing,
                },
            )
        if path.endswith("/url"):
            return httpx.Response(
                200, json={"url": "/api/v1/playback/files/song?sig=s"}, headers=timing
            )
        if path == "/api/v1/library-pages/tracks":
            return httpx.Response(
                200, json={"items": [{"id": "song", "title": "Song"}]}
            )
        if path == "/api/v1/library/albums":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "al",
                            "title": "Album",
                            "cover_url": "/api/v1/library-artwork/albums/al",
                        }
                    ]
                },
            )
        if path == "/api/v1/library/artists":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "ar",
                            "name": "Artist",
                            "cover_url": "/api/v1/library-artwork/artists/ar",
                        }
                    ]
                },
            )
        if path == "/api/v1/home/popular":
            return httpx.Response(
                200,
                json={
                    "albums": [{"id": "rg", "name": "Hit", "artist": "Star"}],
                    "songs": [],
                    "artists": [{"id": "mb", "name": "Star"}],
                },
            )
        if path.endswith("/releases"):
            return httpx.Response(
                200, json={"releases": [{"id": "single", "title": "Single"}]}
            )
        if path == "/api/v1/playlists":
            return httpx.Response(
                200, json={"playlists": [{"id": "pl", "name": "Probe test"}]}
            )
        if "artwork" in path:
            # The album has art; the rest arrive once asked a second time.
            self.asked[path] = self.asked.get(path, 0) + 1
            if path.endswith("/al") or self.asked[path] > 1:
                return httpx.Response(307, headers={"location": "/api/v1/covers/x.jpg"})
            return httpx.Response(404)
        if path == "/api/v1/diagnostics":
            blocks = [
                {"at": self.started - 600, "ms": 9000},
                {"at": time.time(), "ms": 2500},
            ]
            return httpx.Response(
                200,
                json={
                    "blocks": blocks,
                    "stalls": [],
                    "requests": [],
                    "pools": {},
                    "uptime_seconds": 60,
                },
            )
        return httpx.Response(200, json={}, headers=timing)


def fake_clients(server):
    api = httpx.Client(
        base_url="https://pig.test",
        transport=httpx.MockTransport(server),
        headers={"Authorization": "Bearer probe-token-value"},
    )
    media = httpx.Client(
        base_url="https://pig.test", transport=httpx.MockTransport(server)
    )
    return api, media


def args_for(tmp_path, **extra):
    return SimpleNamespace(
        server="https://pig.test",
        token_file="unused",
        output=str(tmp_path / "out.jsonl"),
        **extra,
    )


def test_remote_skips_household_songs_and_asks_twice(tmp_path, capsys):
    server = FakeServer()
    args = args_for(
        tmp_path,
        song=[
            "The Killers|Mr. Brightside|Hot Fuss|222",
            "The Weeknd|Blinding Lights|After Hours|200",
        ],
    )
    records = probe_playback.remote(args, *fake_clients(server))
    assert [r["attempt"] for r in records] == ["first", "again"]
    assert all(r["song"] == "The Weeknd - Blinding Lights" for r in records)
    first = records[0]
    assert first["source"] == "Remote · YouTube" and first["route"] == "relayed"
    assert first["server"]["youtube-found"] == 12000 and first["open"]["status"] == 206
    # The relayed audio is fetched without the device token.
    media = [
        h for m, p, h in server.seen if p.startswith("/api/v1/remote-playback/streams/")
    ]
    assert media and all("authorization" not in h for h in media)
    printed = capsys.readouterr().out
    assert "in the household library, skipped" in printed
    # Only the stall during this run counts.
    assert "blocked 1 times during this run, 2.5 s in all" in printed
    saved = (tmp_path / "out.jsonl").read_text()
    assert '"scenario": "remote"' in saved and "probe-token-value" not in saved


def test_idle_plays_warm_then_after_each_pause(tmp_path, capsys):
    records = probe_playback.idle(
        args_for(tmp_path, track=[], gap=[0.01, 0.02]), *fake_clients(FakeServer())
    )
    assert [r["after_idle_s"] for r in records] == [0, 0, 0.01, 0.02]
    assert all("error" not in r and r["open"]["status"] == 206 for r in records)


def test_browse_times_each_screen_twice(tmp_path):
    records = probe_playback.browse(args_for(tmp_path), *fake_clients(FakeServer()))
    steps = [r["step"] for r in records]
    for step in (
        "sync tracks",
        "home mixes",
        "album page",
        "artist page",
        "playlist",
        "catalogue profile",
    ):
        assert steps.count(step) == 2, step
    assert all(r["status"] == 200 for r in records)


def test_artwork_counts_real_art_and_what_arrives_when_asked(tmp_path, capsys):
    records = probe_playback.artwork(
        args_for(tmp_path, settle=0.01), *fake_clients(FakeServer())
    )
    found = {r["group"]: (r["first"], r["after_settle"]) for r in records}
    assert found["library albums"] == (True, True)
    assert found["library artists"] == (False, True)
    assert found["home albums"] == (False, True) and found["home artists"] == (
        False,
        True,
    )
    assert found["artist pages"] == (False, True)
    printed = capsys.readouterr().out
    assert "library artists   1/1   with artwork (0 at first ask)" in printed
