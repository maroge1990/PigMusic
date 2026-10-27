import json
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
