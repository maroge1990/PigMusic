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
  /// Whether similar songs that just arrived start playing at once: only
  /// when the queue already ended on its last song while they were fetched
  /// (Codex round 1); otherwise they wait in Up next.
  public static func playsAtOnce(ended: Bool, atLastSong: Bool, playing: Bool) -> Bool {
    ended && atLastSong && !playing
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

/// A person's listening for the week, month or year so far (#203), with
/// each day of it for the bars (#216).
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
  /// Each local day from `start` up to and including today; empty from a
  /// server older than #216.
  public let days: [RewindDay]
  /// The top songs that can still play.
  public var playable: [Track] { topSongs.filter(\.available).map(\.track) }
  private enum CodingKeys: String, CodingKey {
    case period, start, end, timezone, plays, minutes, topSongs, topArtists, topAlbums
    case newArtists, topMixes, days
  }
  public init(
    period: String, start: Int = 0, end: Int = 0, timezone: String = "UTC", plays: Int,
    minutes: Int, topSongs: [RewindSong] = [], topArtists: [RewindArtist] = [],
    topAlbums: [RewindAlbum] = [], newArtists: [String] = [], topMixes: [RewindMix] = [],
    days: [RewindDay] = []
  ) {
    self.period = period
    self.start = start
    self.end = end
    self.timezone = timezone
    self.plays = plays
    self.minutes = minutes
    self.topSongs = topSongs
    self.topArtists = topArtists
    self.topAlbums = topAlbums
    self.newArtists = newArtists
    self.topMixes = topMixes
    self.days = days
  }
  public init(from decoder: Decoder) throws {
    let values = try decoder.container(keyedBy: CodingKeys.self)
    period = try values.decode(String.self, forKey: .period)
    start = try values.decode(Int.self, forKey: .start)
    end = try values.decode(Int.self, forKey: .end)
    timezone = try values.decode(String.self, forKey: .timezone)
    plays = try values.decode(Int.self, forKey: .plays)
    minutes = try values.decode(Int.self, forKey: .minutes)
    topSongs = try values.decode([RewindSong].self, forKey: .topSongs)
    topArtists = try values.decode([RewindArtist].self, forKey: .topArtists)
    topAlbums = try values.decode([RewindAlbum].self, forKey: .topAlbums)
    newArtists = try values.decodeIfPresent([String].self, forKey: .newArtists) ?? []
    topMixes = try values.decodeIfPresent([RewindMix].self, forKey: .topMixes) ?? []
    days = try values.decodeIfPresent([RewindDay].self, forKey: .days) ?? []
  }

  /// Tapping a top song plays it, then the top songs after it in order,
  /// leaving out those that can no longer play (#216). Nil when the tapped
  /// song itself can't play.
  public func queue(from index: Int) -> [Track]? {
    guard topSongs.indices.contains(index), topSongs[index].available else { return nil }
    return topSongs[index...].filter(\.available).map(\.track)
  }

  /// The Home card's seven bars, Monday to Sunday: the week's days so far,
  /// then empty days still to come (#216).
  public var weekBars: [RewindBar] {
    let letters = ["M", "T", "W", "T", "F", "S", "S"]
    let names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    let shown = Array(days.prefix(7))
    let heights = RewindBar.heights(shown.map(\.minutes))
    return (0..<7).map { index in
      RewindBar(
        letter: letters[index], day: names[index],
        minutes: index < shown.count ? shown[index].minutes : nil,
        height: index < shown.count ? heights[index] : 0)
    }
  }
}

/// One local calendar day of a Rewind period.
public struct RewindDay: Codable, Sendable, Hashable {
  /// Epoch seconds of the day's local midnight.
  public let start: Int
  public let minutes: Int
  public let plays: Int
  public init(start: Int, minutes: Int, plays: Int) {
    self.start = start
    self.minutes = minutes
    self.plays = plays
  }
}

/// One of the Home card's day bars.
public struct RewindBar: Sendable, Hashable {
  public let letter: String
  public let day: String
  /// Nil for a day still to come.
  public let minutes: Int?
  /// 0...1, relative to the busiest day.
  public let height: Double
  /// The least a day with listening is drawn at, so it never looks empty.
  public static let least = 0.06
  /// Each value relative to the largest: 0 for nothing, never below
  /// `least` for something.
  public static func heights(_ values: [Int]) -> [Double] {
    let most = values.max() ?? 0
    guard most > 0 else { return values.map { _ in 0 } }
    return values.map { $0 <= 0 ? 0 : max(least, Double($0) / Double(most)) }
  }
}

/// Where a Rewind album or artist opens (#216): the library page when the
/// server matched one, else the catalogue page, else nowhere.
public enum RewindLink: Sendable, Hashable {
  /// A library album, with the catalogue release to fall back to.
  case libraryAlbum(id: String, release: String?)
  case release(String)
  /// A library artist, with the catalogue artist to fall back to.
  case libraryArtist(id: String, musicbrainz: String?)
  case catalogueArtist(String)
}

