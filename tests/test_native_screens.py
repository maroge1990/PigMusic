"""Screen rules Mark set for the iPhone app, checked in its Swift sources
(app views have no runtime tests; device checks confirm the look, T5)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "swift/PigMusic"


def test_no_preparing_audio_text_during_normal_playback():
    """#60: no text in the mini or full-screen player during normal use;
    only a problem gets words."""
    for source in APP.glob("*.swift"):
        text = source.read_text()
        for shown in ('ProgressView("Preparing audio', 'return "Preparing audio'):
            assert shown not in text, source.name
        # VoiceOver still hears what the silent spinner means.
        if "player.preparing" in text:
            assert '.accessibilityLabel("Preparing audio")' in text, source.name


def test_the_library_section_tabs_have_no_shuffle_button():
    """#61: the shuffle icon on the same level as the Library's sections
    (Artists, Albums, Musicals…) is removed entirely."""
    library = (APP / "LibraryViews.swift").read_text()
    assert "Shuffle all songs" not in library
    assert "shuffleLibrary" not in (APP / "AppModel.swift").read_text()


def test_every_album_grid_uses_the_library_layout():
    """#62: Popular albums on an artist page was a row of fixed 150-point
    cards, spaced unlike the Library's tight album grid. Every album list
    uses the Library's grid columns and tiles."""
    sources = "\n".join(p.read_text() for p in APP.glob("*.swift"))
    assert 'Shelf(title: "Popular albums")' not in sources
    catalogue = (APP / "CatalogueViews.swift").read_text()
    popular = catalogue[catalogue.index('Text("Popular albums")') :]
    assert popular.index("LazyVGrid(columns: cardColumns") < popular.index(
        "CollectionCard("
    )
