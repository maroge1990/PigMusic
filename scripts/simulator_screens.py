"""Photograph every iPhone screen in a booted simulator, light and dark.

Uses the Debug build's headless review switches (`--demo` for the synthetic
offline library, `--tab=<name>`, `--open-album`, `--demo-play`) so no server,
account or network is needed. Runs on a macOS machine with Xcode; the
`screens.yml` workflow runs it on a GitHub macOS runner and uploads the PNGs.

    python3 scripts/simulator_screens.py --app <PigMusic.app> --out screens

`--list` prints the screens without touching a simulator.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

BUNDLE_ID = "au.com.pig.music"
APPEARANCES = ("light", "dark")
# (name, launch arguments after --demo). Order is the order on the index page.
SCREENS = (
    ("home", ["--tab=home"]),
    ("library", ["--tab=library"]),
    ("album", ["--tab=library", "--open-album"]),
    ("downloads", ["--tab=downloads"]),
    ("search", ["--tab=search"]),
    ("now-playing", ["--tab=home", "--demo-play"]),
)


def simctl(*args, check=True, capture=True):
    return subprocess.run(
        ["xcrun", "simctl", *args], check=check, capture_output=capture, text=True
    )


def choose_device(preferred: str | None) -> tuple[str, str]:
    """The newest available iPhone (or the named one): (udid, label)."""
    listing = json.loads(simctl("list", "devices", "available", "-j").stdout)
    candidates = []
    for runtime, devices in listing["devices"].items():
        version = tuple(int(n) for n in re.findall(r"\d+", runtime)[-2:] or (0,))
        for device in devices:
            if not device.get("isAvailable") or not device["name"].startswith("iPhone"):
                continue
            if preferred and device["name"] != preferred:
                continue
            # Plain models before Pro/Max/Plus/e of the same generation.
            plain = 0 if re.fullmatch(r"iPhone \d+", device["name"]) else 1
            candidates.append((version, -plain, device["name"], device["udid"]))
    if not candidates:
        raise SystemExit(
            f"No available iPhone simulator{f' named {preferred}' if preferred else ''}"
        )
    version, _, name, udid = max(candidates)
    return udid, f"{name} (iOS {'.'.join(map(str, version))})"


def boot(udid: str):
    simctl("boot", udid, check=False)
    simctl("bootstatus", udid, "-b")
    # A stable status bar for review pages.
    simctl(
        "status_bar",
        udid,
        "override",
        "--time",
        "9:41",
        "--batteryState",
        "charged",
        "--batteryLevel",
        "100",
        "--cellularMode",
        "active",
        "--cellularBars",
        "4",
        "--wifiBars",
        "3",
        check=False,
    )


def photograph(
    udid: str, app: Path, out: Path, wait: float, bundle_id: str
) -> list[Path]:
    simctl("install", udid, str(app))
    out.mkdir(parents=True, exist_ok=True)
    taken = []
    for appearance in APPEARANCES:
        simctl("ui", udid, "appearance", appearance)
        first = True
        for name, arguments in SCREENS:
            simctl(
                "launch",
                "--terminate-running-process",
                udid,
                bundle_id,
                "--demo",
                *arguments,
            )
            time.sleep(wait * (2 if first else 1))  # The first launch is cold.
            first = False
            path = out / f"{name}-{appearance}.png"
            simctl("io", udid, "screenshot", str(path))
            taken.append(path)
            print(f"{path}", file=sys.stderr)
        simctl("terminate", udid, bundle_id, check=False)
    return taken


def index_page(out: Path, label: str) -> Path:
    rows = "\n".join(
        f'<section><h2>{name}</h2><div class="pair">'
        + "".join(
            f'<figure><img src="{name}-{a}.png" alt="{name}, {a}" loading="lazy">'
            f"<figcaption>{a}</figcaption></figure>"
            for a in APPEARANCES
        )
        + "</div></section>"
        for name, _ in SCREENS
    )
    html = f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PigMusic iPhone screens</title>
<style>body{{font:16px -apple-system,system-ui,sans-serif;margin:16px;background:#111;color:#eee}}
h1{{font-size:20px}}h2{{font-size:16px;margin:24px 0 8px;text-transform:capitalize}}
.pair{{display:flex;gap:12px;flex-wrap:wrap}}figure{{margin:0;flex:1 1 160px;max-width:300px}}
img{{width:100%;border-radius:18px;border:1px solid #333}}figcaption{{font-size:13px;color:#aaa;text-align:center;margin-top:4px}}</style>
<h1>PigMusic iPhone screens</h1><p>{label}, demo library, light and dark.</p>
{rows}"""
    path = out / "index.html"
    path.write_text(html)
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--app", type=Path, help="PigMusic.app built for the simulator")
    parser.add_argument("--out", type=Path, default=Path("screens"))
    parser.add_argument(
        "--device", help="Simulator name, e.g. 'iPhone 17'; default: newest iPhone"
    )
    parser.add_argument("--bundle-id", default=BUNDLE_ID)
    parser.add_argument(
        "--wait", type=float, default=6.0, help="Seconds for a screen to settle"
    )
    parser.add_argument(
        "--list", action="store_true", help="Print the screens and exit"
    )
    args = parser.parse_args(argv)
    if args.list:
        for name, arguments in SCREENS:
            print(name, " ".join(["--demo", *arguments]))
        return 0
    if not args.app or not args.app.is_dir():
        parser.error("--app must point at a built PigMusic.app")
    udid, label = choose_device(args.device)
    print(f"Simulator: {label}", file=sys.stderr)
    boot(udid)
    taken = photograph(udid, args.app, args.out, args.wait, args.bundle_id)
    page = index_page(args.out, label)
    print(f"{len(taken)} screenshots, index at {page}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