/// A Rewind cover address, as an artwork the app can load.
public enum RewindCover: Sendable, Hashable {
  case stored(String)
  case release(String)
  public init?(_ url: String?) {
    guard let url else { return nil }
    let path = url.split(separator: "?", maxSplits: 1).first.map(String.init) ?? url
    let release = "/api/v1/catalogue/artwork/release/"
    let stored = "/api/v1/covers/"
    if path.hasPrefix(release), path.count > release.count {
      self = .release(String(path.dropFirst(release.count)))
    } else if path.hasPrefix(stored), path.count > stored.count {
      let name = String(path.dropFirst(stored.count))
      self = .stored(name.hasSuffix(".jpg") ? String(name.dropLast(4)) : name)
    } else {
      return nil
    }
  }
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
  /// The library artist (`Artist.id`) when the artist is in the library.
  public let libraryId: String?
  public let musicbrainzArtistId: String?
  /// First played this period.
  public let new: Bool
  /// The artist's most played album cover this period.
  public let coverUrl: String?
  public var id: String { name }
  private enum CodingKeys: String, CodingKey {
    case name, plays, minutes, libraryId = "id", musicbrainzArtistId, new, coverUrl
  }
  public init(
    name: String, plays: Int, minutes: Int, libraryId: String? = nil,
    musicbrainzArtistId: String? = nil, new: Bool = false, coverUrl: String? = nil
  ) {
    self.name = name
    self.plays = plays
    self.minutes = minutes
    self.libraryId = libraryId
    self.musicbrainzArtistId = musicbrainzArtistId
    self.new = new
    self.coverUrl = coverUrl
  }
  public init(from decoder: Decoder) throws {
    let values = try decoder.container(keyedBy: CodingKeys.self)
    name = try values.decode(String.self, forKey: .name)
    plays = try values.decode(Int.self, forKey: .plays)
    minutes = try values.decode(Int.self, forKey: .minutes)
    libraryId = try values.decodeIfPresent(String.self, forKey: .libraryId)
    musicbrainzArtistId = try values.decodeIfPresent(String.self, forKey: .musicbrainzArtistId)
    new = try values.decodeIfPresent(Bool.self, forKey: .new) ?? false
    coverUrl = try values.decodeIfPresent(String.self, forKey: .coverUrl)
  }
  public var link: RewindLink? {
    if let libraryId, !libraryId.isEmpty {
      return .libraryArtist(id: libraryId, musicbrainz: musicbrainzArtistId.nonEmpty)
    }
    return musicbrainzArtistId.nonEmpty.map(RewindLink.catalogueArtist)
  }
}

public struct RewindAlbum: Codable, Sendable, Identifiable {
  public let title: String
  public let artist: String
  public let plays: Int
  public let coverUrl: String?
  /// The library album (`Album.id`, perhaps a requested album).
  public let libraryId: String?
  public let musicbrainzReleaseId: String?
  public var id: String { title + "\u{1F}" + artist }
  private enum CodingKeys: String, CodingKey {
    case title, artist, plays, coverUrl, libraryId = "id", musicbrainzReleaseId
  }
  public init(
    title: String, artist: String, plays: Int, coverUrl: String? = nil,
    libraryId: String? = nil, musicbrainzReleaseId: String? = nil
  ) {
    self.title = title
    self.artist = artist
    self.plays = plays
    self.coverUrl = coverUrl
    self.libraryId = libraryId
    self.musicbrainzReleaseId = musicbrainzReleaseId
  }
  public init(from decoder: Decoder) throws {
    let values = try decoder.container(keyedBy: CodingKeys.self)
    title = try values.decode(String.self, forKey: .title)
    artist = try values.decode(String.self, forKey: .artist)
    plays = try values.decode(Int.self, forKey: .plays)
    coverUrl = try values.decodeIfPresent(String.self, forKey: .coverUrl)
    libraryId = try values.decodeIfPresent(String.self, forKey: .libraryId)
    musicbrainzReleaseId = try values.decodeIfPresent(String.self, forKey: .musicbrainzReleaseId)
  }
  public var link: RewindLink? {
    if let libraryId, !libraryId.isEmpty {
      return .libraryAlbum(id: libraryId, release: musicbrainzReleaseId.nonEmpty)
    }
    return musicbrainzReleaseId.nonEmpty.map(RewindLink.release)
  }
}

public struct RewindMix: Codable, Sendable, Identifiable {
  public let title: String
  public let plays: Int
  /// The most played edition still kept, and its mix kind.
  public let edition: String?
  public let kind: String?
  public var id: String { title }
  private enum CodingKeys: String, CodingKey { case title, plays, edition = "id", kind }
  public init(title: String, plays: Int, edition: String? = nil, kind: String? = nil) {
    self.title = title
    self.plays = plays
    self.edition = edition
    self.kind = kind
  }
  public init(from decoder: Decoder) throws {
    let values = try decoder.container(keyedBy: CodingKeys.self)
    title = try values.decode(String.self, forKey: .title)
    plays = try values.decode(Int.self, forKey: .plays)
    edition = try values.decodeIfPresent(String.self, forKey: .edition)
    kind = try values.decodeIfPresent(String.self, forKey: .kind)
  }
  /// The cover drawing and palette for this mix: its kind, or the daily
  /// mix's for a saved edition or an older server.
  public var coverKind: String {
    guard let kind, !kind.isEmpty, !kind.hasPrefix("saved:") else { return "daily" }
    return kind
  }
}

extension Optional where Wrapped == String {
  fileprivate var nonEmpty: String? {
    guard let self, !self.isEmpty else { return nil }
    return self
  }
}
