"""Play songs the way the iPhone does and time each step, against any server.

For a dedicated test member, never a household account. `login` asks for the
password (never on the command line) and saves only the device token, to a
private file. `run` then reads the token from that file and, for each song:
asks for its playback address, opens the file from its start (as playback
does), and reads near its end (a seek, or an index stored there). Media
requests carry only the signed link, never the token. Each request has an
`X-Request-ID` that the server's log line uses, and the server's own share
of the wait comes back in `Server-Timing`, so a slow step reads as server,
or connection. Nothing is changed beyond the test member's own mix leases;
nothing is downloaded into the library.

    .venv/bin/python -m scripts.probe_playback login --server https://music.example --username probe --token-file ~/.config/pigmusic/probe-token
    .venv/bin/python -m scripts.probe_playback run --server https://music.example --token-file ~/.config/pigmusic/probe-token --output probe.jsonl
"""

import argparse
import getpass
import json
import os
import secrets
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx

OPENING = 64 * 1024


def request_id():
    return "probe-" + secrets.token_hex(6)


def server_timing(header):
    """{"app": 12.3, "mix-lease": 9.1, ...} from a Server-Timing header."""
    phases = {}
    for entry in (header or "").split(","):
        name, _, rest = entry.strip().partition(";")
        for part in rest.split(";"):
            if part.strip().startswith("dur="):
                try:
                    phases[name] = phases.get(name, 0) + float(part.strip()[4:])
                except ValueError:
                    pass
    return phases


def elapsed(began):
    return round((time.perf_counter() - began) * 1000, 1)


def songs(api, library, mix_songs, chosen):
    """Explicit IDs, then songs in the member's library, then mix songs."""
    picked = [{"id": i, "title": "", "kind": "explicit"} for i in chosen]
    if library:
        page = api.get(
            "/api/v1/library-pages/tracks", params={"offset": 0, "limit": 200}
        )
        page.raise_for_status()
        tracks = page.json()["items"]
        picked += [
            {"id": t["id"], "title": t.get("title", ""), "kind": "library"}
            for t in tracks[:library]
        ]
    if mix_songs:
        feed = api.get("/api/v1/home/mixes")
        feed.raise_for_status()
        mixed = [
            {"id": t["id"], "title": t.get("title", ""), "kind": "mix"}
            for mix in feed.json()["mixes"]
            for t in mix["tracks"]
        ]
        # Spread across mixes and positions: late songs found the slow cases.
        step = max(1, len(mixed) // mix_songs)
        picked += mixed[::step][:mix_songs]
    return picked


def timed_get(media, url, start=None):
    """GET with a range; time to headers and to the first `OPENING` bytes."""
    headers = {"X-Request-ID": request_id()}
    headers["Range"] = f"bytes={start}-" if start is not None else "bytes=0-"
    began = time.perf_counter()
    result = {"request_id": headers["X-Request-ID"]}
    with media.stream("GET", url, headers=headers) as response:
        result["status"] = response.status_code
        result["headers_ms"] = elapsed(began)
        result["server"] = server_timing(response.headers.get("server-timing"))
        result["type"] = response.headers.get("content-type", "")
        content_range = response.headers.get("content-range", "")
        if "/" in content_range:
            result["total"] = int(content_range.rsplit("/", 1)[1])
        elif response.headers.get("content-length"):
            result["total"] = int(response.headers["content-length"])
        received = 0
        for chunk in response.iter_bytes():
            received += len(chunk)
            if received >= OPENING:
                break
        result["opening_ms"] = elapsed(began)
        result["opening_bytes"] = received
    return result


def probe_song(api, media, song, full=False):
    """One song: its address, its opening, then a read near its end."""
    record = {
        "at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "id": song["id"],
        "kind": song["kind"],
        "title": song["title"],
    }
    asked = request_id()
    began = time.perf_counter()
    try:
        answer = api.post(
            f"/api/v1/playback/{song['id']}/url",
            headers={"X-Request-ID": asked, "X-Pig-Request": "1"},
        )
    except httpx.HTTPError as exc:
        record["error"] = f"address: {type(exc).__name__}"
        return record
    record["address"] = {
        "request_id": asked,
        "status": answer.status_code,
        "ms": elapsed(began),
        "server": server_timing(answer.headers.get("server-timing")),
    }
    if answer.status_code != 200:
        record["error"] = f"address: HTTP {answer.status_code}"
        return record
    url = answer.json()["url"]
    try:
        record["open"] = timed_get(media, url)
        total = record["open"].get("total")
        if record["open"]["status"] not in (200, 206):
            record["error"] = f"open: HTTP {record['open']['status']}"
        elif total and total > 2 * OPENING:
            record["seek"] = timed_get(media, url, start=total - OPENING)
            if record["seek"]["status"] != 206:
                record["error"] = f"seek: HTTP {record['seek']['status']} (no range)"
        if full and "error" not in record:
            began = time.perf_counter()
            with media.stream("GET", url) as response:
                size = sum(len(chunk) for chunk in response.iter_bytes())
            seconds = max(time.perf_counter() - began, 1e-6)
            record["full"] = {
                "bytes": size,
                "ms": round(seconds * 1000, 1),
                "mbps": round(size * 8 / seconds / 1e6, 2),
            }
    except httpx.HTTPError as exc:
        record["error"] = f"media: {type(exc).__name__}"
    return record


def describe(record):
    if "address" not in record:
        return f"{record['kind']:8} {record['id'][:40]:40} {record['error']}"
    address = record["address"]
    server = address["server"].get("app")
    parts = [
        f"address {address['ms']:7.0f} ms",
        f"(server {server:.0f})" if server is not None else "(no server timing)",
    ]
    if "open" in record:
        parts.append(f"open {record['open']['headers_ms']:6.0f} ms")
        parts.append(f"64 KB {record['open']['opening_ms']:6.0f} ms")
    if "seek" in record:
        parts.append(f"seek {record['seek']['headers_ms']:6.0f} ms")
    if "full" in record:
        parts.append(f"{record['full']['mbps']} Mbit/s")
    if "error" in record:
        parts.append("ERROR " + record["error"])
    slowest = sorted(
        ((ms, name) for name, ms in address["server"].items() if name != "app"),
        reverse=True,
    )[:2]
    if server is not None and server >= 500:
        parts.append(", ".join(f"{name} {ms:.0f}" for ms, name in slowest))
    parts.append(f"id {address['request_id']}")
    return f"{record['kind']:8} {record['id'][:40]:40} " + "  ".join(parts)


def summary(records):
    lines = []
    for label, values in (
        ("address", [r["address"]["ms"] for r in records if "address" in r]),
        (
            "address, server share",
            [r["address"]["server"].get("app", 0) for r in records if "address" in r],
        ),
        ("open", [r["open"]["headers_ms"] for r in records if "open" in r]),
        ("first 64 KB", [r["open"]["opening_ms"] for r in records if "open" in r]),
        ("seek", [r["seek"]["headers_ms"] for r in records if "seek" in r]),
    ):
        if values:
            ordered = sorted(values)
            p95 = ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))]
            lines.append(
                f"{label:22} median {statistics.median(values):7.0f} ms"
                f"  p95 {p95:7.0f} ms  max {max(values):7.0f} ms  n={len(values)}"
            )
    failures = [r for r in records if "error" in r]
    lines.append(f"failures: {len(failures)} of {len(records)}")
    return "\n".join(lines)


