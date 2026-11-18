import PigDomain
import Testing

/// D-112 (#229): a song's ⋯ opens a pop-up menu, not a sheet; it keeps the
/// sheet's rules for which actions a song offers. D-118 (#249): one order on
/// every page.
struct SongMenuTests {
  private func song(_ state: String?, id: String = "s1") -> Track {
    var track = Track(id: id, title: "Song", artist: "Artist")
    track.serverState = state
    return track
  }

  /// D-118: Play next, Add to queue; Favourite, Add to playlist; Download,
  /// Send to Apple Watch; Go to album, Go to artist; Remove from library.
  @Test func aLibrarySongOffersEveryActionInTheAgreedOrderWhilePlaying() {
    var track = song("in_library")
    track.albumId = "album-1"
    let sections = SongMenu.sections(
      for: track, looked: true, requested: false, favourite: true, playing: true)
    #expect(
      sections == [
        [.playNext, .addToQueue],
        [.favourite(true), .addToPlaylist],
        [.download, .sendToWatch],
        [.goToAlbum, .goToArtist],
        [.removeFromLibrary],
      ])
  }

  /// D-118: the page's own items come after Go to album and Go to artist and
  /// before Remove from library.
  @Test func thePagesOwnItemsComeBeforeRemoveFromLibrary() {
    var track = song("in_library")
    track.albumId = "album-1"
    let sections = SongMenu.sections(
      for: track, looked: true, requested: false, favourite: false, playing: false,
      page: [.removeFromPlaylist])
    #expect(
      Array(sections.suffix(3)) == [
        [.goToAlbum, .goToArtist], [.page(.removeFromPlaylist)], [.removeFromLibrary],
      ])
    let queue = SongMenu.sections(
      for: track, looked: true, requested: false, favourite: false, playing: true,
      page: [.moveEarlier, .moveLater, .removeFromUpNext])
    #expect(
      Array(queue.suffix(2)) == [
        [.page(.moveEarlier), .page(.moveLater), .page(.removeFromUpNext)], [.removeFromLibrary],
      ])
  }

  /// D-118: Send to Apple Watch shows only while an Apple Watch is paired.
  @Test func sendToAppleWatchNeedsAPairedWatch() {
    let sections = SongMenu.sections(
      for: song("in_library"), looked: true, requested: false, favourite: false, playing: false,
      watch: false)
    #expect(sections.joined().contains(.download))
    #expect(!sections.joined().contains(.sendToWatch))
  }

  /// D-118: Add to library (when it isn't yours) leads the second section,
  /// before Favourite and Add to playlist.
  @Test func addToLibraryLeadsFavouriteAndAddToPlaylist() {
    let sections = SongMenu.sections(
      for: song("household_available"), looked: true, requested: false, favourite: false,
      playing: true)
    #expect(sections[1] == [.addToLibrary, .favourite(false), .addToPlaylist])
  }

  /// Go to album opens the library album, else the catalogue album; a song
  /// naming neither has only Go to artist.
  @Test func goToAlbumNeedsAnAlbumToOpen() {
    var track = song("not_in_library", id: "catalogue:x")
    #expect(SongMenu.album(of: track) == nil)
    track.musicbrainzReleaseId = "not-a-uuid"
    #expect(SongMenu.album(of: track) == nil)
    let release = "0b6a2a9e-5b8e-4a59-8f3b-2a1d6a1c9e11"
    track.musicbrainzReleaseId = release
    #expect(SongMenu.album(of: track) == .release(release))
    track.albumId = "album-1"
    #expect(SongMenu.album(of: track) == .libraryAlbum(id: "album-1", release: release))
    let none = SongMenu.sections(
      for: song("in_library"), looked: true, requested: false, favourite: false, playing: false)
    #expect(none.contains([.goToArtist]))
    #expect(!none.joined().contains(.goToAlbum))
  }

  @Test func theQueueIsOfferedOnlyWhileSomethingPlays() {
    let sections = SongMenu.sections(
      for: song("in_library"), looked: true, requested: false, favourite: false, playing: false)
    #expect(sections.first == [.favourite(false), .addToPlaylist])
    #expect(!sections.joined().contains(.playNext))
    #expect(!sections.joined().contains(.addToQueue))
  }

  @Test func anUnknownFavouriteOffersToLoadIt() {
    let sections = SongMenu.sections(
      for: song("in_library"), looked: true, requested: false, favourite: nil, playing: false)
    #expect(sections.first == [.loadFavourite, .addToPlaylist])
  }

  /// #211: until the library has been looked at, neither Add to library nor
  /// the library's own actions show, so it never flashes for a held song.
  @Test func nothingOfTheLibraryShowsUntilItHasBeenLookedAt() {
    for state in ["in_library", "not_in_library", "household_available", "requested", nil] {
      let sections = SongMenu.sections(
        for: song(state), looked: false, requested: false, favourite: false, playing: true)
      #expect(
        sections == [[.playNext, .addToQueue], [.favourite(false), .addToPlaylist], [.goToArtist]])
    }
  }

  @Test func aSongNotInTheLibraryCanBeAddedAndNotDownloaded() {
    let sections = SongMenu.sections(
      for: song("not_in_library"), looked: true, requested: false, favourite: false,
      playing: false)
    #expect(sections == [[.request, .favourite(false), .addToPlaylist], [.goToArtist]])
  }

  /// #206: asked for here, or on the server, shows Adding instead.
  @Test func aRequestedSongShowsRequested() {
    let asked = SongMenu.sections(
      for: song("not_in_library"), looked: true, requested: true, favourite: false,
      playing: false)
    #expect(asked.first?.first == .requested)
    let server = SongMenu.sections(
      for: song("requested"), looked: true, requested: false, favourite: false, playing: false)
    #expect(server.first?.first == .requested)
  }

  @Test func aHouseholdSongIsAddedToTheLibrary() {
    let sections = SongMenu.sections(
      for: song("household_available"), looked: true, requested: true, favourite: false,
      playing: false)
    #expect(sections.first?.first == .addToLibrary)
    #expect(!sections.joined().contains(.removeFromLibrary))
  }

  @Test func aRadioSongIsAddedByRequestingIt() {
    let sections = SongMenu.sections(
      for: song("not_in_library", id: "mix:abc"), looked: true, requested: false,
      favourite: false, playing: false)
    #expect(sections.first?.first == .requestMix)
  }

  /// #211: the library's own copy is acted on, so a catalogue row whose song
  /// the library holds offers the library's actions, never Add to library.
  @Test func theLibrarysOwnCopyIsActedOn() {
    var own = song(nil, id: "library-id")
    own.serverState = "household_available"
    let resolved = SongMenu.resolved(own: own, live: song("not_in_library", id: "catalogue:x"))
    #expect(resolved.id == "library-id")
    #expect(resolved.serverState == "in_library")
    let sections = SongMenu.sections(
      for: resolved, looked: true, requested: true, favourite: false, playing: false)
    #expect(!sections.joined().contains(.request))
    #expect(!sections.joined().contains(.requested))
    #expect(sections.contains([.download, .sendToWatch]))
    #expect(sections.last == [.removeFromLibrary])
    let live = song("not_in_library", id: "catalogue:x")
    #expect(SongMenu.resolved(own: nil, live: live) == live)
  }

  /// A song with no state (a playlist's own song) is treated as the library's.
  @Test func aSongWithoutAStateHasTheLibrarysActions() {
    let sections = SongMenu.sections(
      for: song(nil), looked: true, requested: false, favourite: false, playing: false)
    #expect(sections.contains([.download, .sendToWatch]))
    #expect(sections.last == [.removeFromLibrary])
  }
}

