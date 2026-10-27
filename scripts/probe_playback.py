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

Further scenarios take the same --server and --token-file: `remote` (online
sources, asked twice), `idle` (a song after pauses), `browse` (the app's
screens, opened twice), `artwork` (how much artwork is real), `watch` (the
device copies the phone sends to the Watch) and `server` (the server's
recent slow requests and stalls). Each ends with how often
the server's event loop stalled while it ran.
"""

import argparse
import getpass
import json
import os
import secrets
import statistics
import sys
import tempfile
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


def require_https(server):
    """Passwords, tokens and signed links go only to an HTTPS origin."""
    parts = urlsplit(server)
    if parts.scheme != "https" or not parts.hostname:
        sys.exit("Use the server's HTTPS address.")


def save_token(path, token):
    """Writes the token through a new private file, then moves it into place:
    an existing file's looser permissions never apply to the token."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".probe-token-")
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(token)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def login(args, client=None):
    """Signs in once and saves only the device token, readable only by you."""
    require_https(args.server)
    path = Path(args.token_file).expanduser()
    password = getpass.getpass(f"Password for {args.username}: ")
    owned = client is None
    client = client or httpx.Client(base_url=args.server, timeout=30)
    try:
        # Writes carry the header the apps send; the server refuses them without.
        answer = client.post(
            "/api/v1/auth/device",
            headers={"X-Pig-Request": "1"},
            json={
                "username": args.username,
                "password": password,
                "device_name": "Playback probe",
                "platform": "ios",
            },
        )
    finally:
        if owned:
            client.close()
    if answer.status_code != 200:
        sys.exit(f"Sign-in failed: HTTP {answer.status_code}")
    save_token(path, answer.json()["token"])
    print(f"Saved the probe's device token to {path}")


def clients(args):
    """An API client with the token and a media client without it."""
    require_https(args.server)
    token = read_token(args.token_file)
    api = httpx.Client(
        base_url=args.server,
        timeout=120,
        headers={"Authorization": "Bearer " + token, "X-Pig-Request": "1"},
    )
    return api, httpx.Client(base_url=args.server, timeout=120)


def save(args, record):
    if getattr(args, "output", None):
        with Path(args.output).expanduser().open("a") as handle:
            handle.write(json.dumps(record) + "\n")


class Records(list):
    """A list that saves each record as it is added."""

    def __init__(self, keep):
        super().__init__()
        self.keep = keep

    def append(self, record):
        super().append(record)
        self.keep(record)


def scenario(name):
    """Runs a scenario with its own clients unless given some, then says
    how often the server's event loop stalled while it ran."""

    def wrap(body):
        def run_scenario(args, api=None, media=None):
            require_https(args.server)
            owned = api is None
            if owned:
                api, media = clients(args)
            began = time.time()
            # Each record is saved as it is made: a later failure keeps it.
            records = Records(lambda r: save(args, {"scenario": name, **r}))
            try:
                body(args, api, media, records)
                return records
            finally:
                print(server_report(api, began))
                if owned:
                    api.close()
                    media.close()

        return run_scenario

    return wrap


def server_report(api, since):
    """The server's own record of this run: event-loop blocks and stalls."""
    try:
        answer = api.get("/api/v1/diagnostics")
        answer.raise_for_status()
        data = answer.json()
    except (httpx.HTTPError, ValueError):
        return "server: diagnostics unavailable"
    blocks = [b["ms"] for b in data.get("blocks", []) if b["at"] >= since]
    stalls = [s["seconds"] for s in data.get("stalls", []) if s["at"] >= since]
    if not blocks:
        return "server: no event-loop stalls during this run"
    return (
        f"server: event loop blocked {len(blocks)} times during this run,"
        f" {sum(blocks) / 1000:.1f} s in all, longest {max(blocks) / 1000:.1f} s"
        + (f"; {len(stalls)} stall stacks logged" if stalls else "")
    )


