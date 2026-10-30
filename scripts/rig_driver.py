"""Runs inside the T3 rig's app container (#15): prepares a disposable
library and a generated owner, then runs a check or a timing scenario
against the server on 127.0.0.1 (trusted as the HTTPS proxy) and prints one
JSON report. Started by scripts/test_rig.py; never pointed at production.

    python /rig/scripts/rig_driver.py check|artist|mixes
"""

import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8765"
HEADERS = {"X-Forwarded-Proto": "https", "X-Pig-Request": "1"}
STATE = Path(os.environ.get("RIG_STATE", "/data/rig-owner.json"))
LIBRARY = Path(os.environ.get("PIG_MUSIC_LIBRARY", "/library"))
FAKE = os.environ.get("PIG_MUSIC_TEST_SERVICES", "")
ARTISTS, ALBUMS, SONGS = 6, 2, 5


def wait_healthy(client):
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            if client.get("/api/health").status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise SystemExit("server never became healthy")


def owner(client):
    """A generated test owner (rig appdata only), then a device token."""
    if STATE.exists():
        saved = json.loads(STATE.read_text())
    else:
        saved = {"username": "rigowner", "password": secrets.token_urlsafe(24)}
        code = os.environ["RIG_INSTALLATION_CODE"]
        response = client.post(
            "/api/v1/setup/owner",
            json={**saved, "code": code, "display_name": "Rig owner"},
        )
        response.raise_for_status()
        STATE.write_text(json.dumps(saved))
        STATE.chmod(0o600)
    token = client.post(
        "/api/v1/auth/device",
        json={**saved, "device_name": "rig", "platform": "ios"},
    )
    token.raise_for_status()
    client.headers["Authorization"] = "Bearer " + token.json()["token"]


def library(client):
    """A disposable library of short tagged tones, scanned once."""
    if not any(LIBRARY.rglob("*.mp3")):
        for a in range(ARTISTS):
            for b in range(ALBUMS):
                folder = LIBRARY / f"Rig Artist {a}" / f"Rig Album {a}-{b}"
                folder.mkdir(parents=True, exist_ok=True)
                for n in range(1, SONGS + 1):
                    subprocess.run(
                        [
                            "ffmpeg",
                            "-loglevel",
                            "error",
                            "-y",
                            "-f",
                            "lavfi",
                            "-i",
                            f"sine=frequency={220 + 40 * n}:duration=3",
                            "-metadata",
                            f"title=Rig Song {a}-{b}-{n}",
                            "-metadata",
                            f"artist=Rig Artist {a}",
                            "-metadata",
                            f"album_artist=Rig Artist {a}",
                            "-metadata",
                            f"album=Rig Album {a}-{b}",
                            "-metadata",
                            f"track={n}",
                            "-b:a",
                            "128k",
                            str(folder / f"{n:02d} Rig Song.mp3"),
                        ],
                        check=True,
                    )
    started = client.post("/api/v1/admin/library/scan")
    started.raise_for_status()
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        state = client.get("/api/v1/admin/library/scan").json()
        if state.get("status") in {"completed", "failed"}:
            return state
        time.sleep(2)
    raise SystemExit("scan did not finish")


def fake(path, body=None):
    with httpx.Client(base_url=FAKE, timeout=10) as client:
        response = (
            client.post(path, json=body) if body is not None else client.get(path)
        )
        return response.json()


def timed(client, path):
    start = time.monotonic()
    response = client.get(path, timeout=300)
    return response.status_code, round(time.monotonic() - start, 2), response


def prepare(client):
    wait_healthy(client)
    owner(client)
    client.put(
        "/api/v1/admin/settings/media",
        json={
            "portraits": False,
            "remote_playback": False,
            "listenbrainz_token": "00000000-0000-4000-8000-0000000000aa",
        },
    ).raise_for_status()


def check(client):
    prepare(client)
    scan = library(client)
    stats = client.get("/api/v1/admin/library").json().get("stats", {})
    status, seconds, _ = timed(client, "/api/v1/home/mixes")
    return {
        "scan": scan.get("status"),
        "files": scan.get("files"),
        "library_tracks": stats.get("tracks", 0),
        "home_mixes": {"status": status, "seconds": seconds},
        "fake_requests": fake("/_stats"),
    }


def artist(client):
    """#21: a cold artist page (40 album lookups on the old code)."""
    prepare(client)
    fake("/_control", {"reset_stats": True, "albums": 40})
    identity = "%08x-0000-4000-8000-%012x" % (int(time.time()), secrets.randbits(48))
    status, seconds, response = timed(
        client, f"/api/v1/catalogue/artists/{identity}/popularity"
    )
    body = response.json() if status == 200 else {}
    return {
        "status": status,
        "seconds": seconds,
        "songs": len(body.get("songs", [])),
        "albums": len(body.get("albums", [])),
        "fake_requests": fake("/_stats"),
    }


def mixes(client):
    """#22: Home mixes listing timings while the server prepares mixes."""
    prepare(client)
    timings = []
    for _ in range(20):
        status, seconds, _ = timed(client, "/api/v1/home/mixes")
        timings.append(seconds)
        time.sleep(3)
    timings.sort()
    return {"p50": timings[len(timings) // 2], "p95": timings[-1], "all": timings}


def main():
    scenario = sys.argv[1] if len(sys.argv) > 1 else "check"
    with httpx.Client(base_url=BASE, headers=HEADERS, timeout=60) as client:
        result = {"check": check, "artist": artist, "mixes": mixes}[scenario](client)
    print(json.dumps({"scenario": scenario, **result}, indent=2))


if __name__ == "__main__":
    main()
