"""The tap-through runner's CI options: device choice and the launch retry (#265)."""

import subprocess

import pytest

from source import SOURCE

SCRIPT = SOURCE / "scripts/simulator_ui_tests.py"

pytestmark = pytest.mark.skipif(
    not SCRIPT.exists(), reason="scripts/ absent (container image mounts only tests/)"
)


def load():
    import importlib.util

    spec = importlib.util.spec_from_file_location("simulator_ui_tests", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PAIRS = {
    "a": {
        "phone": {"name": "iPad Pro", "udid": "pad"},
        "watch": {"name": "Apple Watch SE", "udid": "w0"},
    },
    "b": {
        "phone": {"name": "iPhone 16", "udid": "p1"},
        "watch": {"name": "Apple Watch Series 10 (46mm)", "udid": "w1"},
    },
    "c": {
        "phone": {"name": "iPhone 17", "udid": "p2"},
        "watch": {"name": "Apple Watch Series 12 (46mm)", "udid": "w2"},
    },
}


def test_the_default_is_still_the_first_iphone_pair(monkeypatch):
    ui = load()
    monkeypatch.delenv("PIG_UI_IPHONE", raising=False)
    monkeypatch.delenv("PIG_UI_WATCH", raising=False)
    args = ui.parse_args([])
    assert (args.iphone, args.watch) == (None, None)
    assert not args.create_pair and not args.retry_launch_timeouts
    assert ui.choose_pair(PAIRS) == {"iphone": "p1", "watch": "w1"}


def test_devices_come_from_the_environment_or_the_options(monkeypatch):
    ui = load()
    monkeypatch.setenv("PIG_UI_IPHONE", "iPhone 17")
    monkeypatch.setenv("PIG_UI_WATCH", "Apple Watch Series 12 (46mm)")
    args = ui.parse_args([])
    assert ui.choose_pair(PAIRS, args.iphone, args.watch) == {
        "iphone": "p2",
        "watch": "w2",
    }
    args = ui.parse_args(["--iphone", "iPhone 16", "--watch", "Nope"])
    assert ui.choose_pair(PAIRS, args.iphone, args.watch) is None
    assert ui.choose_pair(PAIRS, "iPhone 16") == {"iphone": "p1", "watch": "w1"}


def test_a_new_pair_uses_the_newest_runtimes():
    ui = load()

    def runtime(platform, version, *devices, available=True):
        return {
            "identifier": f"rt.{platform}-{version}",
            "name": f"{platform} {version}",
            "platform": platform,
            "version": version,
            "isAvailable": available,
            "supportedDeviceTypes": [
                {"name": name, "identifier": f"dt.{name}", "productFamily": family}
                for family, name in devices
            ],
        }

    runtimes = [
        runtime("iOS", "26.10", ("iPhone", "iPhone 17"), ("iPad", "iPad Pro")),
        runtime("iOS", "26.9", ("iPhone", "iPhone 16")),
        runtime("iOS", "27.0", ("iPhone", "iPhone 18"), available=False),
        runtime(
            "watchOS",
            "26.2",
            ("Apple Watch", "Apple Watch Series 11"),
            ("Apple Watch", "Apple Watch Ultra 3"),
        ),
    ]
    plan = ui.new_pair_plan(runtimes)
    assert plan == {
        "iphone": ("iPhone 17", "dt.iPhone 17", "rt.iOS-26.10"),
        "watch": ("Apple Watch Ultra 3", "dt.Apple Watch Ultra 3", "rt.watchOS-26.2"),
    }
    assert ui.new_pair_plan(runtimes, watch="Apple Watch Series 11")["watch"][0] == (
        "Apple Watch Series 11"
    )
    with pytest.raises(SystemExit):
        ui.new_pair_plan(runtimes, iphone="iPhone 16")


class Runs:
    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.calls = 0

    def __call__(self, command, **kwargs):
        self.calls += 1
        code, out = self.outputs.pop(0)
        return subprocess.CompletedProcess(command, code, out, "")


TIMEOUT = "error: Timed out attempting to launch app\n** TEST FAILED **"
ASSERTION = "error: XCTAssertTrue failed - Failed to find Next song\n** TEST FAILED **"


@pytest.mark.parametrize(
    "text",
    ["Failed to launch", "Timed out attempting to launch app", "Scene update failed"],
)
def test_only_the_launch_timeout_texts_count(text):
    ui = load()
    assert ui.launch_timed_out(f"x\n{text} y\n")
    assert not ui.launch_timed_out(ASSERTION)


def test_a_launch_timeout_is_retried_once_when_asked(tmp_path):
    ui = load()
    log = tmp_path / "Scheme-Class.log"
    runs = Runs((65, TIMEOUT), (0, "** TEST SUCCEEDED **"))
    result = ui.run_tests(["xcodebuild"], {}, log, True, run=runs)
    assert (runs.calls, result.returncode) == (2, 0)
    assert log.read_text() == "** TEST SUCCEEDED **"
    assert (tmp_path / "Scheme-Class.attempt1.log").read_text() == TIMEOUT

    runs = Runs((65, TIMEOUT), (65, TIMEOUT), (0, ""))
    result = ui.run_tests(["xcodebuild"], {}, log, True, run=runs)
    assert (runs.calls, result.returncode) == (2, 65)


def test_any_other_failure_or_no_retry_option_fails_at_once(tmp_path):
    ui = load()
    log = tmp_path / "Scheme-Class.log"
    runs = Runs((65, ASSERTION), (0, ""))
    assert ui.run_tests(["xcodebuild"], {}, log, True, run=runs).returncode == 65
    assert runs.calls == 1
    assert log.read_text() == ASSERTION

    runs = Runs((65, TIMEOUT), (0, ""))
    assert ui.run_tests(["xcodebuild"], {}, log, False, run=runs).returncode == 65
    assert runs.calls == 1
    assert not (tmp_path / "Scheme-Class.attempt1.log").exists()
