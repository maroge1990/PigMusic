"""The simulator UI tests' Xcode project must match its generator, so a test
file added under swift/PigMusicUITests is never left out of the build."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "scripts/generate_ui_test_project.py"


@pytest.mark.skipif(
    not GENERATOR.exists(), reason="native project sources are not in this image"
)
def test_the_ui_test_project_matches_its_generator():
    spec = importlib.util.spec_from_file_location("generate_ui_test_project", GENERATOR)
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    assert generator.main(["--check"]) == 0
