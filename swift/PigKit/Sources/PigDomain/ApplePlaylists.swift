import Foundation

/// An Apple Music catalogue playlist found by the phone's own MusicKit search
/// (D-66): the server never sees it.
public struct ApplePlaylist: Codable, Sendable, Hashable, Identifiable {
  /// Apple's catalogue id ("pl.…").
  public let id: String
  public let name: String
  public let curator: String?
  public let artworkURL: URL?
  public init(id: String, name: String, curator: String? = nil, artworkURL: URL? = nil) {
    self.id = id
    self.name = name
    self.curator = curator
    self.artworkURL = artworkURL
  }
}

/// One song of an Apple Music playlist, as Apple lists it.
public struct ApplePlaylistSong: Codable, Sendable, Hashable, Identifiable {
  /// The song's place in the playlist: a playlist may hold a song twice.
  public let position: Int
  /// Apple's catalogue id, the one the server resolves (`/search/resolve`).
  public let appleId: Int?
  public let title: String
  public let artist: String
  public let album: String?
  public let duration: Double?
  public let artworkURL: URL?
  public var id: Int { position }
  public init(
    position: Int, appleId: Int?, title: String, artist: String, album: String? = nil,
    duration: Double? = nil, artworkURL: URL? = nil
  ) {
    self.position = position
    self.appleId = appleId
    self.title = title
    self.artist = artist
    self.album = album
    self.duration = duration
    self.artworkURL = artworkURL
  }
  /// The song as asked of the person's library (`/library/matches`).
  public var lookupTrack: Track {
    Track(
      id: "apple:\(appleId ?? position)", title: title, artist: artist, album: album,
      duration: duration)
  }
  /// The words the server's ranked search is asked for this song.
  public var serverQuery: String { SearchText.clean(title + " " + artist) }
}

/// Where one Apple playlist song is: in the person's library, elsewhere on
/// the server, or not on the server yet (requestable from its catalogue page).
public enum ApplePlaylistSongState: Codable, Sendable, Hashable {
  case library(Track)
  case server(Track)
  case elsewhere
  public var track: Track? {
    switch self {
    case .library(let track), .server(let track): track
    case .elsewhere: nil
    }
  }
}

public enum ApplePlaylistMatching {
  /// The library's own match wins; otherwise a server song with the same
  /// edition-free title and lead artist. Live, remix and demo words stay in
  /// the title, so such a recording never stands in for the studio one.
  public static func state(
    for song: ApplePlaylistSong, libraryMatch: Track?, serverResults: FoundResults?
  ) -> ApplePlaylistSongState {
    if let libraryMatch { return .library(libraryMatch) }
    let title = titleKey(song.title)
    let artists = artistKeys(song.artist)
    guard
      let found = serverResults?.songs.first(where: {
        titleKey($0.title) == title && !artists.isDisjoint(with: artistKeys($0.artist))
      })
    else { return .elsewhere }
    switch found.serverState {
    case nil, "in_library": return .library(found)
    case "household_available": return .server(found)
    default: return .elsewhere
    }
  }

  /// Folded, punctuation-free words, without featured artists or remaster
  /// notes (the same recording on another edition).
  public static func titleKey(_ title: String) -> String {
    var text = title
    // "Song (feat. X)", "Song [Remastered 2011]", "Song - Remastered 2009".
    let segments = #"\s*[\(\[]((feat|ft|featuring|with)\b|[^\)\]]*remaster)[^\)\]]*[\)\]]"#
    text = text.replacingOccurrences(
      of: segments, with: "", options: [.regularExpression, .caseInsensitive])
    text = text.replacingOccurrences(
      of: #"\s+-\s+[^-]*remaster[^-]*$"#, with: "", options: [.regularExpression, .caseInsensitive])
    return words(text)
  }

  /// The whole credit and its lead artist ("A & B" → "a b", "a").
  public static func artistKeys(_ artist: String) -> Set<String> {
    let lead = artist.replacingOccurrences(
      of: #"\s*(,|&|\b(feat|ft|featuring|with)\b).*$"#, with: "",
      options: [.regularExpression, .caseInsensitive])
    return Set([words(artist), words(lead)].filter { !$0.isEmpty })
  }

  private static func words(_ text: String) -> String {
    let folded = text.folding(options: [.caseInsensitive, .diacriticInsensitive], locale: nil)
    return folded.components(separatedBy: CharacterSet.alphanumerics.inverted)
      .filter { !$0.isEmpty }.joined(separator: " ")
  }
}
