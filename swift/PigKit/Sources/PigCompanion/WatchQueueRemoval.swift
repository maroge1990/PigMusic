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

  /// A queue still being built when removals were committed (Codex round 1
  /// on #252): the positions it keeps, where to start among them, and
  /// whether that is the chosen song (else it starts from the beginning of
  /// the next kept one, or the last kept before it). Nil when nothing is left.
  public static func reconcile(
    _ queue: [String], start: Int, removed: Set<String>
  ) -> (keep: [Int], start: Int, startKept: Bool)? {
    let keep = queue.indices.filter { !removed.contains(queue[$0]) }
    guard !keep.isEmpty else { return nil }
    if let index = keep.firstIndex(of: start) { return (keep, index, true) }
    if let index = keep.firstIndex(where: { $0 > start }) { return (keep, index, false) }
    return (keep, keep.count - 1, false)
  }
}

/// Songs whose removal was committed, kept while any queue that may still
/// hold them is being built (Codex round 2 on #252): a library that no
/// longer lists a song lets its mark go only once no queue is being built,
/// or a queue collected before the removal would keep the song.
public struct WatchRemovalMarks: Equatable, Sendable {
  public private(set) var removed: Set<String> = []
  private var building = 0
  private var library: Set<String>?
  public init() {}
  public mutating func commit(_ ids: some Sequence<String>) { removed.formUnion(ids) }
  /// A queue starts collecting songs.
  public mutating func beginBuilding() { building += 1 }
  /// That queue was published, or given up.
  public mutating func endBuilding() {
    building = max(0, building - 1)
    if building == 0, let library { prune(to: library) }
  }
  /// The library now lists these songs.
  public mutating func libraryChanged(to ids: Set<String>) {
    if building == 0 { prune(to: ids) } else { library = ids }
  }
  private mutating func prune(to ids: Set<String>) {
    removed.formIntersection(ids)
    library = nil
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
