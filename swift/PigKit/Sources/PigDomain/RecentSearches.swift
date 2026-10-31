import Foundation

/// Something opened or played from search, shown under the empty search box
/// (D-61). Only what a person picked is kept, never the words they typed.
public enum RecentPick: Codable, Sendable, Hashable, Identifiable {
  case song(Track)
  case album(Album)
  case artist(Artist)
  case catalogueArtist(id: String, name: String)
  public var id: String {
    switch self {
    case .song(let track): "song:" + track.id
    case .album(let album): "album:" + album.id
    case .artist(let artist): "artist:" + artist.id
    case .catalogueArtist(let id, _): "catalogue-artist:" + id
    }
  }
}

/// The newest picks first, each once, at most `limit`.
public struct RecentSearches: Codable, Sendable, Hashable {
  public static let limit = 20
  public private(set) var picks: [RecentPick] = []
  public init(picks: [RecentPick] = []) {
    self.picks = Array(picks.prefix(Self.limit))
  }
  public mutating func add(_ pick: RecentPick) {
    picks.removeAll { $0.id == pick.id }
    picks.insert(pick, at: 0)
    if picks.count > Self.limit { picks.removeLast(picks.count - Self.limit) }
  }
  public mutating func clear() { picks = [] }
}
