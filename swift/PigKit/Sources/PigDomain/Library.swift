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
  /// The server's address for the cover; without a `coverSha` it can be the
  /// album's own artwork address (`LibraryArtwork`).
  public var coverUrl: String?
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
  /// The server's address for the cover; without a `coverSha` it can be the
  /// album's own artwork address (`LibraryArtwork`).
  public var coverUrl: String?
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
  /// The server's address for that image; without a stored one it can be
  /// the artist's own artwork address (`LibraryArtwork`).
  public var coverUrl: String?
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

/// Whether a song shown from elsewhere (Home, a catalogue page, search, a
/// playlist entry) is a song the person's library holds (#211), by the
/// server's matching (`server/matching.py`, `LibraryIndex.match`): the one
/// copy of the same edition (artist, title, album and album artist, no
/// conflicting release or recording id, lengths within 5 s), else, unless
/// the song was asked for as one edition (`explicitEdition`), the best copy
/// of the same song on any edition (edition-free title, lead artist and
/// kind of recording; no conflicting recording id; lengths within 8 s).
/// Anything the server would send to review is no match: Remove and
/// Download must never act on another recording or an arbitrary file.
public enum SongIdentity {
  /// Lowercased words without punctuation, as the server's `normalise`
  /// compares them: "Choosin’ Texas" is ["choosin", "texas"].
  public static func words(_ text: String) -> [String] {
    text.precomposedStringWithCompatibilityMapping.lowercased()
      .components(separatedBy: CharacterSet.alphanumerics.inverted)
      .filter { !$0.isEmpty }
  }
  // The server's qualifier patterns (`EDITION`, `DISTINCT`, `FEATURE`,
  // `QUALIFIER`), unchanged.
  nonisolated(unsafe) private static let edition = try! NSRegularExpression(
    pattern:
      #"\b(deluxe|expanded|edition|remaster(ed)?|anniversary|bonus|special|collector'?s|reissue|explicit|clean|mono|stereo|super|album version|(19|20)\d\d)\b"#,
    options: .caseInsensitive)
  nonisolated(unsafe) private static let distinct = try! NSRegularExpression(
    pattern:
      #"\b(live|acoustic|demos?|remix(es)?|mix|edit|instrumentals?|karaoke|sessions?|unplugged|cover|reprise|orchestral|piano)\b"#,
    options: .caseInsensitive)
  nonisolated(unsafe) private static let feature = try! NSRegularExpression(
    pattern: #"^\s*(feat\.?|ft\.?|featuring|with)\s"#, options: .caseInsensitive)
  nonisolated(unsafe) private static let qualifier = try! NSRegularExpression(
    pattern: #"\s*(?:[\(\[]([^\)\]]*)[\)\]]|\s-\s([^\(\)\[\]]*))\s*$"#)
  private static func found(_ pattern: NSRegularExpression, in text: String) -> Bool {
    pattern.firstMatch(in: text, range: NSRange(text.startIndex..., in: text)) != nil
  }
  /// The title without trailing edition qualifiers (the server's
  /// `strip_edition`): "Go Your Own Way - 2004 Remaster" is "Go Your Own
  /// Way", but "Brat (Live)" stays. Featured-artist credits go too.
  public static func stripEdition(_ title: String) -> String {
    var text = title
    while let match = qualifier.firstMatch(in: text, range: NSRange(text.startIndex..., in: text)),
      let whole = Range(match.range, in: text)
    {
      let named = [1, 2].lazy.compactMap { Range(match.range(at: $0), in: text) }.first
        .map { String(text[$0]) } ?? ""
      guard found(feature, in: named) || (found(edition, in: named) && !found(distinct, in: named))
      else { break }
      text = String(text[..<whole.lowerBound])
    }
    let stripped = text.trimmingCharacters(in: .whitespacesAndNewlines)
    return stripped.isEmpty ? title : stripped
  }
  /// Titles compare without edition qualifiers (the server's `title_key`).
  public static func titleKey(_ title: String) -> [String] {
    let key = words(stripEdition(title))
    return key.isEmpty ? words(title) : key
  }
  /// The lead credit's words.
  public static func leadArtist(_ artist: String) -> [String] {
    let pattern = #"\s+(feat\.?|ft\.?|featuring|with|&|and|x|vs\.?)\s+|,\s*"#
    let lead =
      artist.range(of: pattern, options: [.regularExpression, .caseInsensitive])
      .map { String(artist[..<$0.lowerBound]) } ?? artist
    let found = words(lead)
    return found.isEmpty ? words(artist) : found
  }
  private static let flavours: [String: String] = [
    "live": "live", "unplugged": "unplugged", "acoustic": "acoustic", "karaoke": "karaoke",
    "demo": "demo", "demos": "demo", "remix": "remix", "remixes": "remix", "remixed": "remix",
    "session": "session", "sessions": "session", "instrumental": "instrumental",
    "instrumentals": "instrumental",
  ]
  /// The kinds of recording a song is: named by its album's title ("Live at
  /// Leeds") or known from its release (`recordingKind`).
  public static func kinds(_ track: Track) -> Set<String> {
    Set(words(track.album ?? "").compactMap { flavours[$0] })
      .union(track.recordingKind ?? [])
  }
  /// The library copy the server's matching would take for `song` among
  /// `candidates` (each with its album when known), or nil when it would
  /// take none or send the song to review.
  public static func pick(_ song: Track, from candidates: [(track: Track, album: Album?)])
    -> Track?
  {
    var seen = Set<String>()
    let unique = candidates.filter { seen.insert($0.track.id).inserted }
    // Several copies of one edition are usable only when their hashes prove
    // the same bytes, which this phone can't see: none of them is taken.
    let edition = unique.filter { sameEdition(song, $0.track) }
    if edition.count == 1 { return edition[0].track }
    if song.explicitEdition == true { return nil }
    // Any edition: the server takes the best copy (`any_edition`).
    return unique.filter { same(song, $0.track) }
      .min { rank(song, $0.track, $0.album) < rank(song, $1.track, $1.album) }?.track
  }
  /// The same song on any edition (`compatible`).
  public static func same(_ song: Track, _ owned: Track) -> Bool {
    guard !titleKey(song.title).isEmpty, titleKey(song.title) == titleKey(owned.title),
      leadArtist(song.artist) == leadArtist(owned.artist), kinds(song) == kinds(owned),
      !conflict(song.musicbrainzRecordingId, owned.musicbrainzRecordingId)
    else { return false }
    return closeLengths(song, owned, within: 8)
  }
  /// The same song on the same edition (`context`, `same_song`).
  public static func sameEdition(_ song: Track, _ owned: Track) -> Bool {
    func context(_ track: Track) -> [[String]] {
      [
        words(track.artist), words(track.title), words(track.album ?? ""),
        words(track.albumArtist ?? track.artist),
      ]
    }
    // Stricter than the server here: a live, remix or demo song is never
    // the studio one, even with the same tags.
    guard !words(song.title).isEmpty, context(song) == context(owned),
      kinds(song) == kinds(owned),
      !conflict(song.musicbrainzReleaseId, owned.musicbrainzReleaseId),
      !conflict(song.musicbrainzRecordingId, owned.musicbrainzRecordingId)
    else { return false }
    return closeLengths(song, owned, within: 5)
  }
  /// The server's order among any-edition copies: the requested album's
  /// title first, then the artist's own albums, the fullest, the oldest,
  /// then by id.
  private static func rank(_ song: Track, _ owned: Track, _ album: Album?) -> (
    Int, Int, Int, Int, String
  ) {
    let title = titleKey(album?.title ?? owned.album ?? "")
    let artist = leadArtist(album?.artist ?? owned.albumArtist ?? owned.artist)
    let year =
      (album?.year?.text).flatMap { text in
        text.range(of: #"\d{4}"#, options: .regularExpression).flatMap { Int(text[$0]) }
      } ?? 0
    return (
      title == titleKey(song.album ?? "") ? 0 : 1,
      artist == leadArtist(song.artist) ? 0 : 1,
      -(album?.trackCount ?? (album?.tracks?.count ?? 0)),
      year == 0 ? 9999 : year,
      owned.id
    )
  }
  private static func conflict(_ left: String?, _ right: String?) -> Bool {
    guard let left, let right, !left.isEmpty, !right.isEmpty else { return false }
    return left != right
  }
  private static func closeLengths(_ left: Track, _ right: Track, within seconds: Double) -> Bool
  {
    guard let a = left.duration, let b = right.duration, a > 0, b > 0 else { return true }
    return abs(a - b) <= seconds
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
  /// Newest first; albums only (D-120).
  case year
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
