"""Run the tap-through UI tests on a paired iPhone and Watch simulator.

Builds the Debug PigMusic and PigMusicWatch apps for the pair, installs them
fresh, starts the disposable HTTPS test server (scripts/serve_ui_test_server.py)
and runs swift/PigMusicUITests in order: the empty Watch, the iPhone (the
offline `--demo` library, then signing in to the server and sending an album
to the Watch) and the Watch's own `--demo` library. WatchConnectivity file
transfers never reach a Watch simulator, so what the iPhone sends stops there.
Shuts the simulators and the server down afterwards. Runs on a Mac with Xcode,
the iOS and watchOS simulator runtimes, FFmpeg and the server's Python
packages in `.venv`; see docs/TESTING.md.

    python3 scripts/simulator_ui_tests.py [--skip-build] [--only iphone|watch] [--keep]
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import ssl
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_PROJECT = ROOT / "swift/PigMusic.xcodeproj"
TEST_PROJECT = ROOT / "swift/PigMusicUITests/PigMusicUITests.xcodeproj"
WORK = ROOT / ".localdata/ui-tests"
APPS = {"iphone": "PigMusic", "watch": "PigMusicWatch"}
BUNDLES = {"iphone": "au.com.pig.music", "watch": "au.com.pig.music.watchkitapp"}
# (device, test scheme, test classes), in the order they run.
PHASES = (
    ("watch", "PigMusicWatchUITests", ["WatchLaunchTests"]),
    ("iphone", "PigMusicUITests", ["DemoLibraryTests", "ServerTests"]),
    ("watch", "PigMusicWatchUITests", ["WatchDemoTests"]),
)


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


def booted() -> set[str]:
    """UDIDs of the simulators running now, whoever started them."""
    listing = json.loads(simctl("list", "devices", "booted", "-j").stdout)["devices"]
    return {device["udid"] for runtime in listing.values() for device in runtime}


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
        # Signed to run locally: an unsigned build has no keychain access.
        "CODE_SIGN_IDENTITY=-",
        "CODE_SIGN_STYLE=Manual",
        "DEVELOPMENT_TEAM=",
    ]
    result = xcodebuild("-quiet", *common, "build")
    if result.returncode:
        sys.stderr.write(result.stdout[-4000:] + result.stderr[-4000:])
        raise SystemExit(f"{scheme} did not build")
    settings = json.loads(xcodebuild(*common, "-showBuildSettings", "-json").stdout)
    values = settings[0]["buildSettings"]
    return Path(values["BUILT_PRODUCTS_DIR"]) / values["FULL_PRODUCT_NAME"]


def start_server() -> tuple[subprocess.Popen, dict]:
    """Starts the test server; returns it and its account once it answers."""
    account = WORK / "account.json"
    account.unlink(missing_ok=True)
    log = open(WORK / "server.log", "w")
    server = subprocess.Popen(
        [str(ROOT / ".venv/bin/python"), "-m", "scripts.serve_ui_test_server"],
        cwd=ROOT,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    try:
        return server, wait_for(server, account)
    except BaseException:
        stop_server(server)
        raise


def stop_server(server: subprocess.Popen):
    """Ctrl-C, not SIGTERM: Uvicorn re-raises SIGTERM after shutting down,
    which would end the server before it deletes its temporary folder."""
    server.send_signal(signal.SIGINT)
    try:
        server.wait(timeout=30)
    except subprocess.TimeoutExpired:
        server.kill()


def wait_for(server: subprocess.Popen, account: Path) -> dict:
    for _ in range(120):
        if server.poll() is not None:
            raise SystemExit(f"The test server stopped: see {WORK / 'server.log'}")
        # The account is written once the certificates exist.
        if account.is_file():
            details = json.loads(account.read_text())
            trust = ssl.create_default_context(cafile=str(WORK / "tls/ca.pem"))
            try:
                urllib.request.urlopen(
                    details["server"] + "/api/health", context=trust, timeout=2
                )
                return details
            except OSError:
                pass
        time.sleep(1)
    raise SystemExit("The test server did not answer within two minutes")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--skip-build", action="store_true", help="Test the apps already installed"
    )
    parser.add_argument("--only", choices=sorted(APPS))
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Leave the simulators and the test server running",
    )
    args = parser.parse_args(argv)
    devices = paired()
    WORK.mkdir(parents=True, exist_ok=True)
    built = {}
    if not args.skip_build:
        for kind, scheme in APPS.items():
            built[kind] = build(scheme, devices[kind])
    failed = []
    server = None
    # Other projects share the Mac's simulators: only the devices this run
    # boots are shut down afterwards, never all of them.
    running_before = booted()
    try:
        server, account = start_server()
        # The Watch boots only beside its booted iPhone.
        for kind in ("iphone", "watch"):
            simctl("boot", devices[kind], check=False)
            simctl("bootstatus", devices[kind], "-b")
        # A clean sign-in each run: forget saved sessions, then trust the
        # test server's certificate authority.
        simctl("keychain", devices["iphone"], "reset")
        simctl("keychain", devices["iphone"], "add-root-cert", str(WORK / "tls/ca.pem"))
        for kind, app in built.items():
            simctl("uninstall", devices[kind], BUNDLES[kind], check=False)
            simctl("install", devices[kind], str(app))
        environment = {
            **os.environ,
            "TEST_RUNNER_PIG_UI_SERVER": account["server"],
            "TEST_RUNNER_PIG_UI_USERNAME": account["username"],
            "TEST_RUNNER_PIG_UI_PASSWORD": account["password"],
        }
        for kind, scheme, classes in PHASES:
            if args.only and kind != args.only:
                continue
            print(f"{scheme} ({', '.join(classes)})", file=sys.stderr)
            result = subprocess.run(
                [
                    "xcodebuild",
                    "test",
                    "-project",
                    str(TEST_PROJECT),
                    "-scheme",
                    scheme,
                    "-destination",
                    f"id={devices[kind]}",
                    *(f"-only-testing:{scheme}/{name}" for name in classes),
                ],
                capture_output=True,
                text=True,
                env=environment,
            )
            # The whole log, with any failing screen's accessibility tree.
            (WORK / f"{scheme}-{classes[0]}.log").write_text(result.stdout)
            for line in result.stdout.splitlines():
                if " error: " in line or line.startswith(("Test Case", "** TEST")):
                    print(line)
            if result.returncode:
                failed.append(f"{scheme} ({', '.join(classes)})")
    finally:
        if args.keep and server:
            print(f"Test server left running: kill {server.pid}", file=sys.stderr)
        elif server:
            stop_server(server)
        if not args.keep:
            for udid in devices.values():
                if udid not in running_before:
                    simctl("shutdown", udid, check=False)
    if failed:
        print("Failed:", "; ".join(failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
