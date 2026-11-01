"""Run the tap-through UI tests on a paired iPhone and Watch simulator.

Builds the Debug PigMusic and PigMusicWatch apps for the pair, installs them,
runs swift/PigMusicUITests (iPhone: the offline `--demo` library; Watch: its
own screens) and shuts the simulators down. Runs on a Mac with Xcode and the
iOS and watchOS simulator runtimes; see docs/TESTING.md.

    python3 scripts/simulator_ui_tests.py [--skip-build] [--only iphone|watch] [--keep]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_PROJECT = ROOT / "swift/PigMusic.xcodeproj"
TEST_PROJECT = ROOT / "swift/PigMusicUITests/PigMusicUITests.xcodeproj"
# (device kind, app scheme, test scheme)
RUNS = {
    "iphone": ("PigMusic", "PigMusicUITests"),
    "watch": ("PigMusicWatch", "PigMusicWatchUITests"),
}


def simctl(*args, check=True):
    return subprocess.run(
        ["xcrun", "simctl", *args], check=check, capture_output=True, text=True
    )


def paired() -> dict[str, str]:
    """{"iphone": udid, "watch": udid} for the first available iPhone pair."""
    pairs = json.loads(simctl("list", "pairs", "available", "-j").stdout)["pairs"]
    for pair in pairs.values():
        if pair["phone"]["name"].startswith("iPhone"):
            return {"iphone": pair["phone"]["udid"], "watch": pair["watch"]["udid"]}
    raise SystemExit(
        "No paired iPhone and Watch simulator: create one with "
        "`xcrun simctl pair <watch udid> <iphone udid>`."
    )


def xcodebuild(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["xcodebuild", *args], capture_output=True, text=True)


def build(scheme: str, udid: str) -> Path:
    """Build `scheme` for the simulator `udid`; returns the built .app."""
    common = [
        "-project",
        str(APP_PROJECT),
        "-scheme",
        scheme,
        "-configuration",
        "Debug",
        "-destination",
        f"id={udid}",
        "-skipPackagePluginValidation",
        "CODE_SIGNING_ALLOWED=NO",
    ]
    result = xcodebuild("-quiet", *common, "build")
    if result.returncode:
        sys.stderr.write(result.stdout[-4000:] + result.stderr[-4000:])
        raise SystemExit(f"{scheme} did not build")
    settings = json.loads(xcodebuild(*common, "-showBuildSettings", "-json").stdout)
    values = settings[0]["buildSettings"]
    return Path(values["BUILT_PRODUCTS_DIR"]) / values["FULL_PRODUCT_NAME"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--skip-build", action="store_true", help="Test the apps already installed"
    )
    parser.add_argument("--only", choices=sorted(RUNS))
    parser.add_argument(
        "--keep", action="store_true", help="Leave the simulators running"
    )
    args = parser.parse_args(argv)
    devices = paired()
    kinds = [args.only] if args.only else list(RUNS)
    failed = []
    try:
        for kind in kinds:
            app_scheme, test_scheme = RUNS[kind]
            udid = devices[kind]
            if not args.skip_build:
                app = build(app_scheme, udid)
            simctl("boot", udid, check=False)
            simctl("bootstatus", udid, "-b")
            if not args.skip_build:
                simctl("install", udid, str(app))
            print(f"{test_scheme} on {udid}", file=sys.stderr)
            result = subprocess.run(
                [
                    "xcodebuild",
                    "test",
                    "-project",
                    str(TEST_PROJECT),
                    "-scheme",
                    test_scheme,
                    "-destination",
                    f"id={udid}",
                ],
                capture_output=True,
                text=True,
            )
            for line in result.stdout.splitlines():
                if " error: " in line or line.startswith(("Test Case", "** TEST")):
                    print(line)
            if result.returncode:
                failed.append(test_scheme)
    finally:
        if not args.keep:
            simctl("shutdown", "all", check=False)
    if failed:
        print("Failed:", ", ".join(failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
