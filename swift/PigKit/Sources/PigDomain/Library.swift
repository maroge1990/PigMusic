import CryptoKit
import Foundation

public struct Track: Codable, Sendable, Identifiable, Hashable {
  public var id: String
  public var title: String
  public var artist: String
  public var album: String?
  public var albumArtist: String?
  public var year: MusicYear?
  public var albumId: String?
  public var duration: Double?
  public var trackNumber: Int?
  public var discNumber: Int?
  public var codec: String?
  public var coverSha: String?
  public var sourceRevision: String?
  public var serverState: String?
  public var musicbrainzReleaseId: String?
  public var musicbrainzRecordingId: String?
  public var libraryTrackId: String?
  /// On a catalogue edition other than the recommended one: a request keeps it.
  public var explicitEdition: Bool?
  /// From a Live, Remix or Demo release: that recording, not the studio one.
  public var recordingKind: [String]?
  public var actionTrack: Track {
    var result = self
    if let libraryTrackId {
      result.id = libraryTrackId
      result.libraryTrackId = nil
    }
    return result
  }
  /// The state a row or the song sheet shows (#206): a song asked for here
  /// (`requested`) shows Requested at once, before the server says so.
  public func shownState(requested: @autoclosure () -> Bool) -> String? {
    serverState == "not_in_library" && requested() ? "requested" : serverState
  }
  public init(
    id: String, title: String, artist: String, album: String? = nil, duration: Double? = nil
  ) {
    self.id = id
    self.title = title
    self.artist = artist
    self.album = album
    self.duration = duration
  }
}

public struct Album: Codable, Sendable, Identifiable, Hashable {
  public var id: String
  public var title: String
  public var artist: String
  public var artistId: String?
  public var category: String?
  public var trackCount: Int?
  public var coverSha: String?
  public var year: MusicYear?
  public var duration: Double?
  public var tracks: [Track]?
  public var musicbrainzReleaseId: String?
}

public struct Artist: Codable, Sendable, Identifiable, Hashable {
  public var id: String
  public var name: String
  public var albumIds: [String]?
  public var albumCount: Int?
  public var trackCount: Int?
  public var compilationOnly: Bool?
  public var coverAlbumId: String?
  /// The server's image for the artist (a portrait, else an album cover).
  public var coverSha: String?
}

public struct PlaylistEntry: Codable, Sendable, Identifiable, Hashable {
  public var entryId: String
  public var id: String { entryId }
  public var title: String
  public var artist: String
  public var album: String?
  public var albumArtist: String?
  public var duration: Double?
  public var trackNumber: Int?
  public var discNumber: Int?
  public var position: Int
  public var year: MusicYear?
  public var serverState: String?
  public var householdAvailable: Bool?
  public var savedId: String?
  public var savedTrack: Track?
  /// The household file when the song is on the server but not in this library.
  public var householdTrack: Track?
  public var musicbrainzReleaseId: String?
  public var musicbrainzRecordingId: String?
}

public struct Playlist: Codable, Sendable, Identifiable, Hashable {
  public var id: String
  public var name: String
  public var revision: Int
  public var favourites: Bool?
  public var trackCount: Int?
  public var entries: [PlaylistEntry]?
}

public struct Person: Codable, Sendable, Identifiable {
  public var id: String
  public var username: String
  public var displayName: String
  public var role: String
}

public enum LibraryCodec {
  public static func decoder() -> JSONDecoder {
    let decoder = JSONDecoder()
    decoder.keyDecodingStrategy = .convertFromSnakeCase
    return decoder
  }
  public static func encoder() -> JSONEncoder {
    let encoder = JSONEncoder()
    encoder.keyEncodingStrategy = .convertToSnakeCase
    return encoder
  }
  public static func convert<T: Decodable, V: Encodable>(_ value: V, to: T.Type = T.self) throws
    -> T
  {
    try decoder().decode(T.self, from: JSONEncoder().encode(value))
  }
}

