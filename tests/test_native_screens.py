"""Screen rules Mark set for the iPhone app, checked in its Swift sources
(app views have no runtime tests; device checks confirm the look, T5)."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "swift/PigMusic"

# The container image tests mount only tests/; the app sources are absent
# there and these checks run wherever the checkout is whole.
pytestmark = pytest.mark.skipif(
    not APP.exists(), reason="native app sources are not in this image"
)


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
    root = library.split("struct LibraryView: View {", 1)[1].split("\n}\n", 1)[0]
    assert "shuffle" not in root.lower()
    # D-120: "Shuffle your library" lives only on an empty Up next.
    assert library.count("model.shuffleLibrary()") == 1
    nothing = library.split("private struct NothingNext: View {", 1)[1]
    assert "model.shuffleLibrary()" in nothing.split("\n}\n", 1)[0]


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


def test_the_song_menu_never_asks_to_load_the_favourite_state():
    """#284: the menu offered "Load favourite status" for any song whose
    state wasn't cached. The library's Favourites playlist answers it; an
    unknown state is plain "Favourite", which makes the song one."""
    app = {p.name: p.read_text() for p in APP.glob("*.swift")}
    assert [n for n, t in app.items() if "Load favourite" in t] == []
    model = app["AppModel.swift"]
    peek = model.split("func peekFavourite(", 1)[1].split("\n  }\n", 1)[0]
    assert "FavouriteStates.listed(track, in: playlists)" in peek
    menu = app["PersonalActionsViews.swift"]
    unknown = menu.split("case .favouriteUnknown:", 1)[1].split("case .", 1)[0]
    assert 'Button("Favourite", systemImage: "star")' in unknown
    assert "orFavourite: true" in unknown
