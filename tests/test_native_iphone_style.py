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


def test_errors_and_notices_share_one_status_line():
    """#159: errors were danger, secondary, default footnote or unstyled."""
    for raw in (
        "Text(error).foregroundStyle(.secondary)",
        "Text(error).font(",
        "Text(message).font(",
        "Text(failure).font(",
        "Text(statusFailure).font(",
        'Text("No matches").foregroundStyle(.secondary)',
        'Text("No catalogue results.").foregroundStyle(.secondary)',
    ):
        assert found(raw) == [], raw
    watch = (ROOT / "swift/PigMusicWatch/PigMusicWatchApp.swift").read_text()
    assert (
        "Text(error).font(.footnote).foregroundStyle(PigBrand.danger(scheme))" in watch
    )


def test_one_paging_label():
    """#159: "Show more…", "More … results" and "Load more …"."""
    labels = re.findall(
        r'Button\("((?:Show|Load) more[^"]*|More [^"]*results)"\)',
        "".join(sources().values()),
    )
    assert labels and all(label.startswith("Show more") for label in labels), labels


def test_whole_screen_empties_use_content_unavailable_view():
    assert (
        'ContentUnavailableView(\n          "No downloads yet"'
        in sources()["DownloadsView.swift"]
    )
