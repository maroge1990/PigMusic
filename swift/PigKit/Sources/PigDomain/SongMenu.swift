import Foundation

/// One item of a song's ⋯ pop-up menu (D-112, #229).
public enum SongMenuItem: Hashable, Sendable {
  case playNext, addToQueue
  /// A submenu of the person's playlists.
  case addToPlaylist
  /// The favourite toggle; the song's state now (#212).
  case favourite(Bool)
  /// The favourite state isn't known on this iPhone yet: reads it.
  case loadFavourite
  /// On the server, not in this person's library: adds the household's copy.
  case addToLibrary
  /// A radio (`mix:`) song: "Add to my library" asks the server for it.
  case requestMix
  /// Asked for already (#206): a mark, not an action.
  case requested
  /// Asks the server for the song, with Undo (#206).
  case request
  /// A submenu: the original or a smaller AAC copy (D-106).
  case download
  case sendToWatch
  /// At once, with Undo (D-79, D-108).
  case removeFromLibrary
}

/// What a song's ⋯ menu offers (D-112, #229): the logic of the old Song
/// actions sheet, kept apart from the view so it can be tested. Sections are
/// drawn with dividers between them, in this order: the queue, playlist and
/// favourite, the song's library state, the library's own actions, removal.
public enum SongMenu {
  /// The song the menu acts on: the library's own copy when this iPhone's
  /// library holds it (#211), else the song as search's newest answer has it.
  public static func resolved(own: Track?, live: Track) -> Track {
    guard var own else { return live }
    own.serverState = "in_library"
    return own
  }

  /// The menu's sections, empty ones left out. `looked` is false until the
  /// library has been looked at for the song's own copy: until then neither
  /// Request nor the library's actions are offered, so Request never shows
  /// for a song the library holds (#211). `favourite` is nil while unknown.
  public static func sections(
    for track: Track, looked: Bool, requested: Bool, favourite: Bool?, playing: Bool
  ) -> [[SongMenuItem]] {
    var sections: [[SongMenuItem]] = []
    if playing { sections.append([.playNext, .addToQueue]) }
    sections.append([.addToPlaylist, favourite.map(SongMenuItem.favourite) ?? .loadFavourite])
    guard looked else { return sections }
    if let state = libraryState(track, requested: requested) { sections.append([state]) }
    if track.serverState == "in_library" || track.serverState == nil {
      sections.append([.download, .sendToWatch])
      sections.append([.removeFromLibrary])
    }
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
