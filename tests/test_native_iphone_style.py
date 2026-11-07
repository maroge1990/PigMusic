"""Batch 5b: the iPhone app's shared styles (Swift sources; the simulator
tap-through tests and screens on the Mac show the result)."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "swift/PigMusic"

pytestmark = pytest.mark.skipif(
    not APP.exists(), reason="native app sources are not in this image"
)


def sources():
    return {path.name: path.read_text() for path in sorted(APP.glob("*.swift"))}


def found(needle):
    return [name for name, text in sources().items() if needle in text]


def test_one_chip_design_and_one_secondary_button():
    """#161: Search drew its own white-on-accent chips; secondary buttons were
    .bordered capsules, chips or plain text."""
    # SwiftUI's Color.white on a chip; UIKit drawing (UIColor) is not a chip.
    assert [
        n for n, t in sources().items() if re.search(r"(?<!UI)Color\.white", t)
    ] == []
    assert found(".buttonStyle(.bordered)") == []
    assert found("ChipStyle(selected: false)") == []
    search = sources()["SearchViews.swift"]
    assert "ChipStyle(selected: option == kind)" in search


def test_favourites_are_a_star_and_colours_are_brand_roles():
    """#161: a heart in one row, stars elsewhere; .green, Color.accentColor,
    Color.secondary.opacity and the PigDanger asset beside PigBrand.danger."""
    assert found('"heart.fill"') == []
    for raw in (
        ".foregroundStyle(.green)",
        "Color.accentColor",
        "Color.secondary.opacity",
        'Color("PigDanger")',
    ):
        assert found(raw) == [], raw


def test_one_wording_for_adding_to_the_library():
    assert found('"Add to library"') == []
