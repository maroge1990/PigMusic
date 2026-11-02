"""Disposable HTTPS PigMusic for the simulator tap-through tests (#113).

Generates a small library (two albums of FFmpeg tones, one song with a long
title), an owner account with a random password and a loopback-only TLS
certificate, refuses every metadata provider, then serves on
https://127.0.0.1:8790 until stopped. Everything lives in a temporary folder
except the certificate authority, which stays in `--tls` so the simulators
trust it once (`xcrun simctl keychain <udid> add-root-cert <tls>/ca.pem`). The
account is written to `--account` as JSON for scripts/simulator_ui_tests.py.
Needs FFmpeg and openssl.

    python -m scripts.serve_ui_test_server [--tls DIR] [--account FILE]
"""

import argparse
import json
import os
import secrets
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT = 8790
ORIGIN = f"https://127.0.0.1:{PORT}"
USERNAME = "uitest"
# (album, artist, [(title, seconds)])
ALBUMS = (
    (
        "Night Drive",
        "The Test Pilots",
        [
            ("Headlights", 40),
            ("A Very Long Song Title That Keeps On Going Past the Edge", 40),
            ("Last Exit", 40),
        ],
    ),
    ("Paper Boats", "Harbour", [("Low Tide", 40), ("Lantern", 40)]),
)


def certificates(folder: Path) -> tuple[Path, Path]:
    """A lasting CA, and a fresh 127.0.0.1 server certificate it signs."""
    folder.mkdir(parents=True, exist_ok=True)
    ca_key, ca = folder / "ca-key.pem", folder / "ca.pem"
    # Extension files rather than -addext, which macOS's LibreSSL lacks; strict
    # verifiers (Python 3.13+, iOS) need the key usages and key identifiers.
    if not ca.is_file():
        ca_request, ca_extensions = folder / "ca.csr", folder / "ca.ext"
        ca_extensions.write_text(
            "basicConstraints=critical,CA:TRUE\nkeyUsage=critical,keyCertSign,cRLSign\n"
            "subjectKeyIdentifier=hash\n"
        )
        subprocess.run(
            ["openssl", "req", "-newkey", "rsa:2048", "-nodes", "-keyout", str(ca_key)]
            + ["-out", str(ca_request), "-subj", "/CN=PigMusic UI test CA"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["openssl", "x509", "-req", "-in", str(ca_request), "-signkey", str(ca_key)]
            + ["-out", str(ca), "-days", "3650", "-sha256"]
            + ["-extfile", str(ca_extensions)],
            check=True,
            capture_output=True,
        )
    key, request, cert = (folder / n for n in ("key.pem", "server.csr", "cert.pem"))
    extensions = folder / "server.ext"
    extensions.write_text(
        "basicConstraints=CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\n"
        "extendedKeyUsage=serverAuth\nsubjectAltName=IP:127.0.0.1,DNS:localhost\n"
        "subjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid\n"
    )
    subprocess.run(
        ["openssl", "req", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key)]
        + ["-out", str(request), "-subj", "/CN=127.0.0.1"],
        check=True,
        capture_output=True,
    )
    # Apple accepts at most 825 days for a server certificate.
    subprocess.run(
        ["openssl", "x509", "-req", "-in", str(request), "-CA", str(ca)]
        + ["-CAkey", str(ca_key), "-CAcreateserial", "-out", str(cert)]
        + ["-days", "365", "-sha256", "-extfile", str(extensions)],
        check=True,
        capture_output=True,
    )
    return key, cert


def library(root: Path) -> list[str]:
    """Writes the songs and their snapshot; returns the track ids."""
    tracks = []
    for album_number, (album, artist, songs) in enumerate(ALBUMS):
        for number, (title, seconds) in enumerate(songs, start=1):
            name = f"album-{album_number}-{number}.flac"
            subprocess.run(
                ["ffmpeg", "-loglevel", "error", "-f", "lavfi"]
                + [
                    "-i",
                    f"sine=frequency={220 * (number + album_number)}:duration={seconds}",
                ]
                + ["-ac", "2", "-ar", "44100", str(root / name)],
                check=True,
            )
            tracks.append(
                {
                    "id": f"track-{album_number}-{number}",
                    "title": title,
                    "artist": artist,
                    "album_artist": artist,
                    "album": album,
                    "track_number": number,
                    "duration": seconds,
                    "available": True,
                    "relative_path": name,
                }
            )
    (root / "library-snapshot.json").write_text(json.dumps({"tracks": tracks}))
    return [track["id"] for track in tracks]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tls", type=Path, default=ROOT / ".localdata/ui-tests/tls")
    parser.add_argument(
        "--account", type=Path, default=ROOT / ".localdata/ui-tests/account.json"
    )
    args = parser.parse_args(argv)
    os.environ.pop("PIG_MUSIC_TEST_LEGACY_API", None)
    os.environ.update(
        PIG_MUSIC_PRELOAD="false",
        PIG_MUSIC_EMBEDDED_ENGINE="false",
        PIG_MUSIC_PUBLIC_ORIGIN=ORIGIN,
        # The T3 rig's switch, aimed at a closed local port: every metadata
        # request fails at once and none reaches a real provider.
        PIG_MUSIC_TEST_SERVICES="http://127.0.0.1:9",
    )
    key, cert = certificates(args.tls)
    with tempfile.TemporaryDirectory(prefix="pig-ui-tests-") as temporary:
        root = Path(temporary)
        os.environ["PIG_MUSIC_LIBRARY"] = str(root)
        os.environ["PIG_MUSIC_DATA"] = str(root)
        ids = library(root)
        from server.auth import Auth
        from server.store import Store

        store = Store(root)
        password = secrets.token_urlsafe(18)
        owner = Auth(store).create_owner(USERNAME, password, "UI test listener")
        store.user_libraries.add(owner, ids)
        args.account.parent.mkdir(parents=True, exist_ok=True)
        args.account.write_text(
            json.dumps({"server": ORIGIN, "username": USERNAME, "password": password})
        )
        import uvicorn
        from server.app import create_app

        print(f"Serving {ORIGIN} as {USERNAME}", flush=True)
        uvicorn.run(
            create_app(root),
            host="127.0.0.1",
            port=PORT,
            ssl_keyfile=str(key),
            ssl_certfile=str(cert),
            access_log=False,
            # The app keeps a connection open; don't wait on it at Ctrl-C.
            timeout_graceful_shutdown=5,
        )


if __name__ == "__main__":
    main()
