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
    assert "favouriteIndex.index(playlists)" in peek
    assert "index.contains(track)" in peek
    menu = app["PersonalActionsViews.swift"]
    unknown = menu.split("case .favouriteUnknown:", 1)[1].split("case .", 1)[0]
    assert 'Button("Favourite", systemImage: "star")' in unknown
    assert "orFavourite: true" in unknown


def test_a_sync_keeps_shown_favourite_states_current():
    """Codex round 1 on batch G (#230): a known state was never looked at
    again, so a song favourited on another device kept "Add to favourites"
    after the sync. Every song shown is followed, each library reload sets
    them from the synced Favourites playlist, and a tap still being saved or
    waiting to be sent keeps its state."""
    model = (APP / "AppModel.swift").read_text()
    reload = model.split("private func loadCache(", 1)[1].split("\n  }\n", 1)[0]
    # Codex round 2 on batch G: the index is kept per version of the
    # playlist, never rebuilt per lookup, and waiting edits are read once.
    assert (
        "favouriteIndex.index(playlists), waiting: FavouriteStates.waiting(" in reload
    )
    assert "FavouriteStates.listed(" not in model
    assert "private var favouriteIndex = FavouriteIndexCache()" in model
    for name in ("func peekFavourite(", "func loadFavourite("):
        body = model.split(name, 1)[1].split("\n  }\n", 1)[0]
        assert "favourites.follow(track)" in body, name
    # Codex round 2 on batch G: FavouriteStates holds a tap as saving until
    # its save ends; the player's star starts no read for it meanwhile.
    tap = model.split("func tapFavourite(", 1)[1].split("\n  }\n", 1)[0]
    assert "defer { favourites.settled(tap) }" in tap
    load = model.split("func loadFavourite(", 1)[1].split("\n  }\n", 1)[0]
    guard = load.index("guard !favourites.isSaving(track.id) else { return }")
    assert guard < load.index("readCatalogue(")


def test_add_to_playlist_offers_a_new_playlist_first():
    """#285: Add to playlist had no way to make a playlist. Song and
    collection menus (albums, artists, playlists, mixes) offer New playlist
    with the plus (D-116) first; the page under the menu asks for the name in
    the Playlists tab's words, and the songs go to the playlist it makes."""
    app = {p.name: p.read_text() for p in APP.glob("*.swift")}
    menus = app["PersonalActionsViews.swift"]
    pickers = menus.split('Menu("Add to playlist", systemImage: "text.badge.plus") {')[
        1:
    ]
    assert len(pickers) == 2
    for picker in pickers:
        body = picker.split("}.disabled(!model.canMutate)", 1)[0]
        new = body.index('Button("New playlist", systemImage: "plus")')
        assert new < body.index("ForEach(playlists)")
        assert "model.newPlaylist = NewPlaylistRequest(" in body
    prompt = menus.split("struct NewPlaylistPrompt: ViewModifier {", 1)[1]
    prompt = prompt.split("\n}\n", 1)[0]
    tools = app["PlaylistViews.swift"]
    for words in ('"New playlist"', '"Playlist name"', '"Create playlist"'):
        assert words in prompt and words in tools, words
    assert "model.createPlaylist(named: value, adding: request)" in prompt
    # The tabs ask unless the player (or Account) is open; the player asks then.
    shell = app["PigMusicApp.swift"]
    assert (
        "NewPlaylistPrompt(model: model, active: !showPlayer && !showAccount)" in shell
    )
    player = app["LibraryViews.swift"].split("struct NowPlayingView: View {", 1)[1]
    assert ".modifier(NewPlaylistPrompt(model: model))" in player.split("\n  }\n", 1)[0]
    model = app["AppModel.swift"].split("func createPlaylist(named", 1)[1]
    assert "NewPlaylistEdits.make(name: name, songs: songs)" in model
    # Codex round 1 on batch G: the playlist and its songs enter the outbox
    # in one transaction, never one saved edit at a time.
    create = model.split("\n  }\n", 1)[0]
    assert "saveEditsOutcome(NewPlaylistEdits.make(" in create
    assert "saveEdit(" not in create
    group = app["AppModel.swift"].split("func saveEditsOutcome(", 1)[1]
    assert "store.enqueueMutations(mutations)" in group.split("\n  }\n", 1)[0]
    # Codex round 2 on batch G: the name is checked with the server's rule
    # before anything is queued, on both New playlist paths; a refused create
    # gets no success tap; the outbox lets a refused create go with its songs
    # and says so once.
    assert create.index("PlaylistName.accepted(name) != nil") < create.index(
        "request.songs()"
    )
    assert "notify(PlaylistName.refusal)" in create
    assert "if outcome != .refused { confirmations += 1 }" in create
    tools = app["PlaylistViews.swift"].split("private func create() async {", 1)[1]
    tools = tools.split("\n  }\n", 1)[0]
    assert "guard let value = PlaylistName.accepted(name) else {" in tools
    flush = app["AppModel.swift"].split("func flushEdits()", 1)[1]
    flush = flush.split("\n  }\n", 1)[0]
    assert "EditOutbox.settleRefusedCreates(store: store)" in flush
    assert 'notify("Couldn\'t create \\(name). Try again.")' in flush


def test_searching_uses_the_shared_status_line():
    """#290: the brief "Searching…" was a labelled ProgressView, whose words
    are set in the body size, larger than the status lines beside it ("No
    matches", errors). It is now a spinner beside the shared StatusText."""
    search = (APP / "SearchViews.swift").read_text()
    assert 'ProgressView("Searching' not in search
    loading = search[search.index("if search.loading {") :]
    loading = loading[: loading.index("} else if let error = search.error")]
    assert 'StatusText("Searching…")' in loading
    assert "ProgressView()" in loading


