"""Fake MusicBrainz, ListenBrainz, Cover Art Archive and Apple chart for the
T3 rig (#15). Answers are deterministic from the requested ids, shaped like
the real services' responses the server reads, and never touch the network.

    python -m scripts.fake_services --port 8790

Requests arrive as /<real host>/<real path> (server/test_services.py).
POST /_control with JSON {"delay": seconds, "fail": status or 0,
"albums": n} changes every later answer; GET /_stats counts requests by
service and path kind.
"""

import argparse
import hashlib
import json
import re
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

STATE = {"delay": 0.0, "fail": 0, "albums": 40}
STATS = Counter()
LOCK = threading.Lock()


def mbid(*parts):
    """A stable UUID-shaped id from text."""
    h = hashlib.sha256(":".join(parts).encode()).hexdigest()
    return f"{h[:8]}-{h[8:12]}-4{h[13:16]}-8{h[17:20]}-{h[20:32]}"


def musicbrainz(path, query):
    parts = [p for p in path.split("/") if p][2:]  # after ws/2
    kind = parts[0] if parts else ""
    if kind == "release-group" and len(parts) > 1:
        identity = parts[1]
        return {
            "id": identity,
            "title": "Album " + identity[:4],
            "primary-type": "Album",
            "secondary-types": [],
            "first-release-date": "20%02d" % (int(identity[:2], 16) % 25),
        }
    if kind == "artist" and len(parts) > 1:
        return {"id": parts[1], "name": "Artist " + parts[1][:4], "type": "Group"}
    if kind == "artist":
        name = (query.get("query") or [""])[0]
        return {"artists": [{"id": mbid("artist", name), "name": name, "score": 100}]}
    if kind in ("recording", "release", "release-group"):
        return {kind + "s": [], "count": 0}
    return {}


def listenbrainz(path, query):
    found = re.search(r"top-(recordings|release-groups)-for-artist/([0-9a-f-]+)", path)
    if found:
        kind, artist = found.groups()
        if kind == "recordings":
            return [
                {
                    "artist_mbids": [artist],
                    "artist_name": "Artist " + artist[:4],
                    "recording_mbid": mbid(artist, "recording", str(n)),
                    "recording_name": "Song %d" % n,
                    "release_mbid": mbid(artist, "release", str(n % 5)),
                    "release_name": "Album %d" % (n % 5),
                    "total_listen_count": 1000 - n,
                }
                for n in range(25)
            ]
        return [
            {
                "release_group_mbid": mbid(artist, "group", str(n)),
                "release_group": {
                    "type": "Album",
                    "name": "Album %d" % n,
                    "date": "2020",
                },
                "total_listen_count": 1000 - n,
            }
            for n in range(STATE["albums"])
        ]
    if "lb-radio" in path:
        return {"payload": {"jspf": {"playlist": {"track": []}}}}
    return []


def answer(host, path, query):
    if host == "musicbrainz.org":
        return 200, musicbrainz(path, query)
    if host.endswith("listenbrainz.org"):
        return 200, listenbrainz(path, query)
    if host == "rss.marketingtools.apple.com":
        return 200, {"feed": {"results": []}}
    return 404, {}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/_stats":
            with LOCK:
                return self.send(200, dict(STATS))
        host, _, rest = url.path.lstrip("/").partition("/")
        kind = "/".join(rest.split("/")[:3])
        with LOCK:
            STATS[host + " " + kind] += 1
            delay, fail = STATE["delay"], STATE["fail"]
        if delay:
            time.sleep(delay)
        if fail:
            return self.send(fail, {"error": "fake failure"})
        status, body = answer(host, "/" + rest, parse_qs(url.query))
        self.send(status, body)

    def do_POST(self):
        if urlsplit(self.path).path != "/_control":
            return self.send(404, {})
        length = int(self.headers.get("Content-Length") or 0)
        change = json.loads(self.rfile.read(length) or b"{}")
        with LOCK:
            STATE.update({k: v for k, v in change.items() if k in STATE})
            if change.get("reset_stats"):
                STATS.clear()
            self.send(200, dict(STATE))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
