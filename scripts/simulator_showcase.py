"""Photograph every iPhone and Watch screen with a realistic library.

For design review. Starts the showcase server (scripts/serve_showcase_server.py:
a disposable library of a few hundred songs under real album names, with
playlists, listening history and requests in every state), signs the iPhone
simulator in to it and runs the ScreensTour UI test in light and dark; then
runs WatchScreensTour on the Watch's large demo library (`--demo --demo-big`),
since nothing sent from an iPhone simulator reaches a Watch simulator.
Pictures and an index page land in `.localdata/showcase/screens`. Runs on the
Mac, with the same needs as scripts/simulator_ui_tests.py.

    python3 scripts/simulator_showcase.py [--skip-build] [--only iphone|watch] [--keep]
"""

from __future__ import annotations

import argparse
import html
import os
import shutil
import subprocess
import sys
from pathlib import Path

import simulator_ui_tests as ui

WORK = ui.ROOT / ".localdata/showcase"
APPEARANCES = ("light", "dark")
# What the tour opens by name: all in the showcase library, or (search) not.
TOUR = {
    "PIG_TOUR_ARTIST": "Coldplay",
    "PIG_TOUR_ALBUM": "Parachutes",
    "PIG_TOUR_PLAYLIST": "Road Trip",
    "PIG_TOUR_SEARCH": "radiohead",
    "PIG_TOUR_CATALOGUE_ALBUM": "OK Computer",
}


def start_server() -> tuple[subprocess.Popen, dict]:
    account = WORK / "account.json"
    account.unlink(missing_ok=True)
    log = open(WORK / "server.log", "w")
    server = subprocess.Popen(
        [str(ui.ROOT / ".venv/bin/python"), "-m", "scripts.serve_showcase_server"]
        + ["--account", str(account)],
        cwd=ui.ROOT,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    try:
        return server, ui.wait_for(server, account)
    except BaseException:
        ui.stop_server(server)
        raise


def tour(kind: str, scheme: str, name: str, udid: str, environment: dict) -> bool:
    print(f"{scheme}/{name}", file=sys.stderr)
    result = subprocess.run(
        ["xcodebuild", "test", "-project", str(ui.TEST_PROJECT), "-scheme", scheme]
        + ["-destination", f"id={udid}", f"-only-testing:{scheme}/{name}"],
        capture_output=True,
        text=True,
        env={**os.environ, **environment},
    )
    tag = environment.get("TEST_RUNNER_PIG_SCREENS_APPEARANCE", kind)
    (WORK / f"{name}-{tag}.log").write_text(result.stdout)
    for line in result.stdout.splitlines():
        if " error: " in line or line.startswith(("Test Case", "** TEST")):
            print(line)
    return result.returncode == 0


def index_page(out: Path) -> Path:
    pictures = sorted(p.name for p in out.glob("*.png"))
    missing = sorted(p.name for p in out.glob("*missing-*.txt"))
    figures = "\n".join(
        f'<figure><img src="{html.escape(n)}" alt="" loading="lazy">'
        f"<figcaption>{html.escape(n[:-4])}</figcaption></figure>"
        for n in pictures
    )
    skipped = "".join(f"<li>{html.escape(n)}</li>" for n in missing)
    page = out / "index.html"
    page.write_text(
        '<!doctype html><meta charset="utf-8"><meta name="viewport" '
        'content="width=device-width,initial-scale=1"><title>PigMusic screens</title>'
        "<style>body{font:15px -apple-system,system-ui,sans-serif;margin:16px;"
        "background:#111;color:#eee}.all{display:grid;gap:14px;"
        "grid-template-columns:repeat(auto-fill,minmax(180px,1fr))}figure{margin:0}"
        "img{width:100%;border-radius:14px;border:1px solid #333}"
        "figcaption{font-size:12px;color:#aaa;text-align:center;margin-top:4px}</style>"
        f"<h1>PigMusic screens</h1><p>{len(pictures)} pictures, showcase library.</p>"
        f'<div class="all">{figures}</div>'
        + (f"<h2>Not reached</h2><ul>{skipped}</ul>" if skipped else "")
    )
    return page


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--only", choices=sorted(ui.APPS))
    parser.add_argument("--keep", action="store_true")
    parser.add_argument(
        "--appearance", choices=APPEARANCES, help="Only this one (default: both)"
    )
    parser.add_argument("--out", type=Path, default=WORK / "screens")
    args = parser.parse_args(argv)
    kinds = [args.only] if args.only else ["iphone", "watch"]
    devices = ui.paired()
    WORK.mkdir(parents=True, exist_ok=True)
    ui.WORK.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(args.out, ignore_errors=True)
    args.out.mkdir(parents=True)
    built = {}
    if not args.skip_build:
        for kind in kinds:
            built[kind] = ui.build(ui.APPS[kind], devices[kind])
    ok = True
    server = None
    # Other projects share the Mac's simulators: only what this run boots is
    # shut down afterwards.
    running_before = ui.booted()
    try:
        for kind in ("iphone", "watch"):
            ui.simctl("boot", devices[kind], check=False)
            ui.simctl("bootstatus", devices[kind], "-b")
        for kind, app in built.items():
            ui.simctl("uninstall", devices[kind], ui.BUNDLES[kind], check=False)
            ui.simctl("install", devices[kind], str(app))
        screens = {"TEST_RUNNER_PIG_SCREENS_DIR": str(args.out.resolve())}
        if "iphone" in kinds:
            server, account = start_server()
            ui.simctl("keychain", devices["iphone"], "reset")
            ui.simctl(
                "keychain",
                devices["iphone"],
                "add-root-cert",
                str(ui.WORK / "tls/ca.pem"),
            )
            ui.simctl("status_bar", devices["iphone"], "override", "--time", "9:41")
            for appearance in [args.appearance] if args.appearance else APPEARANCES:
                ui.simctl("ui", devices["iphone"], "appearance", appearance)
                environment = {
                    **screens,
                    "TEST_RUNNER_PIG_SCREENS_APPEARANCE": appearance,
                    "TEST_RUNNER_PIG_UI_SERVER": account["server"],
                    "TEST_RUNNER_PIG_UI_USERNAME": account["username"],
                    "TEST_RUNNER_PIG_UI_PASSWORD": account["password"],
                    **{"TEST_RUNNER_" + key: value for key, value in TOUR.items()},
                }
                ok &= tour(
                    "iphone",
                    "PigMusicUITests",
                    "ScreensTour",
                    devices["iphone"],
                    environment,
                )
            ui.simctl("ui", devices["iphone"], "appearance", "light")
            ui.simctl("status_bar", devices["iphone"], "clear", check=False)
        if "watch" in kinds:
            ok &= tour(
                "watch",
                "PigMusicWatchUITests",
                "WatchScreensTour",
                devices["watch"],
                screens,
            )
    finally:
        if args.keep and server:
            print(f"Showcase server left running: kill {server.pid}", file=sys.stderr)
        elif server:
            ui.stop_server(server)
        if not args.keep:
            for udid in devices.values():
                if udid not in running_before:
                    ui.simctl("shutdown", udid, check=False)
    page = index_page(args.out)
    print(f"{len(list(args.out.glob('*.png')))} pictures, index at {page}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
