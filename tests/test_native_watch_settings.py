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


def test_the_watch_crossfade_switch_has_no_explanation():
    """#163 gave the Watch the phone's crossfade wording; D-101 (#215) then
    removed the explanation and kept the switch."""
    watch = WATCH.read_text()
    assert 'Toggle("Crossfade"' in watch
    assert "three-second crossfade" not in watch
    assert "over 3 seconds" not in watch


def test_the_controls_artist_line_is_secondary_and_labels_are_plain():
    watch = WATCH.read_text()
    # The Text and the modifiers on its following lines.
    artist = re.search(r"\.track\.artist\)[^\n]*(?:\n\s*\.[^\n]*)*", watch).group(0)
    assert ".secondary" in artist
    assert '"Play / pause"' not in watch


def test_the_watch_shows_the_iphone_song_instead_of_a_phone_remote():
    """D-104 (#220): three blind "Phone remote" buttons needed both apps
    reachable. The Watch's home shows the iPhone's song instead, and Apple's
    Now Playing controls the iPhone's audio; the phone no longer takes
    playback commands from the Watch."""
    watch = WATCH.read_text()
    model = (ROOT / "swift/PigMusicWatch/WatchModel.swift").read_text()
    phone = (PHONE / "WatchPhoneController.swift").read_text()
    assert '"Phone remote"' not in watch
    for command in ('"playPause"', '"next"', '"previous"'):
        assert command not in watch and command not in model and command not in phone
    row = watch[watch.index("struct WatchPhoneNowPlayingRow") :]
    row = row[: row.index("\nprivate struct")]
    assert "PhoneNowPlaying.showsRow(" in row and "NowPlayingView()" in row
    assert '"Now playing on iPhone, ' in row and 'systemImage: "iphone"' in row
    assert "PhoneNowPlaying.decode(data)" in model
    assert "sendNowPlaying(" in (PHONE / "AppModel.swift").read_text()


def test_a_reinstalled_watch_gets_the_phone_song_again():
    """Codex on #220: the phone sent its song once and kept the key, so a
    Watch reinstalled mid-song never showed the iPhone row until the song
    changed. Every (re)activation or install change resends the latest."""
    controller = (PHONE / "WatchPhoneController.swift").read_text()
    activated = controller[controller.index("link?.onActivated = {") :]
    activated = activated[: activated.index("link?.onMessage")]
    assert "resendNowPlaying()" in activated
    resend = controller[controller.index("private func resendNowPlaying()") :]
    resend = resend[: resend.index("\n  }\n")]
    assert "nowPlayingSent = nil" in resend
    send = controller[controller.index("func sendNowPlaying(") :]
    assert send.index("nowPlayingLatest =") < send.index("guard link?.available")
