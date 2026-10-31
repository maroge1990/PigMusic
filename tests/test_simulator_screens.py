"""The screenshot script's launch switches must be ones the app understands."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts/simulator_screens.py"
APP = ROOT / "swift/PigMusic"

pytestmark = pytest.mark.skipif(
    not (SCRIPT.exists() and APP.exists()),
    reason="scripts/ or swift/ absent (container image mounts only tests/)",
)


def load():
    import importlib.util

    spec = importlib.util.spec_from_file_location("simulator_screens", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_screen_uses_switches_and_tabs_the_app_defines():
    screens = load()
    sources = "\n".join(p.read_text() for p in APP.glob("*.swift"))
    tabs = {
        m.lower() for m in re.findall(r'case \w+ = "(\w+)"', sources)
    }  # Destination raw values
    names = [name for name, _ in screens.SCREENS]
    assert len(names) == len(set(names))
    for _, arguments in screens.SCREENS:
        for argument in arguments:
            switch, _, value = argument.partition("=")
            assert f'"{switch}{"=" if value else ""}"' in sources, argument
            if switch == "--tab":
                assert value in tabs, argument
    assert '"--demo"' in sources


def test_list_mode_prints_without_a_simulator(capsys):
    assert load().main(["--list"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("home --demo --tab=home")
    assert "now-playing --demo --tab=home --demo-play --show-player" in out
