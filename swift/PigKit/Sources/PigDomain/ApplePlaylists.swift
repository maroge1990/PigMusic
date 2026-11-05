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

/// One song of an Apple playlist as the import sends it (#121).
public struct AppleImportSong: Codable, Sendable, Hashable {
  public let title: String
  public let artist: String
  public let album: String?
  public let duration: Double
  public let appleId: String?
  public init(title: String, artist: String, album: String?, duration: Double, appleId: String?) {
    self.title = title
    self.artist = artist
    self.album = album
    self.duration = duration
    self.appleId = appleId
  }
}

/// "Add to my playlists" for an Apple playlist (#121, D-69): the playlist as
/// shown, in Apple's order, to become the person's own exact copy.
public struct AppleImportRequest: Sendable, Hashable {
  public let applePlaylistId: String
  public let name: String
  public let curator: String?
  public let songs: [AppleImportSong]
  public let downloadMissing: Bool
  /// The playlist had more songs than one press adds: only the first
  /// `AppleImport.limit` were sent.
  public let capped: Bool
}

/// What the server made of an added Apple playlist.
public struct AppleImportCounts: Codable, Sendable, Hashable {
  public var inLibrary: Int
  public var household: Int
  public var missing: Int
  public var requested: Int
  public init(inLibrary: Int, household: Int, missing: Int, requested: Int) {
    self.inLibrary = inLibrary
    self.household = household
    self.missing = missing
    self.requested = requested
  }
}

public struct AppleImportResult: Sendable, Hashable {
  public let playlist: Playlist
  public let counts: AppleImportCounts
  public let capped: Bool
  public init(playlist: Playlist, counts: AppleImportCounts, capped: Bool) {
    self.playlist = playlist
    self.counts = counts
    self.capped = capped
  }
}

public enum AppleImport {
  /// The most songs one press adds (the server's import limit).
  public static let limit = 200

  /// The request for a playlist's songs, in Apple's order: at most `limit`
  /// songs, each with its album and length so the server keeps live, remix
  /// and demo recordings apart. `moreThanShown` says Apple listed more songs
  /// than were fetched. A song without a title or artist can't be matched
  /// and is left out (the server refuses it).
  public static func request(
    playlist: ApplePlaylist, songs: [ApplePlaylistSong], moreThanShown: Bool = false,
    downloadMissing: Bool
  ) -> AppleImportRequest {
    func clean(_ text: String?, _ length: Int) -> String? {
      let trimmed = (text ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
      return trimmed.isEmpty ? nil : String(trimmed.prefix(length))
    }
    let named = songs.sorted { $0.position < $1.position }.compactMap {
      song -> AppleImportSong? in
      guard let title = clean(song.title, 500), let artist = clean(song.artist, 500) else {
        return nil
      }
      return AppleImportSong(
        title: title, artist: artist, album: clean(song.album, 500),
        duration: min(max(song.duration ?? 0, 0), 86400),
        appleId: song.appleId.map { String($0) })
    }
    return AppleImportRequest(
      applePlaylistId: String(playlist.id.prefix(64)),
      name: clean(playlist.name, 200) ?? "Apple Music playlist",
      curator: clean(playlist.curator, 200),
      songs: Array(named.prefix(limit)),
      downloadMissing: downloadMissing,
      capped: moreThanShown || named.count > limit)
  }

  /// What the sheet offers before adding: songs already on the server (the
  /// person's or a household copy) and songs that would need downloading,
  /// counted over the songs one press adds.
  public static func choice(_ states: [ApplePlaylistSongState]) -> (
    onServer: Int, toDownload: Int
  ) {
    let counted = states.prefix(limit)
    let onServer = counted.filter { $0.track != nil }.count
    return (onServer, counted.count - onServer)
  }

  /// The confirmation after adding.
  public static func summary(_ result: AppleImportResult) -> String {
    let counts = result.counts
    func songs(_ n: Int) -> String { "\(n) song\(n == 1 ? "" : "s")" }
    var parts: [String] = []
    if counts.inLibrary > 0 { parts.append("\(songs(counts.inLibrary)) in your library") }
    if counts.household > 0 { parts.append("\(songs(counts.household)) from the server") }
    if counts.missing > 0 { parts.append("\(songs(counts.missing)) not on the server yet") }
    var lines = [
      "Added to your playlists" + (parts.isEmpty ? "." : ": " + parts.joined(separator: ", ") + ".")
    ]
    if counts.requested > 0 {
      lines.append("\(songs(counts.requested)) requested; they join the playlist once downloaded.")
    } else if counts.missing > 0 {
      lines.append("The missing songs join the playlist if they are downloaded later.")
    }
    if result.capped {
      lines.append("Only the first \(limit) songs were added.")
    }
    return lines.joined(separator: " ")
  }
}