def timed(api, method, path, **kwargs):
    """One request with an ID: status, time, size and the server's phases."""
    rid = request_id()
    headers = {"X-Request-ID": rid, **kwargs.pop("headers", {})}
    began = time.perf_counter()
    try:
        answer = api.request(method, path, headers=headers, **kwargs)
    except httpx.HTTPError as exc:
        return {
            "request_id": rid,
            "error": type(exc).__name__,
            "ms": elapsed(began),
        }, None
    return {
        "request_id": rid,
        "status": answer.status_code,
        "ms": elapsed(began),
        "bytes": len(answer.content),
        "server": server_timing(answer.headers.get("server-timing")),
    }, answer


REMOTE_SONGS = [
    "The Weeknd|Blinding Lights|After Hours|200",
    "a-ha|Take On Me|Hunting High and Low|225",
    "Dua Lipa|Levitating|Future Nostalgia|203",
    "Tame Impala|The Less I Know the Better|Currents|216",
    "Lorde|Royals|Pure Heroine|190",
    "Kate Bush|Running Up That Hill (A Deal with God)|Hounds of Love|300",
]


def owned(api, artist, title):
    found = api.get("/api/v1/search", params={"q": f"{artist} {title}"})
    if found.status_code != 200:
        return False
    data = found.json()
    return any(
        t["title"].lower() == title.lower() and artist.lower() in t["artist"].lower()
        for t in data.get("household_tracks", []) + data.get("tracks", [])
    )


@scenario("remote")
def remote(args, api, media, records):
    """Songs not in the household, from online sources as the phone streams
    them: asked twice, so the second shows the server's relay cache."""
    for entry in args.song or REMOTE_SONGS:
        artist, title, album, seconds = (entry.split("|") + ["", "", "", "0"])[:4]
        if owned(api, artist, title):
            print(f"remote   {artist} - {title}: in the household library, skipped")
            continue
        for attempt in ("first", "again"):
            body = {"title": title, "artist": artist, "album": album}
            if float(seconds or 0) > 0:
                body["duration"] = float(seconds)
            record, answer = timed(api, "POST", "/api/v1/remote-playback", json=body)
            record.update({"song": f"{artist} - {title}", "attempt": attempt})
            if answer is not None and answer.status_code == 200:
                found = answer.json()
                record["source"] = found.get("source")
                url = found["url"]
                record["route"] = "relayed" if url.startswith("/") else "direct"
                try:
                    record["open"] = timed_get(media, url)
                except httpx.HTTPError as exc:
                    record["error"] = f"open: {type(exc).__name__}"
            elif "error" not in record:
                record["error"] = f"HTTP {record['status']}"
            records.append(record)
            phases = ", ".join(
                f"{k} {v:.0f}"
                for k, v in sorted(
                    record.get("server", {}).items(), key=lambda kv: -kv[1]
                )
                if k != "app" and v >= 20
            )
            print(
                f"remote   {record['song'][:40]:40} {attempt:5} {record['ms']:7.0f} ms"
                f"  {record.get('source') or record.get('error', '')}"
                + (
                    f"  open {record['open']['opening_ms']:.0f} ms"
                    if "open" in record
                    else ""
                )
                + (f"  [{phases}]" if phases else "")
                + f"  id {record['request_id']}",
                flush=True,
            )
    return records


@scenario("idle")
def idle(args, api, media, records):
    """One library song played warm, then again after each pause, on the
    same connections, as a phone left alone and picked up again."""
    chosen = songs(api, 1, 0, args.track[:1])
    if not chosen:
        sys.exit("No songs: add some to the probe's library, or pass --track.")
    for gap in [0, 0, *args.gap]:
        if gap:
            print(f"idle     waiting {gap:.0f} s", flush=True)
            time.sleep(gap)
        record = {"after_idle_s": gap, **probe_song(api, media, chosen[0])}
        records.append(record)
        print(f"idle     after {gap:5.0f} s  " + describe(record), flush=True)
    return records


