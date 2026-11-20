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
/// not 0, are within 5 s. Normalising is `library.normalise`: NFKC, Python's
/// case folding, then the runs of word characters (letters, digits and
/// "_", Python's `\w`) joined by single spaces.
public enum FavouriteMatch {
  /// One field, normalised, as Unicode scalar values: compared as the server
  /// compares strings, by code point, not by Swift's canonical equivalence.
  public typealias Words = [UInt32]

  public static func normalise(_ text: String) -> Words {
    let folded = casefold(text.precomposedStringWithCompatibilityMapping)
    var words: [Words] = []
    var word: Words = []
    for scalar in folded.unicodeScalars {
      if isWordCharacter(scalar) {
        word.append(scalar.value)
      } else if !word.isEmpty {
        words.append(word)
        word = []
      }
    }
    if !word.isEmpty { words.append(word) }
    return Array(words.joined(separator: [32]))
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

  /// Python's `str.casefold()` for the comparisons that matter here: full
  /// case folding equates what upper-casing then lower-casing equates
  /// ("ß", "SS" and "ss"; "ς", "Σ" and "σ"; "ſ" and "s").
  static func casefold(_ text: String) -> String {
    String(String.UnicodeScalarView(text.unicodeScalars.flatMap { scalar in
      scalar.properties.uppercaseMapping.unicodeScalars.flatMap {
        $0.properties.lowercaseMapping.unicodeScalars
      }
    }))
  }

  /// Python's `\w` in a str pattern: `str.isalnum()` or "_", that is any
  /// letter (L*) or number (N*), not a mark or punctuation.
  static func isWordCharacter(_ scalar: Unicode.Scalar) -> Bool {
    if scalar == "_" { return true }
    switch scalar.properties.generalCategory {
    case .uppercaseLetter, .lowercaseLetter, .titlecaseLetter, .modifierLetter, .otherLetter,
      .decimalNumber, .letterNumber, .otherNumber:
      return true
    default:
      return scalar.properties.numericType != nil
    }
  }
}
