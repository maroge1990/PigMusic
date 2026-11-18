import Foundation

/// One item of a song's ⋯ pop-up menu (D-112, #229, D-118).
public enum SongMenuItem: Hashable, Sendable {
  case playNext, addToQueue
  /// On the server, not in this person's library: adds the household's copy.
  case addToLibrary
  /// A radio (`mix:`) song: "Add to library" asks the server for it.
  case requestMix
  /// Asked for already (#206): a mark, not an action.
  case requested
  /// Asks the server for the song, with Undo (#206).
  case request
  /// The favourite toggle; the song's state now (#212).
  case favourite(Bool)
  /// The favourite state isn't known on this iPhone yet: reads it.
  case loadFavourite
  /// A submenu of the person's playlists.
  case addToPlaylist
  /// A submenu: the original or a smaller AAC copy (D-106).
  case download
  /// Only while an Apple Watch is paired (D-118).
  case sendToWatch
  /// Opens the song's album page (D-118: new on iPhone).
  case goToAlbum
  /// Opens the song's artist page (D-118: new on iPhone).
  case goToArtist
  /// An item of the page the row is on (D-118).
  case page(SongPageItem)
  /// At once, with Undo (D-79, D-108).
  case removeFromLibrary
}

/// What the page a song row is on adds to its menu (D-118), after Go to
/// album and Go to artist and before Remove from library.
public enum SongPageItem: Hashable, Sendable {
  case removeFromPlaylist
  /// Up next under the player: Move earlier, Move later, Remove from Up next.
  case moveEarlier, moveLater
  case removeFromUpNext
}

/// What a song's ⋯ menu offers (D-112, #229) and in which order (D-118): the
/// logic of the old Song actions sheet, kept apart from the view so it can be
/// tested. Sections are drawn with dividers between them, in this order: the
/// queue; the song's library state, Favourite and Add to playlist; Download
/// and Send to Apple Watch; Go to album and Go to artist; the page's own
/// items; Remove from library.
public enum SongMenu {
  /// The song the menu acts on: the library's own copy when this iPhone's
  /// library holds it (#211), else the song as search's newest answer has it.
  public static func resolved(own: Track?, live: Track) -> Track {
    guard var own else { return live }
    own.serverState = "in_library"
    return own
  }

  /// The song's album page: the library album when the song names one (with
  /// its catalogue release to fall back to), else the catalogue release.
  public static func album(of track: Track) -> RewindLink? {
    let release = track.musicbrainzReleaseId.flatMap { UUID(uuidString: $0) == nil ? nil : $0 }
    if let id = track.albumId, !id.isEmpty { return .libraryAlbum(id: id, release: release) }
    return release.map { .release($0) }
  }

  /// The menu's sections, empty ones left out. `looked` is false until the
  /// library has been looked at for the song's own copy: until then neither
  /// Add to library nor the library's actions are offered, so Add to library
  /// never shows for a song the library holds (#211). `favourite` is nil
  /// while unknown; `watch` is whether an Apple Watch is paired; `page` is
  /// what the row's page adds.
  public static func sections(
    for track: Track, looked: Bool, requested: Bool, favourite: Bool?, playing: Bool,
    watch: Bool = true, page: [SongPageItem] = []
  ) -> [[SongMenuItem]] {
    var sections: [[SongMenuItem]] = []
    if playing { sections.append([.playNext, .addToQueue]) }
    var mine: [SongMenuItem] = []
    if looked, let state = libraryState(track, requested: requested) { mine.append(state) }
    mine.append(favourite.map(SongMenuItem.favourite) ?? .loadFavourite)
    mine.append(.addToPlaylist)
    sections.append(mine)
    let yours = looked && (track.serverState == "in_library" || track.serverState == nil)
    if yours { sections.append(watch ? [.download, .sendToWatch] : [.download]) }
    var places: [SongMenuItem] = []
    if album(of: track) != nil { places.append(.goToAlbum) }
    if !track.artist.isEmpty { places.append(.goToArtist) }
    if !places.isEmpty { sections.append(places) }
    if !page.isEmpty { sections.append(page.map(SongMenuItem.page)) }
    if yours { sections.append([.removeFromLibrary]) }
    return sections
  }

  private static func libraryState(_ track: Track, requested: Bool) -> SongMenuItem? {
    let state = track.serverState
    if state == "household_available" { return .addToLibrary }
    if state != "in_library", state == "requested" || requested { return .requested }
    if state == "not_in_library" { return track.id.hasPrefix("mix:") ? .requestMix : .request }
    return nil
  }
}

/// The kind of collection a collection menu acts on (D-118).
public enum CollectionKind: Hashable, Sendable {
  case album, artist, playlist, mix
}

/// One item of the collection menu albums, artists, playlists and mixes
/// share (D-118, #249).
public enum CollectionMenuItem: Hashable, Sendable {
  /// A mix card's way to its page: a tap on the card plays it.
  case showSongs
  case play, shuffle, playNext
  /// "Add N songs to library": the songs not yet in your library.
  case addToLibrary
  case addToPlaylist
  /// A submenu: the original or a smaller AAC copy (D-106).
  case download
  /// Only while an Apple Watch is paired (D-118).
  case sendToWatch
  /// Mixes only: they live nowhere else (D-118).
  case saveMix
  /// Mixes only, at once with Undo (D-79).
  case hideMix
}

/// What the one collection menu offers (D-118): Play, Shuffle, Play next;
/// Add to library (when some songs aren't yours), Add to playlist; Download,
/// Send to Apple Watch; and for a mix, Save and Hide this edition.
public enum CollectionMenu {
  /// The songs not in your library: on the server for someone else, or not
  /// on it yet. A song without a state (a library page's) is yours.
  public static func missing(_ tracks: [Track]) -> [Track] {
    tracks.filter { $0.serverState == "household_available" || $0.serverState == "not_in_library" }
  }

  /// `missing` is how many of its songs aren't in your library; `watch`
  /// whether an Apple Watch is paired; `opens` adds the mix card's Show songs.
  public static func sections(
    _ kind: CollectionKind, missing: Int, watch: Bool, opens: Bool = false
  ) -> [[CollectionMenuItem]] {
    var sections: [[CollectionMenuItem]] = []
    if opens { sections.append([.showSongs]) }
    sections.append([.play, .shuffle, .playNext])
    sections.append(missing > 0 ? [.addToLibrary, .addToPlaylist] : [.addToPlaylist])
    sections.append(watch ? [.download, .sendToWatch] : [.download])
    if kind == .mix { sections.append([.saveMix, .hideMix]) }
    return sections
  }
}