def browse_steps(api):
    """What the app asks for when opened and browsed, in that order."""
    steps = [
        ("me", "/api/v1/me", {}),
        ("library summary", "/api/v1/library", {}),
        ("sync tracks", "/api/v1/library/tracks", {}),
        ("sync albums", "/api/v1/library/albums", {}),
        ("sync artists", "/api/v1/library/artists", {}),
        ("home recent", "/api/v1/home/recent", {}),
        ("home popular", "/api/v1/home/popular", {}),
        ("home recommended", "/api/v1/home/recommended", {}),
        ("home mixes", "/api/v1/home/mixes", {}),
        ("search", "/api/v1/search", {"q": "love"}),
        ("playlists", "/api/v1/playlists", {}),
    ]
    try:
        albums = api.get("/api/v1/library/albums").json()["items"]
        artists = api.get("/api/v1/library/artists").json()["items"]
        lists = api.get("/api/v1/playlists").json()["playlists"]
        popular = api.get("/api/v1/home/popular").json()
    except (httpx.HTTPError, ValueError, KeyError):
        return steps
    if albums:
        steps.append(("album page", f"/api/v1/albums/{albums[0]['id']}", {}))
    if artists:
        steps.append(("artist page", f"/api/v1/artists/{artists[0]['id']}", {}))
    if lists:
        steps.append(("playlist", f"/api/v1/playlists/{lists[0]['id']}", {}))
    for artist in (popular.get("artists") or [])[:1]:
        base = f"/api/v1/catalogue/artists/{artist['id']}"
        steps += [
            ("catalogue profile", base + "/profile", {}),
            ("catalogue releases", base + "/releases", {}),
            ("catalogue popularity", base + "/popularity", {}),
        ]
    return steps


@scenario("browse")
def browse(args, api, media, records):
    """Each screen's requests timed twice: as opened, and opened again."""
    for label, path, params in browse_steps(api):
        for attempt in ("first", "again"):
            record, _ = timed(api, "GET", path, params=params)
            record.update({"step": label, "path": path, "attempt": attempt})
            records.append(record)
            server = record.get("server", {}).get("app")
            print(
                f"browse   {label:22} {attempt:5} {record['ms']:7.0f} ms"
                + (f"  (server {server:.0f})" if server is not None else "")
                + f"  {record.get('status', record.get('error'))}"
                f"  {record.get('bytes', 0) // 1024} KB  id {record['request_id']}",
                flush=True,
            )
    return records


def artwork_items(api):
    """(group, name, path) for the artwork the app shows."""
    items = []
    for kind in ("albums", "artists"):
        answer = api.get(f"/api/v1/library/{kind}")
        if answer.status_code == 200:
            for item in answer.json()["items"]:
                if item.get("cover_url"):
                    name = item.get("title") or item.get("name")
                    items.append((f"library {kind}", name, item["cover_url"]))
    for feed in ("popular", "recommended"):
        answer = api.get(f"/api/v1/home/{feed}")
        if answer.status_code != 200:
            continue
        data = answer.json()
        for album in data.get("albums") or []:
            items.append(
                (
                    "home albums",
                    f"{album['artist']} - {album['name']}",
                    f"/api/v1/catalogue/artwork/release-group/{album['id']}",
                )
            )
        for song in data.get("songs") or []:
            if song.get("release_id"):
                items.append(
                    (
                        "home songs",
                        f"{song['artist']} - {song['name']}",
                        f"/api/v1/catalogue/artwork/release/{song['release_id']}",
                    )
                )
        for artist in data.get("artists") or []:
            items.append(
                (
                    "home artists",
                    artist["name"],
                    f"/api/v1/catalogue/artists/{artist['id']}/artwork",
                )
            )
        # An artist page's discography: the covers there come from the catalogue.
        for artist in (data.get("artists") or [])[:3]:
            answer = api.get(f"/api/v1/catalogue/artists/{artist['id']}/releases")
            if answer.status_code != 200:
                continue
            for release in answer.json().get("releases", [])[:12]:
                items.append(
                    (
                        "artist pages",
                        f"{artist['name']} - {release['title']}",
                        f"/api/v1/catalogue/artwork/release-group/{release['id']}",
                    )
                )
    return list(dict.fromkeys(items))


def has_artwork(api, path):
    """True for an image (or a redirect to one), False for none yet."""
    answer = api.get(path, params={"w": 256}, follow_redirects=False)
    if answer.status_code in (200, 301, 302, 303, 307, 308):
        return True
    if answer.status_code == 404:
        return False
    return None


