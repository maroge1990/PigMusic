import Foundation

public enum SearchText {
  /// Type-ahead waits this long after the last keystroke (at least 300 ms, so
  /// typing adds no catalogue load).
  public static let debounce: Duration = .milliseconds(350)
  /// Catalogue artist searches start at this many characters.
  public static let catalogueMinimum = 2
  public static func clean(_ query: String) -> String {
    String(query.trimmingCharacters(in: .whitespacesAndNewlines).prefix(150))
  }
  /// Answers for the same words share one cache entry.
  public static func key(_ query: String) -> String {
    clean(query).folding(options: [.caseInsensitive, .diacriticInsensitive], locale: nil)
  }
}
public struct LibrarySearchPage: Sendable {
  public let tracks: [Track]
  public let albums: [Album]
  public let artists: [Artist]
  public let offset: Int
  public let nextOffset: Int?
  public let revision: String
  public init(
    tracks: [Track] = [], albums: [Album] = [], artists: [Artist] = [], offset: Int = 0,
    nextOffset: Int? = nil, revision: String = ""
  ) {
    self.tracks = tracks
    self.albums = albums
    self.artists = artists
    self.offset = offset
    self.nextOffset = nextOffset
    self.revision = revision
  }
}
public struct CatalogueArtist: Codable, Sendable, Identifiable {
  public let id: String
  public let name: String
  public let description: String
}
public struct CatalogueArtistPage: Codable, Sendable {
  public let artists: [CatalogueArtist]
  public let total: Int
  public let offset: Int
  public let nextOffset: Int?
}
