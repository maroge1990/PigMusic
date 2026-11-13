"""#180: the iPhone app against brand 1.8 sections 8, 14 and 16 (Swift
sources; the simulator tap-through tests and screens on the Mac show the
result)."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "swift/PigMusic"

pytestmark = pytest.mark.skipif(
    not APP.exists(), reason="native app sources are not in this image"
)


def source(name):
    return (APP / name).read_text()


def test_the_splash_ends_when_the_saved_library_is_shown():
    """Brand 1.8 section 8: the splash shows only while loading, with no
    minimum time. It waited up to 1.5 s after a restored account attached."""
    app = source("PigMusicApp.swift")
    scene = app[app.index("var body: some Scene") : app.index("struct RootView")]
    assert "Task.sleep" not in scene
    assert re.search(r"onChange\(of: model\.launched\)[^}]*finishStarting\(\)", app)
    model = source("AppModel.swift")
    restore = model[model.index("func restore() async") :]
    restore = restore[: restore.index("\n  func ")]
    # Launch is over once the saved library is attached, before the refresh,
    # and on every other way out of restore (sign-in form, demo, failure).
    attached = restore.index("try await attach(connection.person")
    assert attached < restore.index("launched = true", attached)
    assert restore.index("launched = true", attached) < restore.index("await refresh()")
    assert re.search(r"defer \{[^}]*launched = true", restore)


def test_empty_and_error_screens_show_the_pig_with_one_line():
    """D-82: the iPhone's quiet moments show the pig with one short line, as
    the web's Empty does. They were system ContentUnavailableViews; Library
    had no error state and Downloads offered "Connect your library" with no
    way to act on it."""
    for name in ("SearchViews.swift", "LibraryViews.swift", "DownloadsView.swift"):
        assert "ContentUnavailableView" not in source(name), name
    assert "Connect your library" not in source("DownloadsView.swift")
    brand = (ROOT / "swift/PigKit/Sources/PigBrand/BrandViews.swift").read_text()
    quiet = brand[brand.index("public struct PigQuietState") :]
    assert 'Image("PigMark")' in quiet and ".accessibilityHidden(true)" in quiet
    library = source("LibraryViews.swift")
    empty = library[library.index("private var emptyLibrary") :]
    empty = empty[: empty.index("\n  }\n")]
    # Loading, error with a way to recover, and empty with the one action.
    assert "model.syncing" in empty
    assert "model.syncFailed" in empty and "model.refresh()" in empty
    assert "model.openSearch()" in empty
    model = source("AppModel.swift")
    assert "syncFailed = true" in model and "syncFailed = false" in model


def test_no_page_opens_on_a_spinner_alone():
    """Brand 1.8 section 14: no spinner alone on a blank page. The artist,
    album and Apple-link pages opened on a bare ProgressView filling the
    screen; they now say what is opening, as the web's Loading does."""
    bare = re.compile(r"ProgressView\(\)\s*\.frame\(maxWidth: \.infinity, maxHeight")
    for path in sorted(APP.glob("*.swift")):
        assert not bare.search(path.read_text()), path.name
    assert source("SearchViews.swift").count("PigLoading(") == 3
    assert "PigLoading(" in source("LibraryViews.swift")


def test_plain_words_on_the_iphone():
    """Brand 1.8 section 14: no technical terms a household member would not
    use. "server" stays as the name of a place, as on the web ("On the
    server"); sync jargon and server-centred labels go."""
    swift_strings = re.compile(r'"((?:[^"\\]|\\.)*)"')
    shown = []
    for path in sorted(APP.glob("*.swift")):
        for line in path.read_text().splitlines():
            # Comments and diagnostics lines are not shown to anyone.
            if line.lstrip().startswith("//") or "DiagnosticsLog" in line:
                continue
            shown += swift_strings.findall(line)
    for banned in (
        "Server request",
        "Your server",
        "Server quality",
        "Songs on the server",
        "Preparing on server",
        "Server confirmed",
        "to sync",
        "Sync now",
        "Syncing",
        "synced",
        "Pending edits",
        "pending edit",
        "on this phone",
    ):
        assert not [s for s in shown if banned in s], banned


def test_a_failed_download_says_so_in_plain_words():
    """Brand 1.8 section 14: an error says what happened in plain words,
    never a raw error. A failed download showed only a red mark, and
    VoiceOver read "Failed: " followed by the system's error text."""
    downloads = source("DownloadsView.swift")
    assert "Failed: \\(record.error" not in downloads
    assert 'StatusText("Couldn\'t download", kind: .error)' in downloads


def test_compared_figures_use_rounded_tabular_numerals():
    """D-84: rounded tabular numerals where figures are compared (download
    sizes, playback times, the Watch's figures). The download sizes had
    neither; the player's times and three Watch figures set their own."""
    brand = (ROOT / "swift/PigKit/Sources/PigBrand/BrandViews.swift").read_text()
    assert (
        "func pigFigures() -> some View { fontDesign(.rounded).monospacedDigit() }"
        in brand
    )
    assert 'downloaded"\n        ).pigFigures()' in source("DownloadsView.swift")
    assert source("WatchDownloadsView.swift").count(".pigFigures()") == 2
    assert ".font(.caption).pigFigures()" in source("LibraryViews.swift")
    watch = (ROOT / "swift/PigMusicWatch/PigMusicWatchApp.swift").read_text()
    assert watch.count(".pigFigures()") == 4
    # One way to set figures: no hand-rolled rounded digits left.
    for text in [watch] + [p.read_text() for p in APP.glob("*.swift")]:
        assert ".fontDesign(.rounded).monospacedDigit()" not in text


def test_the_watch_mark_on_a_song_row_is_not_colour_alone():
    """Brand 1.8 section 16: information never by colour alone. The Apple
    Watch mark on a song row was the same glyph for ready, sending and
    failed, told apart only by colour."""
    row = source("Components.swift")
    symbols = row[row.index("static func watchSymbol") :]
    symbols = symbols[: symbols.index("\n  }\n")]
    names = re.findall(r'case \.(\w+): "([\w.]+)"', symbols)
    assert {state for state, _ in names} == {"ready", "pending", "failed"}
    assert len({symbol for _, symbol in names}) == 3
    assert "Image(systemName: Self.watchSymbol(watch))" in row
