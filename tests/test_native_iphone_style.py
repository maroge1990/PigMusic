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
        "Text(error).font(PigType.footnote).foregroundStyle(PigBrand.danger(scheme))"
        in watch
    )


def test_one_paging_label():
    """#159: "Show more…", "More … results" and "Load more …"."""
    labels = re.findall(
        r'Button\("((?:Show|Load) more[^"]*|More [^"]*results)"\)',
        "".join(sources().values()),
    )
    assert labels and all(label.startswith("Show more") for label in labels), labels


def test_whole_screen_empties_use_one_quiet_state():
    """#159, then D-82: one kind of empty screen, now the pig's quiet state."""
    assert (
        'PigQuietState(\n          "No downloads yet"'
        in sources()["DownloadsView.swift"]
    )


def test_one_section_heading_card_title_and_page_spacing():
    """#160: headline for some section headings, a headline-sized mix card
    beside semibold card titles, page spacing 14 on five pages and 16 on two."""
    app = sources()
    assert found(".font(.title3.weight(.bold))") == []
    assert 'Text("Settings").font(PigType.sectionTitle)' in app["Components.swift"]
    assert 'Text("Up next").font(PigType.sectionTitle)' in app["LibraryViews.swift"]
    home = app["HomeView.swift"]
    assert ".font(.headline)" not in home
    assert home.count("PigType.cardTitle") == 2
    assert found("VStack(alignment: .leading, spacing: 14)") == []
    assert found("VStack(alignment: .leading, spacing: 16)") == []


def test_row_subtitles_and_raw_fonts_use_the_type_roles():
    """#160: caption subtitles on some rows; raw .footnote/.caption fonts."""
    for name in ("PendingEditsView.swift", "ArtistAcquisitionView.swift"):
        text = sources()[name]
        assert ".font(.footnote)" not in text and ".font(.caption)" not in text
    watch = (ROOT / "swift/PigMusicWatch/PigMusicWatchApp.swift").read_text()
    assert ".font(.footnote)" not in watch and ".font(.caption)" not in watch
    for name in ("DownloadsView.swift", "WatchDownloadsView.swift"):
        assert "PigType.caption).foregroundStyle(.secondary)" not in sources()[name]


def test_the_artist_hero_respects_reduce_transparency():
    """#162: the blurred portrait, the photo's fade and the translucent link
    tile stayed under Reduce Transparency."""
    hero = sources()["CatalogueViews.swift"]
    assert hero.count("accessibilityReduceTransparency") >= 2
    assert "} else if let portrait, !reduceTransparency {" in hero
    assert "reduceMotion || reduceTransparency ? 0" in hero


def test_fixed_sizes_follow_dynamic_type():
    """#162: no @ScaledMetric anywhere; fixed frames and glyph sizes."""
    app = sources()
    for raw in (
        ".frame(width: 26)",
        ".frame(width: 32, height: 44)",
        ".frame(width: 34, height: 34)",
        "font(.system(size: 16, weight: .semibold))",
        "font(.system(size: 13, weight: .semibold))",
        ".font(.system(size: 64))",
    ):
        assert found(raw) == [], raw
    assert sum(text.count("@ScaledMetric") for text in app.values()) >= 8


ROOT_TITLES = {
    '"Home"',
    '"Search"',
    '"Your library"',
    '"Downloads"',
    '"Account"',  # the account sheet's root
    '"PigMusic"',  # sign-in
    'result == nil ? "Add to my playlists" : "Added"',  # a sheet's root
}


def test_pushed_pages_have_inline_titles_and_one_toolbar_rule():
    """#158: .inline on 15 screens, the default on 24; the artist hero
    re-enabled the toolbar background PigCanvas hides everywhere else."""
    large = []
    for name, text in sources().items():
        for m in re.finditer(
            r"\.navigationTitle\(([^\n]*?)\)(\.navigationBarTitleDisplayMode)?", text
        ):
            if not m.group(2) and m.group(1) not in ROOT_TITLES:
                large.append(f"{name}: {m.group(1)}")
    assert large == []
    assert found(".toolbarBackground(") == []


def test_playlist_and_mix_pages_use_the_detail_layout():
    """#158: inset-grouped cells, Section("Settings") and a hand-made header."""
    playlist = sources()["PlaylistViews.swift"]
    mix = sources()["HomeView.swift"]
    assert 'Section("Settings")' not in playlist and "DetailSettings {" in playlist
    assert playlist.count(".listStyle(.plain)") == 1
    assert 'eyebrow: "MIX"' in mix and "Text(mix.title).font(.title2.bold())" not in mix
    assert mix.count(".listStyle(.plain)") == 1


def test_collection_rows_share_one_component_thumb_and_radius():
    """#157: inline rows with 32/40/44/48/52 pt thumbs, radii 6, 10 and 12, the
    queue listed twice with different rows, and a SongButton alias."""
    app = sources()
    assert "struct CollectionRow" in app["Components.swift"]
    assert found("SongButton") == []
    assert re.findall(r"ArtworkThumb\([^)]*size:", "".join(app.values())) == []
    assert re.findall(r"cornerRadius: (?:6|10|12)\)", "".join(app.values())) == []
    for name in (
        "SearchViews.swift",
        "LibraryViews.swift",
        "CatalogueViews.swift",
        "PersonalActionsViews.swift",
        "DownloadsView.swift",
        "WatchDownloadsView.swift",
    ):
        assert "CollectionRow(" in app[name], name
    editor = app["LibraryViews.swift"].split("struct QueueEditorView", 1)[1]
    assert "UpNextRow(model: model, item: item)" in editor.split("\n}\n", 1)[0]
    assert '"Remove", role: .destructive' not in app["LibraryViews.swift"]


def test_a_status_line_knows_whether_it_reports_a_failure():
    """Codex round 2 on #159: one String slot held successes and failures, so
    failures showed as notices and a successful cancellation as an error."""
    for name in (
        "AccountToolsViews.swift",
        "ArtistAcquisitionView.swift",
        "Components.swift",
        "HomeView.swift",
        "PersonalActionsViews.swift",
        "PlaylistViews.swift",
        "SearchViews.swift",
    ):
        text = sources()[name]
        assert "@State private var message: String?" not in text, name
        assert "@State private var notice: String?" not in text or name == (
            "PersonalActionsViews.swift"
        ), name
        # A failure is never stored as a notice.
        assert not re.search(r"catch \{ (message|notice) = \.info", text), name
        assert not re.search(r"\.info\([^)]*userMessage", text), name
    requests = sources()["PersonalActionsViews.swift"]
    assert 'self.error =\n          "Your request was cancelled' not in requests


def test_now_playing_controls_wrap_at_large_text_sizes():
    """Codex round 2 on #162: five scaled round buttons in one row ran off
    the screen at accessibility sizes."""
    player = sources()["LibraryViews.swift"].split("private var options: some View", 1)[
        1
    ]
    assert player.lstrip().startswith("{\n    ViewThatFits(in: .horizontal)")


def test_a_refused_or_failed_watch_send_is_an_error():
    """Codex confirmation on #159: every Send to Apple Watch outcome was a
    notice, failures included."""
    watch = sources()["WatchPhoneController.swift"]
    send = watch.split("func enqueue(", 1)[1].split("  func remove(", 1)[0]
    assert send.lstrip().startswith(
        "_ tracks: [Track], origin: DownloadOrigin = .direct) async\n    -> StatusMessage?"
    )
    assert send.count("return message.map(StatusMessage.error)") == 5
    assert "StatusMessage.info)" not in sources()["PersonalActionsViews.swift"]
