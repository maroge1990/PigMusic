import Foundation
import PigDomain

public struct PlaybackOccurrence: Identifiable, Sendable, Equatable, Codable {
  public let id: UUID
  public let track: Track
  public init(track: Track, id: UUID = UUID()) {
    self.id = id
    self.track = track
  }
}

public enum PlaybackRepeat: String, Codable, Sendable, CaseIterable {
  case off, all, one
}

/// A song taken out of Up next, with where it was and from which queue.
public struct TakenOccurrence: Sendable, Equatable {
  public let item: PlaybackOccurrence
  public let position: Int
  public let queue: UUID
}

public struct PlaybackQueue: Sendable, Codable {
  public private(set) var items: [PlaybackOccurrence] = []
  public private(set) var index: Int = 0
  /// This queue's identity: new each time it is replaced (playing something
  /// else, Stop) or read back from a checkpoint; kept while it advances or
  /// is reordered. An Up next Undo acts only on the queue it came from.
  public private(set) var identity = UUID()
  public var current: PlaybackOccurrence? { items.indices.contains(index) ? items[index] : nil }
  private enum CodingKeys: String, CodingKey { case items, index }
  public init() {}
  /// The songs the queue added itself when it ran out (similar songs,
  /// #202), by occurrence: one you add later is never taken for them, even
  /// the same song (Codex round 1 on batch E). Not kept in a checkpoint.
  public private(set) var recommended: Set<UUID> = []
  public func isRecommended(_ id: UUID) -> Bool { recommended.contains(id) }
  public mutating func replace(_ tracks: [Track], startingAt: Int = 0) {
    identity = UUID()
    recommended = []
    items = tracks.map { PlaybackOccurrence(track: $0) }
    index = min(max(0, startingAt), max(0, items.count - 1))
  }
  /// Marks every song now in the queue as added by the queue itself: similar
  /// songs played as a fresh queue.
  public mutating func markAllRecommended() {
    recommended = Set(items.map(\.id))
  }
  public mutating func select(index: Int) {
    guard items.indices.contains(index) else { return }
    self.index = index
  }
  /// `recommended`: the queue adds these itself, at the very end. A song you
  /// add (Add to queue) goes before them, at the end of your own songs.
  public mutating func append(_ tracks: [Track], next: Bool = false, recommended: Bool = false) {
    let additions = tracks.map { PlaybackOccurrence(track: $0) }
    if recommended {
      items.append(contentsOf: additions)
      self.recommended.formUnion(additions.map(\.id))
    } else if next && current != nil {
      items.insert(contentsOf: additions, at: index + 1)
    } else if let first = items.indices.first(where: {
      $0 > index && self.recommended.contains(items[$0].id)
    }) {
      items.insert(contentsOf: additions, at: first)
    } else {
      items.append(contentsOf: additions)
    }
  }
  public mutating func removeUpcoming(_ id: UUID) {
    guard let position = items.firstIndex(where: { $0.id == id }), position > index else { return }
    items.remove(at: position)
  }
  /// Takes a song out of Up next at once (D-79, D-118: it can never become
  /// current while its Undo waits) and says where it was, for `putBack`.
  public mutating func takeUpcoming(_ id: UUID) -> TakenOccurrence? {
    guard let position = items.firstIndex(where: { $0.id == id }), position > index else {
      return nil
    }
    return TakenOccurrence(item: items.remove(at: position), position: position, queue: identity)
  }
  /// Whether `taken` came from this queue, so its Undo can still put it back.
  public func holds(_ taken: TakenOccurrence) -> Bool { taken.queue == identity }
  /// Undo of `takeUpcoming`: the song returns to its old place while that is
  /// still in Up next, else to the front of Up next. Never twice, and never
  /// into another queue (Codex round 1 on batch D): false when it didn't.
  @discardableResult public mutating func putBack(_ taken: TakenOccurrence) -> Bool {
    guard holds(taken), !items.contains(where: { $0.id == taken.item.id }) else { return false }
    let position = taken.position
    let place = position > index && position <= items.count ? position : min(index + 1, items.count)
    items.insert(taken.item, at: place)
    return true
  }
  public mutating func moveUpcoming(_ id: UUID, by delta: Int) {
    guard let position = items.firstIndex(where: { $0.id == id }), position > index else { return }
    let destination = position + delta
    guard destination > index, items.indices.contains(destination) else { return }
    let item = items.remove(at: position)
    items.insert(item, at: destination)
  }
  public mutating func renewOccurrence(at index: Int, id: UUID) {
    guard items.indices.contains(index) else { return }
    items[index] = PlaybackOccurrence(track: items[index].track, id: id)
  }
  public mutating func shuffleUpcoming() {
    guard index + 1 < items.count else { return }
    let upcoming = items[(index + 1)...].shuffled()
    items.replaceSubrange((index + 1)..., with: upcoming)
  }
  public mutating func restoreUpcoming(_ original: [PlaybackOccurrence]) {
    let upcoming = Array(items.dropFirst(index + 1))
    let ids = Set(upcoming.map(\.id))
    let restored = original.filter { ids.contains($0.id) }
    let known = Set(restored.map(\.id))
    items = Array(items.prefix(index + 1)) + restored + upcoming.filter { !known.contains($0.id) }
  }
  @discardableResult public mutating func next() -> Bool {
    guard index + 1 < items.count else { return false }
    index += 1
    return true
  }
  /// Returns true when Previous should restart the current occurrence.
  public mutating func previous(elapsed: TimeInterval) -> Bool {
    if elapsed > 3 || index == 0 { return true }
    index -= 1
    return false
  }
}

