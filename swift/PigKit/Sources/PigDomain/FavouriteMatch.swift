import Foundation

/// The server's test for whether a song is one of the person's favourites
/// (`Playlists.favourite`, `matching.same_song`), copied exactly so the
/// iPhone reads the same answer from its library's Favourites playlist
/// (#284; Codex round 1 on batch G). Shared fixtures keep the two alike:
/// tests/fixtures/favourite_same_song.json.
///
/// Two songs are the same when their artist, title, album and album artist
/// (the artist when empty) normalise alike, no release or recording id
/// conflicts where both are known, and lengths, where both are known and
/// not 0, are within 5 s. Normalising is `library.normalise`, in its order:
/// NFKC, then Python's `str.casefold()`, then the runs of Python's `\w`
/// characters joined by single spaces. Case folding and `\w` come from
/// tables generated from Python's own Unicode data (FavouriteFolding,
/// scripts/generate_favourite_folding.py; Codex round 2 on batch G), and
/// tests/fixtures/favourite_normalise.json checks the whole of it.
public enum FavouriteMatch {
  /// One field, normalised, as Unicode scalar values: compared as the server
  /// compares strings, by code point, not by Swift's canonical equivalence.
  public typealias Words = [UInt32]

  public static func normalise(_ text: String) -> Words {
    var result: Words = []
    var pendingSpace = false
    for scalar in text.precomposedStringWithCompatibilityMapping.unicodeScalars {
      for value in FavouriteFolding.folds[scalar.value] ?? [scalar.value] {
        if isWordCharacter(value) {
          if pendingSpace { result.append(32) }
          pendingSpace = false
          result.append(value)
        } else if !result.isEmpty {
          pendingSpace = true
        }
      }
    }
    return result
  }

  /// The four fields that place a song (`matching.context`).
  public static func context(_ track: Track) -> [Words] {
    let albumArtist = track.albumArtist.flatMap { $0.isEmpty ? nil : $0 } ?? track.artist
    return [track.artist, track.title, track.album ?? "", albumArtist].map(normalise)
  }

  /// `matching.same_in_context`: no conflicting ids, close lengths.
  public static func sameInContext(_ left: Track, _ right: Track) -> Bool {
    for (a, b) in [
      (left.musicbrainzReleaseId, right.musicbrainzReleaseId),
      (left.musicbrainzRecordingId, right.musicbrainzRecordingId),
    ] {
      if let a, let b, !a.isEmpty, !b.isEmpty, a != b { return false }
    }
    if let a = left.duration, let b = right.duration, a != 0, b != 0, abs(a - b) > 5 {
      return false
    }
    return true
  }

  /// `matching.same_song`.
  public static func same(_ left: Track, _ right: Track) -> Bool {
    context(left) == context(right) && sameInContext(left, right)
  }

  /// The Favourites playlist's songs by their context, built once and asked
  /// for many songs (a sync looks up every song shown).
  public struct Index: Sendable {
    private let songs: [[Words]: [Track]]
    /// Nil while the library holds no Favourites playlist with its songs.
    public init?(_ playlists: [Playlist]) {
      guard let entries = playlists.first(where: { $0.favourites == true })?.entries else {
        return nil
      }
      songs = Dictionary(grouping: entries.map(\.entryTrack), by: FavouriteMatch.context)
    }
    public func contains(_ track: Track) -> Bool {
      songs[FavouriteMatch.context(track)]?.contains { FavouriteMatch.sameInContext($0, track) }
        ?? false
    }
  }
}

/// The Favourites index, built once per version of the playlist and reused
/// by every lookup until the playlist changes (Codex round 2 on batch G: a
/// menu lookup or a sync rebuilt it each time). A version is the playlist's
/// id (its owner's), its revision, which the server raises on every change,
/// and its number of songs.
public struct FavouriteIndexCache: Sendable {
  private struct Version: Equatable, Sendable {
    let id: String
    let revision: Int
    let count: Int
  }
  private var version: Version?
  private var index: FavouriteMatch.Index?
  /// How many times an index was built, for tests.
  public private(set) var builds = 0
  public init() {}
  /// The index of `playlists`' Favourites, nil while they hold none with
  /// its songs.
  public mutating func index(_ playlists: [Playlist]) -> FavouriteMatch.Index? {
    guard let favourites = playlists.first(where: { $0.favourites == true }),
      let entries = favourites.entries
    else {
      version = nil
      index = nil
      return nil
    }
    let current = Version(id: favourites.id, revision: favourites.revision, count: entries.count)
    if current != version || index == nil {
      index = FavouriteMatch.Index([favourites])
      version = current
      builds += 1
    }
    return index
  }
}

extension FavouriteMatch {

  /// Python's `\w` in a str pattern, from its own data: a binary search of
  /// the generated ranges.
  static func isWordCharacter(_ value: UInt32) -> Bool {
    let ranges = FavouriteFolding.wordRanges
    var low = 0
    var high = ranges.count - 1
    while low <= high {
      let middle = (low + high) / 2
      let (first, last) = ranges[middle]
      if value < first {
        high = middle - 1
      } else if value > last {
        low = middle + 1
      } else {
        return true
      }
    }
    return false
  }
}
