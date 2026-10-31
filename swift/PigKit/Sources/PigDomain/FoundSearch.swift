import Foundation

/// The server's ranked search (D-61): every kind of result from the person's
/// library and the rest of the server, marked, never split by where it lives.
public struct FoundResults: Codable, Sendable {
  public struct Ref: Codable, Sendable, Hashable {
    public let kind: String
    public let id: String
    public init(kind: String, id: String) {
      self.kind = kind
      self.id = id
    }
  }
  /// An album or artist with whether it is in the person's library.
  public struct Marked<Item: Codable & Sendable>: Codable, Sendable {
    public let item: Item
    public let inLibrary: Bool
    public init(_ item: Item, inLibrary: Bool) {
      self.item = item
      self.inLibrary = inLibrary
    }
    private enum Keys: String, CodingKey { case inLibrary }
    public init(from decoder: any Decoder) throws {
      item = try Item(from: decoder)
      inLibrary =
        try decoder.container(keyedBy: Keys.self).decodeIfPresent(Bool.self, forKey: .inLibrary)
        ?? false
    }
    public func encode(to encoder: any Encoder) throws {
      try item.encode(to: encoder)
      var container = encoder.container(keyedBy: Keys.self)
      try container.encode(inLibrary, forKey: .inLibrary)
    }
  }
  public let top: [Ref]
  public let songs: [Track]
  public let albums: [Marked<Album>]
  public let artists: [Marked<Artist>]
  public let playlists: [Playlist]
  public init(
    top: [Ref] = [], songs: [Track] = [], albums: [Marked<Album>] = [],
    artists: [Marked<Artist>] = [], playlists: [Playlist] = []
  ) {
    self.top = top
    self.songs = songs
    self.albums = albums
    self.artists = artists
    self.playlists = playlists
  }
}

/// Apple's search, for songs and albums not on the server, in Apple's order.
public struct AppleSong: Codable, Sendable, Hashable, Identifiable {
  public let appleId: Int
  public let title: String
  public let artist: String
  public let album: String?
  public let albumAppleId: Int?
  public let duration: Double?
  public let explicit: Bool?
  public let year: String?
  public var id: Int { appleId }
}
public struct AppleAlbum: Codable, Sendable, Hashable, Identifiable {
  public let appleId: Int
  public let title: String
  public let artist: String
  public let trackCount: Int?
  public let explicit: Bool?
  public let year: String?
  public var id: Int { appleId }
}
public struct OnlineResults: Codable, Sendable {
  public let songs: [AppleSong]
  public let albums: [AppleAlbum]
  /// "ready", "busy" (past Apple's request budget for now) or "unavailable".
  public let state: String
  public init(songs: [AppleSong] = [], albums: [AppleAlbum] = [], state: String = "ready") {
    self.songs = songs
    self.albums = albums
    self.state = state
  }
}
/// The MusicBrainz recording (song) or release group (album) an Apple result
/// is, matched by the server to the exact version.
public struct ResolvedApple: Codable, Sendable, Hashable {
  public let id: String
  public let name: String
  public let artist: String
  public let artistId: String
  public let releaseId: String?
  public let album: String?
}

/// One row of the search screen.
public enum FoundItem: Sendable, Identifiable {
  case song(Track)
  case album(Album, inLibrary: Bool)
  case artist(Artist, inLibrary: Bool)
  case playlist(Playlist)
  case appleSong(AppleSong)
  case appleAlbum(AppleAlbum)
  case catalogueArtist(CatalogueArtist)
  public var id: String {
    switch self {
    case .song(let track): "song:" + track.id
    case .album(let album, _): "album:" + album.id
    case .artist(let artist, _): "artist:" + artist.id
    case .playlist(let playlist): "playlist:" + playlist.id
    case .appleSong(let song): "apple-song:\(song.appleId)"
    case .appleAlbum(let album): "apple-album:\(album.appleId)"
    case .catalogueArtist(let artist): "catalogue-artist:" + artist.id
    }
  }
}

/// The chips across the results: Top results, then one kind each.
public enum FoundKind: String, CaseIterable, Sendable {
  case top = "Top results"
  case artists = "Artists"
  case albums = "Albums"
  case songs = "Songs"
  case playlists = "Playlists"
}

/// The server's results with Apple's and MusicBrainz's added after them, so
/// what arrives later never moves what is already on screen (D-61).
public struct FoundScreen: Sendable {
  public static let topSize = 8
  public let top: [FoundItem]
  public let artists: [FoundItem]
  public let albums: [FoundItem]
  public let songs: [FoundItem]
  public let playlists: [FoundItem]
  public init(
    _ results: FoundResults?, online: OnlineResults? = nil, catalogue: [CatalogueArtist] = []
  ) {
    let server = results ?? FoundResults()
    let songs = server.songs.map { FoundItem.song($0) }
    let albums = server.albums.map { FoundItem.album($0.item, inLibrary: $0.inLibrary) }
    let artists = server.artists.map { FoundItem.artist($0.item, inLibrary: $0.inLibrary) }
    let playlists = server.playlists.map { FoundItem.playlist($0) }
    var byRef: [String: FoundItem] = [:]
    for item in songs + albums + artists + playlists { byRef[item.id] = item }
    var top = server.top.compactMap { byRef[$0.kind + ":" + $0.id] }
    // Artists the server already lists by name aren't repeated from MusicBrainz.
    let named = Set(server.artists.map { SearchText.key($0.item.name) })
    let others = catalogue.filter { !named.contains(SearchText.key($0.name)) }
      .map { FoundItem.catalogueArtist($0) }
    let appleSongs = (online?.songs ?? []).map { FoundItem.appleSong($0) }
    let appleAlbums = (online?.albums ?? []).map { FoundItem.appleAlbum($0) }
    // Apple's best songs and album fill Top results after the server's own,
    // so a song nobody here has still comes up first when nothing else does.
    for item in Array(appleSongs.prefix(3)) + Array(appleAlbums.prefix(2)) + others.prefix(1)
    where top.count < Self.topSize {
      top.append(item)
    }
    self.top = top
    self.artists = artists + others
    self.albums = albums + appleAlbums
    self.songs = songs + appleSongs
    self.playlists = playlists
  }
  public subscript(kind: FoundKind) -> [FoundItem] {
    switch kind {
    case .top: top
    case .artists: artists
    case .albums: albums
    case .songs: songs
    case .playlists: playlists
    }
  }
  /// The chips worth showing: Top results, then kinds with any results.
  public var kinds: [FoundKind] {
    FoundKind.allCases.filter { $0 == .top || !self[$0].isEmpty }
  }
}
