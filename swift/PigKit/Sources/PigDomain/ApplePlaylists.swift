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
  /// The song as asked of the server (`/server/matches`): title, artist,
  /// album and length, so the server's shared policy keeps live, remix and
  /// demo recordings apart from the studio one.
  public var lookupTrack: Track {
    Track(
      id: "apple:\(appleId ?? position)", title: title, artist: artist, album: album,
      duration: duration)
  }
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
  /// The server's answer for one song (`/server/matches`): the person's
  /// copy, a household copy, or none.
  public init(match: Track?) {
    guard let match else {
      self = .elsewhere
      return
    }
    self = match.serverState == "household_available" ? .server(match) : .library(match)
  }
}

/// A playlist's song states for exactly the songs they were found for:
/// Apple may replace or reorder a playlist's songs, and a state is by place.
public struct ApplePlaylistStates: Codable, Sendable, Hashable {
  public let fingerprint: String
  public let states: [ApplePlaylistSongState]
  public init(songs: [ApplePlaylistSong], states: [ApplePlaylistSongState]) {
    self.fingerprint = Self.fingerprint(songs)
    self.states = states
  }
  /// The ordered Apple ids (title and artist for a song without one).
  public static func fingerprint(_ songs: [ApplePlaylistSong]) -> String {
    songs.map { song in
      song.appleId.map { String($0) } ?? "?" + song.title + "\u{1F}" + song.artist
    }.joined(separator: "\u{1E}")
  }
  /// The states when they belong to these songs, else nil.
  public func states(for songs: [ApplePlaylistSong]) -> [ApplePlaylistSongState]? {
    states.count == songs.count && fingerprint == Self.fingerprint(songs) ? states : nil
  }
}