@scenario("artwork")
def artwork(args, api, media, records):
    """How much of the artwork the app shows is real, and how much the
    server fills in after being asked (it fetches missing art lazily)."""
    items = artwork_items(api)
    first = {path: has_artwork(api, path) for _, _, path in items}
    missing = [path for path, found in first.items() if found is False]
    if missing and args.settle:
        print(f"artwork  {len(missing)} missing; asking again in {args.settle:.0f} s")
        time.sleep(args.settle)
    later = {path: has_artwork(api, path) for path in missing} if args.settle else {}
    groups = {}
    for group, name, path in items:
        found = later.get(path, first[path])
        record = {"group": group, "name": name, "path": path}
        record["first"], record["after_settle"] = first[path], found
        records.append(record)
        totals = groups.setdefault(group, [0, 0, 0, []])
        totals[0] += 1
        totals[1] += bool(first[path])
        totals[2] += bool(found)
        if not found:
            totals[3].append(name)
    for group, (count, at_first, settled, absent) in groups.items():
        print(
            f"artwork  {group:14} {settled:3}/{count:<3} with artwork"
            f" ({at_first} at first ask)"
            + (f"; missing: {', '.join(absent[:6])}" if absent else ""),
            flush=True,
        )
    return records


def copy_manifest(answer):
    """The device copy's manifest from an answer, or why there is none."""
    if answer is None:
        return None, "no answer"
    if answer.status_code != 200:
        return None, f"HTTP {answer.status_code}"
    try:
        manifest = answer.json()
    except ValueError:
        return None, "the answer was not JSON"
    if not isinstance(manifest, dict) or not isinstance(manifest.get("state"), str):
        return None, "the answer had no copy state"
    return manifest, None


def fetch_copy(api, media, base, params, expected, record):
    """Downloads a ready copy and checks its size and SHA-256 against the
    manifest. The device token goes only to this server: `base` is a path."""
    import hashlib

    if not isinstance(expected.get("bytes"), int) or not isinstance(
        expected.get("sha256"), str
    ):
        record["error"] = "the ready copy had no size or SHA-256"
        return
    digest = hashlib.sha256()
    began = time.perf_counter()
    with media.stream(
        "GET",
        base + "/file",
        params=params,
        headers={
            "Authorization": api.headers["Authorization"],
            "X-Request-ID": request_id(),
        },
    ) as response:
        record["download_status"] = response.status_code
        record["download_headers_ms"] = elapsed(began)
        size = 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            digest.update(chunk)
    seconds = max(time.perf_counter() - began, 1e-6)
    record["download"] = {
        "bytes": size,
        "ms": round(seconds * 1000, 1),
        "mbps": round(size * 8 / seconds / 1e6, 2),
    }
    record["intact"] = (
        size == expected["bytes"] and digest.hexdigest() == expected["sha256"]
    )
    if record["download_status"] != 200:
        record["error"] = f"download HTTP {record['download_status']}"
    elif not record["intact"]:
        record["error"] = "download does not match the advertised SHA-256"


