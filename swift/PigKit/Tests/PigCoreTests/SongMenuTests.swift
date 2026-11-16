import PigDomain
import Testing

/// D-112 (#229): a song's ⋯ opens a pop-up menu, not a sheet; it keeps the
/// sheet's rules for which actions a song offers.
struct SongMenuTests {
  private func song(_ state: String?, id: String = "s1") -> Track {
    var track = Track(id: id, title: "Song", artist: "Artist")
    track.serverState = state
    return track
  }

  @Test func aLibrarySongOffersEveryActionInOrderWhilePlaying() {
    let sections = SongMenu.sections(
      for: song("in_library"), looked: true, requested: false, favourite: true, playing: true)
    #expect(
      sections == [
        [.playNext, .addToQueue],
        [.addToPlaylist, .favourite(true)],
        [.download, .sendToWatch],
        [.removeFromLibrary],
      ])
  }

  @Test func theQueueIsOfferedOnlyWhileSomethingPlays() {
    let sections = SongMenu.sections(
      for: song("in_library"), looked: true, requested: false, favourite: false, playing: false)
    #expect(sections.first == [.addToPlaylist, .favourite(false)])
    #expect(!sections.joined().contains(.playNext))
    #expect(!sections.joined().contains(.addToQueue))
  }

  @Test func anUnknownFavouriteOffersToLoadIt() {
    let sections = SongMenu.sections(
      for: song("in_library"), looked: true, requested: false, favourite: nil, playing: false)
    #expect(sections.first == [.addToPlaylist, .loadFavourite])
  }

  /// #211: until the library has been looked at, neither Request nor the
  /// library's own actions show, so Request never flashes for a held song.
  @Test func nothingOfTheLibraryShowsUntilItHasBeenLookedAt() {
    for state in ["in_library", "not_in_library", "household_available", "requested", nil] {
      let sections = SongMenu.sections(
        for: song(state), looked: false, requested: false, favourite: false, playing: true)
      #expect(sections == [[.playNext, .addToQueue], [.addToPlaylist, .favourite(false)]])
    }
  }

  @Test func aSongNotInTheLibraryCanBeRequestedAndNothingElse() {
    let sections = SongMenu.sections(
      for: song("not_in_library"), looked: true, requested: false, favourite: false,
      playing: false)
    #expect(sections == [[.addToPlaylist, .favourite(false)], [.request]])
  }

  /// #206: asked for here, or on the server, shows Requested instead.
  @Test func aRequestedSongShowsRequested() {
    let asked = SongMenu.sections(
      for: song("not_in_library"), looked: true, requested: true, favourite: false,
      playing: false)
    #expect(asked.last == [.requested])
    let server = SongMenu.sections(
      for: song("requested"), looked: true, requested: false, favourite: false, playing: false)
    #expect(server.last == [.requested])
  }

  @Test func aHouseholdSongIsAddedToTheLibrary() {
    let sections = SongMenu.sections(
      for: song("household_available"), looked: true, requested: true, favourite: false,
      playing: false)
    #expect(sections.last == [.addToLibrary])
    #expect(!sections.joined().contains(.removeFromLibrary))
  }

  @Test func aRadioSongIsAddedByRequestingIt() {
    let sections = SongMenu.sections(
      for: song("not_in_library", id: "mix:abc"), looked: true, requested: false,
      favourite: false, playing: false)
    #expect(sections.last == [.requestMix])
  }

  /// #211: the library's own copy is acted on, so a catalogue row whose song
  /// the library holds offers the library's actions, never Request.
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
    #expect(sections.suffix(2) == [[.download, .sendToWatch], [.removeFromLibrary]])
    let live = song("not_in_library", id: "catalogue:x")
    #expect(SongMenu.resolved(own: nil, live: live) == live)
  }

  /// A song with no state (a playlist's own song) is treated as the library's.
  @Test func aSongWithoutAStateHasTheLibrarysActions() {
    let sections = SongMenu.sections(
      for: song(nil), looked: true, requested: false, favourite: false, playing: false)
    #expect(sections.suffix(2) == [[.download, .sendToWatch], [.removeFromLibrary]])
  }
}