def read_token(path):
    path = Path(path).expanduser()
    if path.stat().st_mode & 0o077:
        sys.exit(f"{path} must be private to you (chmod 600)")
    return path.read_text().strip()


def login(args):
    """Signs in once and saves only the device token, readable only by you."""
    path = Path(args.token_file).expanduser()
    password = getpass.getpass(f"Password for {args.username}: ")
    with httpx.Client(base_url=args.server, timeout=30) as client:
        answer = client.post(
            "/api/v1/auth/device",
            json={
                "username": args.username,
                "password": password,
                "device_name": "Playback probe",
                "platform": "ios",
            },
        )
    if answer.status_code != 200:
        sys.exit(f"Sign-in failed: HTTP {answer.status_code}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(answer.json()["token"])
    print(f"Saved the probe's device token to {path}")


def run(args, api=None, media=None):
    host = urlsplit(args.server)
    if host.scheme != "https" and host.hostname not in {"127.0.0.1", "localhost"}:
        sys.exit("Use the server's HTTPS address.")
    owned = api is None
    if owned:
        token = read_token(args.token_file)
        api = httpx.Client(
            base_url=args.server,
            timeout=60,
            headers={"Authorization": "Bearer " + token},
        )
        media = httpx.Client(base_url=args.server, timeout=60)
    output = Path(args.output).expanduser() if args.output else None
    records = []
    try:
        rounds = 0
        while True:
            rounds += 1
            chosen = songs(api, args.library, args.mix_songs, args.track)
            if not chosen:
                sys.exit("No songs: add some to the probe's library, or pass --track.")
            for song in chosen:
                record = probe_song(api, media, song, full=args.full)
                records.append(record)
                print(describe(record), flush=True)
                if output:
                    with output.open("a") as handle:
                        handle.write(json.dumps(record) + "\n")
            if args.repeat and rounds >= args.repeat:
                break
            time.sleep(args.interval)
    finally:
        if owned:
            api.close()
            media.close()
    print(summary(records))
    return records


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)
    sign_in = commands.add_parser("login", help="save a device token for the probe")
    sign_in.add_argument("--server", required=True)
    sign_in.add_argument("--username", required=True)
    sign_in.add_argument("--token-file", required=True)
    probe = commands.add_parser("run", help="time playback of some songs")
    probe.add_argument("--server", required=True)
    probe.add_argument("--token-file", required=True)
    probe.add_argument("--library", type=int, default=5, help="songs from the library")
    probe.add_argument("--mix-songs", type=int, default=5, help="songs from mixes")
    probe.add_argument("--track", action="append", default=[], help="a song ID")
    probe.add_argument("--full", action="store_true", help="also time whole files")
    probe.add_argument("--repeat", type=int, default=1, help="rounds; 0 runs on")
    probe.add_argument("--interval", type=float, default=300, help="seconds between")
    probe.add_argument("--output", help="append JSON lines here")
    args = parser.parse_args(argv)
    login(args) if args.command == "login" else run(args)


if __name__ == "__main__":
    main()
