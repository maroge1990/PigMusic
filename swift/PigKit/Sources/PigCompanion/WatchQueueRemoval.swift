import Foundation
import PigDomain

/// What removing songs from the Watch does to what plays (#252). Removing
/// a song that isn't playing leaves playback alone and only takes it out of
/// the queue; removing the song that plays moves on to the next one.
public enum WatchQueueRemoval: Equatable, Sendable {
  /// No queued song is removed: nothing changes.
  case untouched
  /// The playing song keeps its place: the queue becomes `keep` (positions
  /// in the old queue) and plays on without a restart.
  case upcoming(keep: [Int])
  /// The playing song stays but earlier songs go, so it now sits at `index`
  /// of `keep`: continue it there, at the same point.
  case moved(keep: [Int], index: Int)
  /// The playing song goes: the next kept one, at `index` of `keep`, plays
  /// from its start.
  case advance(keep: [Int], index: Int)
  /// Nothing is left to play.
  case stop

  /// `queue`: song ids in play order; `current`: the playing position.
  public static func plan(
    queue: [String], current: Int, removing: Set<String>, repeatAll: Bool = false
  ) -> Self {
    let keep = queue.indices.filter { !removing.contains(queue[$0]) }
    guard keep.count != queue.count else { return .untouched }
    guard queue.indices.contains(current) else { return keep.isEmpty ? .stop : .upcoming(keep: keep) }
    if let index = keep.firstIndex(of: current) {
      return index == current ? .upcoming(keep: keep) : .moved(keep: keep, index: index)
    }
    if let index = keep.firstIndex(where: { $0 > current }) {
      return .advance(keep: keep, index: index)
    }
    if repeatAll, !keep.isEmpty { return .advance(keep: keep, index: 0) }
    return .stop
  }
}

/// The Watch's lists in the order the iPhone files them (#252): A to Z
/// ignoring case, accents and a leading "The", then by the second name.
public enum WatchListOrder {
  public static func precedes(
    _ left: (name: String, detail: String, id: String),
    _ right: (name: String, detail: String, id: String)
  ) -> Bool {
    switch left.name.sortingCompare(right.name) {
    case .orderedAscending: return true
    case .orderedDescending: return false
    case .orderedSame: break
    }
    switch left.detail.sortingCompare(right.detail) {
    case .orderedAscending: return true
    case .orderedDescending: return false
    case .orderedSame: return left.id < right.id
    }
  }
}
