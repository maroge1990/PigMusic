"""#163: the Watch's settings say what the phone says (Swift sources; the
tap-through tests on the Mac show the screens)."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WATCH = ROOT / "swift/PigMusicWatch/PigMusicWatchApp.swift"
PHONE = ROOT / "swift/PigMusic"

pytestmark = pytest.mark.skipif(
    not WATCH.exists(), reason="native app sources are not in this image"
)


def test_the_watch_has_one_repeat_picker():
    assert WATCH.read_text().count('Picker("Repeat"') == 1


def test_the_watch_uses_the_phones_transport_glyphs():
    watch = WATCH.read_text()
    phone = (PHONE / "LibraryViews.swift").read_text()
    for glyph in ("backward.fill", "forward.fill"):
        assert f'"{glyph}"' in phone
        assert f'"{glyph}"' in watch
    assert "backward.end.fill" not in watch and "forward.end.fill" not in watch


def test_the_watch_describes_crossfade_as_the_phone_does():
    """The phone: a three-second crossfade between unrelated songs."""
    watch = WATCH.read_text()
    assert "three-second crossfade between unrelated songs" in watch
    assert "over 3 seconds" not in watch


def test_the_controls_artist_line_is_secondary_and_labels_are_plain():
    watch = WATCH.read_text()
    # The Text and the modifiers on its following lines.
    artist = re.search(r"\.track\.artist\)[^\n]*(?:\n\s*\.[^\n]*)*", watch).group(0)
    assert ".secondary" in artist
    assert '"Play / pause"' not in watch
