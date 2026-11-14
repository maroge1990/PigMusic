import Foundation

/// Similar songs when a queue ends (#202): what to ask the server for.
public enum QueueContinuation {
  /// The last songs to be like.
  public static let seedCount = 5
  /// Whether a queue reaching its end goes on with similar songs: not the
  /// radio (it refills itself), not on repeat, and only when the person
  /// hasn't turned it off.
  public static func continues(keepPlaying: Bool, repeating: Bool, radio: Bool) -> Bool {
    keepPlaying && !repeating && !radio
  }
  /// The last few songs up to and including the current one (most recent
  /// last) as seeds, and every song in the queue to leave out.
  public static func request(queue ids: [String], current index: Int) -> (
    seeds: [String], exclude: [String]
  ) {
    guard !ids.isEmpty else { return ([], []) }
    let end = min(max(index, 0), ids.count - 1)
    let seeds = Array(ids[max(0, end - seedCount + 1)...end])
    var seen = Set<String>()
    let exclude = ids.filter { seen.insert($0).inserted }.suffix(500)
    return (seeds, Array(exclude))
  }
}

/// A person's listening for the week, month or year so far (#203).
public struct Rewind: Codable, Sendable {
  public let period: String
  public let start: Int
  public let end: Int
  public let timezone: String
  public let plays: Int
  public let minutes: Int
  public let topSongs: [RewindSong]
  public let topArtists: [RewindArtist]
  public let topAlbums: [RewindAlbum]
  public let newArtists: [String]
  public let topMixes: [RewindMix]
  /// The top songs that can still play, for "Play your top songs".
  public var playable: [Track] { topSongs.filter(\.available).map(\.track) }
}

public struct RewindSong: Codable, Sendable, Identifiable {
  public let track: Track
  public let plays: Int
  /// False for a mix song that is no longer kept: shown, not played.
  public let available: Bool
  public var id: String { track.id }
  private enum CodingKeys: String, CodingKey { case track, plays }
  private struct Availability: Decodable { let available: Bool? }
  public init(track: Track, plays: Int, available: Bool = true) {
    self.track = track
    self.plays = plays
    self.available = available
  }
  public init(from decoder: Decoder) throws {
    let values = try decoder.container(keyedBy: CodingKeys.self)
    track = try values.decode(Track.self, forKey: .track)
    plays = try values.decode(Int.self, forKey: .plays)
    available =
      (try? values.decode(Availability.self, forKey: .track).available ?? true) ?? true
  }
  public func encode(to encoder: Encoder) throws {
    var values = encoder.container(keyedBy: CodingKeys.self)
    try values.encode(track, forKey: .track)
    try values.encode(plays, forKey: .plays)
  }
}

public struct RewindArtist: Codable, Sendable, Identifiable {
  public let name: String
  public let plays: Int
  public let minutes: Int
  public var id: String { name }
}

public struct RewindAlbum: Codable, Sendable, Identifiable {
  public let title: String
  public let artist: String
  public let plays: Int
  public let coverUrl: String?
  public var id: String { title + "\u{1F}" + artist }
}

public struct RewindMix: Codable, Sendable, Identifiable {
  public let title: String
  public let plays: Int
  public var id: String { title }
}
