"""#215 (D-101): helper text Mark marked unnecessary is gone from the apps,
and the lines that stay are short (Swift sources; screens on the Mac show
the result)."""

from pathlib import Path

import pytest

SWIFT = Path(__file__).resolve().parents[1] / "swift"

pytestmark = pytest.mark.skipif(
    not (SWIFT / "PigMusic").exists(),
    reason="native app sources are not in this image",
)

REMOVED = {
    "PigMusic/PigMusicApp.swift": [
        "Your music, wherever you are.",
        "Use the address from your invitation",
        "Use the password above for your new account",
    ],
    "PigMusic/HomeView.swift": [
        "Endless radio from your library",
        "Add songs to your library to keep their audio",
    ],
    "PigMusic/AccountToolsViews.swift": [
        "similar songs carry on",
        "Shared files keep the highest requested quality",
        "This server is older than the app",
    ],
    "PigMusic/PersonalActionsViews.swift": ["Favourites do not download music"],
    "PigMusic/PlaylistViews.swift": [
        "Import personal playlists and matching metadata",
        "Review the selected export before importing",
    ],
    "PigMusic/PendingEditsView.swift": ["Changes are kept on this iPhone"],
    "PigMusic/SearchViews.swift": [
        "Songs, albums, artists and playlists, wherever they are.",
        "Hold your iPhone near the music.",
        "Which songs are on the server isn't known yet.",
    ],
    "PigMusic/LibraryViews.swift": ["Find songs and albums to add."],
    "PigMusic/Components.swift": ["Shared files keep the highest quality"],
    "PigMusic/ArtistAcquisitionView.swift": ["This can create many server downloads"],
    "PigMusicShare/ShareViewController.swift": [
        "Open PigMusic to see whether your server has it"
    ],
    "PigMusicWatch/PigMusicWatchApp.swift": [
        "Keep iPhone nearby",
        "Phone reachable",
        "Phone unavailable",
        "Albums play gapless",
    ],
    # D-122 (#292): the Watch is not only for running.
    "PigKit/Sources/PigCompanion/WatchStatus.swift": ["Ready for your run"],
    "PigMusicUITests/Watch/WatchDemoTests.swift": ["Ready for your run"],
}

KEPT = {
    "PigMusic/PigMusicApp.swift": ['Text("At least 12 characters")'],
    "PigMusic/AppModel.swift": ['notifyDone("Playlist saved")'],
    "PigMusic/AccountToolsViews.swift": [
        'static let serverUpdate = "Update the server to use everything"',
    ],
    "PigMusic/Components.swift": ['message = .info("Saved")'],
    "PigMusic/ArtistAcquisitionView.swift": [
        'Text("This can add many albums at once.")'
    ],
    "PigMusic/PendingEditsView.swift": ['"Discard this change?"'],
    "PigMusic/SearchViews.swift": ["Listening…"],
    "PigKit/Sources/PigDomain/MainScreens.swift": [
        'case .library: "Your library is empty"',
        'case .library: "Find music"',
    ],
    "PigMusicShare/ShareViewController.swift": ["Added to PigMusic's Shazamed list"],
    "PigKit/Sources/PigCompanion/WatchStatus.swift": ['case .ready: "Ready to play"'],
}


def test_unneeded_helper_text_is_gone():
    left = [
        (name, text)
        for name, texts in REMOVED.items()
        for text in texts
        if text in (SWIFT / name).read_text()
    ]
    assert left == []


def test_short_wording_and_kept_lines():
    missing = [
        (name, text)
        for name, texts in KEPT.items()
        for text in texts
        if text not in (SWIFT / name).read_text()
    ]
    assert missing == []