@scenario("watch")
def watch(args, api, media, records):
    """What the phone does to send songs to the Watch: ask the server for a
    device copy (AAC by default, converted on the server), wait until it is
    ready, download it and check it against the advertised SHA-256. A song
    that fails is recorded with why, and the rest carry on."""
    chosen = songs(api, args.songs, 0, args.track)
    if not chosen:
        sys.exit("No songs: add some to the probe's library, or pass --track.")
    for song in chosen:
        base = f"/api/v1/tracks/{song['id']}/copy"
        params = {"quality": args.quality}
        record = {"id": song["id"], "title": song["title"], "quality": args.quality}
        record["polls"], record["ready_ms"] = 0, 0
        try:
            asked, answer = timed(api, "POST", base, params=params)
            record["request"] = asked
            manifest, problem = copy_manifest(answer)
            state = manifest["state"] if manifest else None
            record["state_at_request"] = state
            began = time.perf_counter()
            polls = []
            # Bounded by --wait; one poll a second, as the app does.
            while (
                state in ("queued", "preparing") and elapsed(began) < args.wait * 1000
            ):
                time.sleep(1)
                poll, answer = timed(api, "GET", base, params=params)
                polls.append(poll["ms"])
                manifest, problem = copy_manifest(answer)
                state = manifest["state"] if manifest else None
            record["ready_ms"] = elapsed(began) if polls else 0
            record["polls"] = len(polls)
            record["slowest_poll_ms"] = max(polls) if polls else None
            record["state"] = state
            if problem:
                record["error"] = "copy: " + problem
            elif state != "ready":
                record["error"] = f"copy {state}"
            else:
                fetch_copy(api, media, base, params, manifest, record)
        except httpx.HTTPError as exc:
            record["error"] = f"{type(exc).__name__} during the copy"
        records.append(record)
        line = f"watch    {song['title'][:30]:30} {args.quality:8}"
        if "request" in record:
            line += (
                f" request {record['request']['ms']:6.0f} ms ({record.get('state_at_request')})"
                f"  ready after {record['ready_ms'] / 1000:5.1f} s ({record['polls']} polls)"
            )
        if "download" in record:
            line += (
                f"  download {record['download']['bytes'] // 1024} KB in"
                f" {record['download']['ms']:.0f} ms ({record['download']['mbps']} Mbit/s)"
                + ("" if record["intact"] else "  CORRUPT")
            )
        if "error" in record:
            line += "  ERROR " + record["error"]
        print(line, flush=True)
    return records


def server_command(args):
    """The server's recent record for the probe member: slow requests and stalls."""
    api, media = clients(args)
    try:
        data = api.get("/api/v1/diagnostics").raise_for_status().json()
    finally:
        api.close()
        media.close()
    print(f"server up {data['uptime_seconds'] / 3600:.1f} h; pools {data['pools']}")
    blocks = [b["ms"] for b in data["blocks"]]
    if blocks:
        print(
            f"event loop blocked {len(blocks)} times recently,"
            f" {sum(blocks) / 1000:.1f} s in all, longest {max(blocks) / 1000:.1f} s"
        )
    slow = sorted(
        (r for r in data["requests"] if r["headers_ms"] is not None),
        key=lambda r: -r["headers_ms"],
    )[:10]
    for r in slow:
        phases = ", ".join(f"{n} {ms:.0f}" for n, ms in r["phases"] if ms >= 20)
        print(
            f"{r['headers_ms']:8.0f} ms  {r['method']} {r['route']}  [{phases}]  id {r['id']}"
        )


def run(args, api=None, media=None):
    require_https(args.server)
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
    for name, help_text in (
        ("remote", "time songs from online sources, twice each"),
        ("idle", "time a song after pauses, as a phone picked up again"),
        ("browse", "time the app's screens, opened twice"),
        ("artwork", "how much artwork is real, and filled in when asked"),
        ("server", "the server's recent slow requests and stalls"),
        ("watch", "time the device copies the phone sends to the Watch"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--server", required=True)
        command.add_argument("--token-file", required=True)
        command.add_argument("--output", help="append JSON lines here")
        if name == "remote":
            command.add_argument(
                "--song", action="append", default=[], help="Artist|Title|Album|Seconds"
            )
        if name == "idle":
            command.add_argument("--track", action="append", default=[])
            command.add_argument(
                "--gap", type=float, action="append", default=[], help="seconds idle"
            )
        if name == "artwork":
            command.add_argument("--settle", type=float, default=30)
        if name == "watch":
            command.add_argument("--songs", type=int, default=5)
            command.add_argument("--track", action="append", default=[])
            command.add_argument(
                "--quality", choices=["aac", "original"], default="aac"
            )
            command.add_argument("--wait", type=float, default=300, help="seconds")
    args = parser.parse_args(argv)
    if args.command == "idle" and not args.gap:
        args.gap = [60, 300, 900]
    {
        "login": login,
        "run": run,
        "remote": remote,
        "idle": idle,
        "browse": browse,
        "artwork": artwork,
        "server": server_command,
        "watch": watch,
    }[args.command](args)


if __name__ == "__main__":
    main()