public enum TransitionMode: Sendable, Equatable {
  case gapless
  case crossfade(seconds: Double)
}

public enum TransitionPlanner {
  /// Stable album identity and sequential numbering preserve continuous albums, including discs.
  public static func albumOrder(outgoing: Track, incoming: Track) -> Bool {
    guard let album = outgoing.albumId, !album.isEmpty, incoming.albumId == album,
      let track = outgoing.trackNumber, let next = incoming.trackNumber, track > 0, track < Int.max,
      next > 0,
      outgoing.id != incoming.id
    else { return false }
    let disc = outgoing.discNumber ?? 1
    let nextDisc = incoming.discNumber ?? 1
    guard disc > 0, disc < Int.max, nextDisc > 0 else { return false }
    return (disc == nextDisc && next == track + 1) || (nextDisc == disc + 1 && next == 1)
  }
  public static func mode(crossfade: Bool, albumOrder: Bool, duration: Double = 3) -> TransitionMode
  {
    crossfade && !albumOrder ? .crossfade(seconds: min(12, max(0, duration))) : .gapless
  }
  /// Linear complementary gains avoid amplifying correlated signals above unity.
  public static func gains(progress: Double) -> (outgoing: Float, incoming: Float) {
    let value = min(1, max(0, progress))
    return (Float(1 - value), Float(value))
  }
  /// Offline reference renderer used to measure boundaries and overlap before choosing a live engine.
  public static func render(_ outgoing: [Float], _ incoming: [Float], overlapFrames: Int) -> [Float]
  {
    let overlap = min(max(0, overlapFrames), min(outgoing.count, incoming.count))
    guard overlap > 0 else { return outgoing + incoming }
    var result = Array(outgoing.dropLast(overlap))
    for frame in 0..<overlap {
      let gain = gains(progress: overlap == 1 ? 0.5 : Double(frame) / Double(overlap - 1))
      result.append(
        outgoing[outgoing.count - overlap + frame] * gain.outgoing + incoming[frame] * gain.incoming
      )
    }
    result.append(contentsOf: incoming.dropFirst(overlap))
    return result
  }
}

public struct PlaybackCheckpoint: Codable, Sendable {
  public let queue: PlaybackQueue
  public let elapsed: Double
  public init(queue: PlaybackQueue, elapsed: Double) {
    self.queue = queue
    self.elapsed = elapsed.isFinite ? max(0, elapsed) : 0
  }
}
public struct PlaybackPosition: Codable, Sendable {
  public let index: Int
  public let elapsed: Double
  public init(index: Int, elapsed: Double) {
    self.index = index
    self.elapsed = elapsed
  }
}