/// D-118 (#249): albums, artists, playlists and mixes share one menu.
struct CollectionMenuTests {
  @Test func anAlbumOffersTheSharedItemsInOrder() {
    #expect(
      CollectionMenu.sections(.album, missing: 0, watch: true) == [
        [.play, .shuffle, .playNext], [.addToPlaylist], [.download, .sendToWatch],
      ])
  }

  @Test func songsNotInYourLibraryCanBeAdded() {
    let sections = CollectionMenu.sections(.playlist, missing: 3, watch: true)
    #expect(sections[1] == [.addToLibrary, .addToPlaylist])
    let states: [String?] = ["in_library", nil, "household_available", "not_in_library", "requested"]
    let tracks = states.enumerated().map { index, state in
      var track = Track(id: "s\(index)", title: "Song", artist: "Artist")
      track.serverState = state
      return track
    }
    #expect(CollectionMenu.missing(tracks).map(\.id) == ["s2", "s3"])
  }

  @Test func sendToAppleWatchNeedsAPairedWatch() {
    let sections = CollectionMenu.sections(.artist, missing: 0, watch: false)
    #expect(sections.last == [.download])
  }

  /// Save and Hide this edition live only in a mix's menu, which the card's
  /// long press and the page's ⋯ both open.
  @Test func onlyAMixCanBeSavedOrHidden() {
    for kind in [CollectionKind.album, .artist, .playlist] {
      let items = CollectionMenu.sections(kind, missing: 0, watch: true).joined()
      #expect(!items.contains(.saveMix))
      #expect(!items.contains(.hideMix))
    }
    let card = CollectionMenu.sections(.mix, missing: 0, watch: true, opens: true)
    #expect(card.first == [.showSongs])
    #expect(card.last == [.saveMix, .hideMix])
    let page = CollectionMenu.sections(.mix, missing: 0, watch: true)
    #expect(!page.joined().contains(.showSongs))
    #expect(page.last == [.saveMix, .hideMix])
  }
}