def test_the_account_sheet_takes_a_new_appearance_at_once():
    """#291: switching light or dark in Preferences changed the app behind
    the Account sheet but not Preferences itself. The choice was applied
    only on the root view, and SwiftUI applies a preferred colour scheme to
    the nearest presentation; the Account sheet, where Preferences is
    pushed, is a presentation of its own. The sheet now carries it too."""
    app = (APP / "PigMusicApp.swift").read_text()
    library = (APP / "LibraryViews.swift").read_text()
    # Preferences lives in Account, which opens as a sheet.
    assert "PreferencesView(model: model)" in library
    shell = app[app.index("struct LibraryShell") :]
    account = shell[shell.index(".sheet(isPresented: $showAccount)") :]
    account = account[: account.index(".undoBarSheet(model)")]
    assert "AccountView(model: model)" in account
    assert ".pigAppearance(model.preferences)" in account
    root = app[app.index("struct RootView") : app.index("struct LibraryShell")]
    assert ".pigAppearance(model.preferences)" in root


def test_up_next_swipes_to_remove_in_the_player_with_no_edit():
    """#287: Up next had an Edit link to a Queue page with an Edit button.
    The player's own Up next is now the list: a song swipes to Remove at
    once with Undo (the menu's own path), Move earlier and Move later stay
    in its menu, and there is no Edit and no separate page."""
    app = {p.name: p.read_text() for p in APP.glob("*.swift")}
    library = app["LibraryViews.swift"]
    player = library.split("struct NowPlayingView: View {", 1)[1]
    player = player.split("\n}\n", 1)[0]
    assert 'NavigationLink("Edit")' not in player
    assert "QueueEditorView" not in "".join(app.values())
    assert [n for n, t in app.items() if "EditButton()" in t] == ["PlaylistViews.swift"]
    # One list owns the scrolling; no ScrollView holds a nested list.
    body = player.split("var body: some View {", 1)[1].split("\n  }\n", 1)[0]
    assert "List {" in body and "ScrollView" not in body
    rows = player.split("private func upNextRows(", 1)[1].split("\n  }\n", 1)[0]
    assert "UpNextRow(model: model, item: item)" in rows
    # Codex round 1 on batch G: every song of Up next and of the similar
    # songs is listed, as on the Queue page; none past the 50th is cut off.
    assert "ForEach(shown) { item in" in rows and "prefix(" not in rows
    assert "upNextRows(layout.chosen)" in player
    assert "upNextRows(layout.similar)" in player
    swipe = rows.split(".swipeActions {", 1)[1]
    assert 'Button("Remove", systemImage: "xmark", role: .destructive)' in swipe
    assert "model.removeFromUpNextLater(item)" in swipe
    for words in ('Text("Up next")', "similarTitle", "NothingNext(model: model"):
        assert words in player, words
    menu = library.split("private struct UpNextRow: View {", 1)[1]
    for item in (".moveEarlier", ".moveLater", ".removeFromUpNext"):
        assert item in menu.split("\n}\n", 1)[0], item


def test_a_playlists_add_says_what_happened_past_the_reload():
    """#260: the playlist page's Add missing songs wrote its result to the
    page's own line, which the reload after the Add cleared at once. The
    result goes to the app's notice bar, which the reload doesn't touch."""
    page = (APP / "PlaylistViews.swift").read_text()
    page = page.split("struct PlaylistView: View {", 1)[1]
    add = page.split("private func addMissing() async {", 1)[1].split("\n  }\n", 1)[0]
    assert "model.notifyDone(AcquisitionResult.playlistNotice(" in add
    assert "message" not in add
    assert "reloadAfterEdit = true" in add
    load = page.split("private func load() async {", 1)[1].split("\n  }\n", 1)[0]
    assert "notices" not in load


def test_a_playlists_own_add_belongs_to_the_account_that_chose_it():
    """#280: the collection menu's actions note the account and do nothing
    once it ended (batch D), but the playlist page's own Add to library ran
    unguarded, so songs read for one account could be added for the next."""
    menus = (APP / "PersonalActionsViews.swift").read_text()
    add = menus.split("case .addToLibrary:", 1)[1].split("case .addToPlaylist:", 1)[0]
    page = add.split("if let addMissing {", 1)[1].split("} else {", 1)[0]
    stamp = page.index("let stamp = model.currentGeneration")
    guard = page.index("guard model.isCurrent(stamp) else { return }")
    assert stamp < page.index("Task {") < guard < page.index("await addMissing()")


def test_a_memory_warning_lets_go_of_page_answers():
    """#253: iOS ended the app nine times in a day for memory pressure. The
    page answers kept in memory (up to 400, any size) never went; a memory
    warning now lets go of them and the cover colours, and says so in the
    diagnostics log. The day's memory figures go in the export."""
    app = (APP / "PigMusicApp.swift").read_text()
    assert "UIApplication.didReceiveMemoryWarningNotification" in app
    assert "{ _ in model.receivedMemoryWarning() }" in app
    assert "metrics.peakMemory = bytes(payload.memoryMetrics?.peakMemoryUsage)" in app
    assert "averageSuspendedMemory.averageMeasurement" in app
    model = (APP / "AppModel.swift").read_text()
    warning = model.split("func receivedMemoryWarning() {", 1)[1]
    warning = warning.split("\n  }\n", 1)[0]
    assert "catalogueMemory.clear()" in warning
    assert "coverSwatches = [:]" in warning
    assert 'DiagnosticsLog.shared.record("lifecycle"' in warning