public struct LibrarySnapshot: Codable, Sendable {
  public var revision: String
  public var tracks: [Track]
  public var albums: [Album]
  public var artists: [Artist]
  public var playlists: [Playlist]
  public init(
    revision: String, tracks: [Track] = [], albums: [Album] = [], artists: [Artist] = [],
    playlists: [Playlist] = []
  ) {
    self.revision = revision
    self.tracks = tracks
    self.albums = albums
    self.artists = artists
    self.playlists = playlists
  }
}

public struct DeletedEntities: Codable, Sendable {
  public var tracks: [String]
  public var albums: [String]
  public var artists: [String]
  public var playlists: [String]
}

public struct LibraryDelta: Codable, Sendable {
  public var revision: String
  public var upserts: Entities
  public var deletes: DeletedEntities
  public struct Entities: Codable, Sendable {
    public var tracks: [Track]
    public var albums: [Album]
    public var artists: [Artist]
    public var playlists: [Playlist]
  }
}

public enum LibraryCollection: String, CaseIterable, Sendable {
  case tracks, albums, artists, playlists
}

/// Local namespaces never depend on usernames or a server's display name.
public enum LibraryNamespace {
  public static func key(server: URL, personId: String) -> String {
    SHA256.hash(data: Data((server.absoluteString + "\n" + personId).utf8))
      .map { String(format: "%02x", $0) }.joined()
  }
}

extension PlaylistEntry {
  /// Your file, else the household file (played from the server without adding
  /// it), else the playlist's own description for a remote source.
  public var playbackTrack: Track { savedTrack ?? householdTrack ?? entryTrack }
  /// The song as the playlist stored it, with its recording and release identity,
  /// for edits such as unfavouriting that match the stored entry.
  public var entryTrack: Track {
    var track = Track(
      id: savedId ?? entryId, title: title, artist: artist, album: album, duration: duration)
    track.albumArtist = albumArtist
    track.year = year
    track.trackNumber = trackNumber
    track.discNumber = discNumber
    track.musicbrainzReleaseId = musicbrainzReleaseId
    track.musicbrainzRecordingId = musicbrainzRecordingId
    track.serverState = "not_in_library"
    return track
  }
}

public enum LibrarySort: String, Codable, Sendable, CaseIterable {
  case title, artist, titleDescending
}

extension String {
  /// The name as it files alphabetically: a leading "The " is ignored, so
  /// "The Beatles" sits under B. "The" alone keeps its place.
  public var sortingName: Substring {
    let trimmed = drop { $0.isWhitespace }
    guard trimmed.count > 4, trimmed.prefix(4).lowercased() == "the " else { return trimmed }
    return trimmed.dropFirst(4).drop { $0.isWhitespace }
  }
  /// Alphabetical order by `sortingName`, ignoring case and accents and reading
  /// numbers as numbers. It never depends on the device locale, so database
  /// indexes built with it stay valid.
  public func sortingCompare(_ other: String) -> ComparisonResult {
    let options: String.CompareOptions = [.caseInsensitive, .diacriticInsensitive, .numeric]
    let order = String(sortingName).compare(String(other.sortingName), options: options)
    return order == .orderedSame ? compare(other) : order
  }
}

/// Whether the on-screen library must reload around a sync. A sync reloads
/// only when it changed the library, so a view change made while one runs
/// (choosing another album category, say) is remembered here and loaded once
/// the sync has finished, changed or not.
public struct LibraryReloadGate: Sendable {
  private var viewChanged = false
  public init() {}
  /// The visible lists changed what they show while a sync held the library.
  public mutating func viewChangedDuringSync() { viewChanged = true }
  /// After a sync: whether to reload now. Consumes any pending view change.
  public mutating func afterSync(libraryChanged: Bool) -> Bool {
    defer { viewChanged = false }
    return libraryChanged || viewChanged
  }
  /// A view change still waiting for its reload (consumed).
  public mutating func takePending() -> Bool {
    defer { viewChanged = false }
    return viewChanged
  }
}
