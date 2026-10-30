"""Screen rules Mark set for the iPhone app, checked in its Swift sources
(app views have no runtime tests; device checks confirm the look, T5)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "swift/PigMusic"


def test_no_preparing_audio_text_during_normal_playback():
    """#60: no text in the mini or full-screen player during normal use;
    only a problem gets words."""
    for source in APP.glob("*.swift"):
        assert "Preparing audio" not in source.read_text(), source.name
