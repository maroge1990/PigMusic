import Foundation

/// What one song row shows in its fixed slots (D-118, #248): the cover, or
/// the track number on an album page or chart; the title with a small star
/// for a favourite; "Artist · Album" (the artist always, numbered rows
/// included); and the length on numbered rows.
public struct SongRowFacts: Equatable, Sendable {
  public enum Lead: Equatable, Sendable {
    case cover
    case number(Int)
  }
  /// Where the row is drawn.
  public enum Place: Equatable, Sendable {
    /// Any list of songs: cover first.
    case list
    /// A numbered list that isn't one album (a chart): number, then
    /// "Artist · Album".
    case chart(Int)
    /// An album's own page: number, then the artist (the album is the page).
    case album(Int)
  }
  public var lead: Lead
  public var title: String
  public var star: Bool
  public var caption: String
  /// "3:07", on numbered rows.
  public var length: String?

  public init(_ track: Track, place: Place = .list, favourite: Bool = false) {
    title = track.title
    star = favourite
    let artist = track.artist.isEmpty ? nil : track.artist
    switch place {
    case .list:
      lead = .cover
      caption = Self.join([artist, track.album])
      length = nil
    case .chart(let number):
      lead = .number(number)
      caption = Self.join([artist, track.album])
      length = Self.length(track.duration)
    case .album(let number):
      lead = .number(number)
      caption = artist ?? ""
      length = Self.length(track.duration)
    }
  }

  /// "Artist · Album" for a song the server hasn't got (an Apple Music or
  /// Shazam song), drawn by the same row.
  public static func caption(artist: String, album: String?) -> String {
    join([artist, album])
  }

  public static func length(_ seconds: Double?) -> String? {
    guard let seconds, seconds.isFinite, seconds > 0 else { return nil }
    let whole = Int(seconds.rounded())
    return String(format: "%d:%02d", whole / 60, whole % 60)
  }

  private static func join(_ parts: [String?]) -> String {
    parts.compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · ")
  }
}
