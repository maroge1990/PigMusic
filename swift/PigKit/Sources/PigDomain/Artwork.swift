import Foundation

public struct ArtworkRequest: Hashable, Sendable {
  public let sha: String
  public let width: Int
  public var filename: String { "\(sha)-v1-\(width).image" }
  public init(sha: String, width: Int = 256) throws {
    guard sha.utf8.count == 64,
      sha.utf8.allSatisfy({
        (48...57).contains($0) || (97...102).contains($0)
      }), [128, 256, 512, 1024, 1600].contains(width)
    else {
      throw ArtworkFailure.invalidRequest
    }
    self.sha = sha
    self.width = width
  }
}

public enum ArtworkFailure: Error, Sendable, Equatable {
  case invalidRequest, tooLarge, invalidImage, unavailable
}

public enum CatalogueArtworkKind: String, Sendable {
  case release, releaseGroup = "release-group"
  /// An album found through Apple's search (D-61); its cover comes through
  /// the server, never from Apple directly.
  case appleAlbum = "apple-album"
}

/// The ids a server artwork alias takes. A request path is only ever built
/// from an id of the shape the server issues for that kind.
public enum ArtworkAlias {
  /// Apple's album ids are numbers (as the server's route accepts them: no
  /// leading zero, at most 15 digits); every MusicBrainz kind takes a UUID.
  public static func accepts(kind: String, id: String) -> Bool {
    guard kind == CatalogueArtworkKind.appleAlbum.rawValue else {
      return UUID(uuidString: id) != nil
    }
    let digits = id.utf8
    return (1...15).contains(digits.count) && digits.first != 48
      && digits.allSatisfy { (48...57).contains($0) }
  }
}

/// How long a tile keeps asking for a cover the server is still fetching
/// (#168). The first ask for an unfetched cover is answered "not available"
/// while the server fetches it, which takes 5 to 9 s for one cover and up to
/// a minute for a full grid; the tile asks again after each of these waits
/// (2, 8, 23, 53 and 113 s after the first miss) and then stops. Only that
/// answer is asked about again, and only while the tile's task is alive.
public enum CoverRetry {
  public static let waits: [Duration] = [
    .seconds(2), .seconds(6), .seconds(15), .seconds(30), .seconds(60),
  ]
}

/// A library album's or artist's own artwork address (#168): what the server
/// gives as `cover_url` while it has no stored image for the item. Asking it
/// answers with a redirect to the stored image once the server has fetched
/// one, and starts the fetch otherwise, like a catalogue alias.
///
/// It exists only for the server's own form: the fixed prefix, `albums` or
/// `artists`, and an id of the shape the server issues (24 lowercase hex
/// digits). The request path is rebuilt from those parts, never copied.
public struct LibraryArtwork: Hashable, Sendable {
  public enum Kind: String, Sendable, CaseIterable { case albums, artists }
  public let kind: Kind
  public let id: String
  public init?(kind: Kind, id: String) {
    guard id.utf8.count == 24,
      id.utf8.allSatisfy({ (48...57).contains($0) || (97...102).contains($0) })
    else { return nil }
    self.kind = kind
    self.id = id
  }
  /// From a `cover_url`; its query (`?portraits=1`, `?v=3`) is not part of
  /// the address. Nil for a stored cover's address and for anything else.
  public init?(coverUrl: String?) {
    guard let coverUrl else { return nil }
    let path = coverUrl.prefix { $0 != "?" && $0 != "#" }
    guard
      let prefix = ["/api/v1/library-artwork/", "/api/library-artwork/"].first(where: {
        path.hasPrefix($0)
      })
    else { return nil }
    let parts = path.dropFirst(prefix.count).split(
      separator: "/", omittingEmptySubsequences: false)
    guard parts.count == 2, let kind = Kind(rawValue: String(parts[0])) else { return nil }
    self.init(kind: kind, id: String(parts[1]))
  }
  public var path: String { "/api/v1/library-artwork/\(kind.rawValue)/\(id)" }
}

/// What to ask for when the item has no stored cover: its own artwork
/// address. A stored hash always wins, so these are nil with one.
extension Track {
  public var libraryArtwork: LibraryArtwork? {
    coverSha == nil ? LibraryArtwork(coverUrl: coverUrl) : nil
  }
}
extension Album {
  public var libraryArtwork: LibraryArtwork? {
    coverSha == nil ? LibraryArtwork(coverUrl: coverUrl) : nil
  }
}
extension Artist {
  public var libraryArtwork: LibraryArtwork? {
    coverSha == nil ? LibraryArtwork(coverUrl: coverUrl) : nil
  }
}
