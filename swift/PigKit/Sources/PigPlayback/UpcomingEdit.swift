import Foundation
import PigDomain

/// Which queue entry an occurrence id stands for (#279). Each listening of
/// a repeated song gets a fresh id; the iPhone's queue takes it (its
/// PlayerController renews the entry from each snapshot) while the Watch's
/// keeps the first. Every id an entry has gone by names the same entry, so
/// an edit from either is recognised. Ids map to the entry's first id.
public struct OccurrenceIdentity: Sendable, Equatable {
  private var roots: [UUID: UUID] = [:]
  public init() {}
  /// The entry's first id. Followed through: two owners may each have
  /// learnt one step of the chain (a merge joins them).
  func root(_ id: UUID) -> UUID {
    var current = id
    for _ in 0...roots.count {
      guard let next = roots[current], next != current else { return current }
      current = next
    }
    return current
  }
  /// `id` is a later listening of the entry `entry` names.
  mutating func record(_ id: UUID, as entry: UUID) {
    let root = root(entry)
    if id != root { roots[id] = root }
  }
  mutating func merge(_ other: OccurrenceIdentity) {
    roots.merge(other.roots) { mine, _ in mine }
  }
  /// What an edit compares: the entry, and its song (a fresh copy of a song
  /// keeps its entry but is another file to fetch).
  struct Key: Hashable {
    let entry: UUID
    let track: Track
  }
  func key(_ occurrence: PlaybackOccurrence) -> Key {
    Key(entry: root(occurrence.id), track: occurrence.track)
  }
}

/// What a playing queue stream did with an edit to the songs after the
/// current one (#279): took it while the current song plays on, or found
/// it too late (the next song is already joining the audio, or the songs up
/// to the one decoding changed), so the owner restarts from its position.
public enum UpcomingEdit: Sendable, Equatable {
  case applied, tooLate
}

/// The pure part of an upcoming edit (#279), kept apart from the audio so it
/// can be tested anywhere. The stream gives it what it holds; it says what
/// to do. Positions are queue positions; items are compared whole (a song's
/// fresh copy keeps its occurrence id but is a different song to fetch).
enum UpcomingEditRule {
  enum Decision: Equatable {
    /// An older edit arriving after a newer one: ignored.
    case stale
    /// Too late to change without a restart.
    case tooLate
    /// Take the new list; what is being fetched or prepared stays.
    case keep
    /// Take the new list, drop the fetch and choose the next song again.
    case refetch
  }

  /// The position that follows `position`, passing over songs already
  /// played in this pass (`skipping`, #303) until the order wraps round to
  /// a new pass, where every song plays again.
  static func following<Item: Hashable>(
    _ position: Int, in items: [Item], mode: PlaybackRepeat, skipping: Set<Item>
  ) -> Int? {
    var candidate = following(position, count: items.count, mode: mode)
    // Rises until it wraps (to 0, never above `position`) or runs out.
    while let next = candidate, next > position, skipping.contains(items[next]) {
      candidate = following(next, count: items.count, mode: mode)
    }
    return candidate
  }
  /// The position that follows `position`, as the stream plays it.
  static func following(_ position: Int, count: Int, mode: PlaybackRepeat) -> Int? {
    if mode == .one { return position }
    if position + 1 < count { return position + 1 }
    return mode == .all ? 0 : nil
  }

  /// - Parameters:
  ///   - current: the position the stream is decoding (it can be ahead of
  ///     the one heard).
  ///   - prepared: the next song's position once its source has arrived and
  ///     its decoder is opening or open (the transition is being built).
  ///   - fetching: the position the background fetch is for, if any.
  ///   - fetchIntact: the fetch is still for the song first chosen (nothing
  ///     passed over) and has not given up.
  ///   - betweenSongs: the stream is releasing a finished song and chooses
  ///     the next one itself right after, from whatever list it then has.
  ///   - skipped: songs already played in this pass, not chosen again.
  static func decide<Item: Hashable>(
    version: Int, lastVersion: Int?, old: [Item], new: [Item], current: Int,
    oldMode: PlaybackRepeat, newMode: PlaybackRepeat,
    prepared: Int?, fetching: Int?, fetchIntact: Bool, betweenSongs: Bool,
    skipped: Set<Item> = []
  ) -> Decision {
    if let lastVersion, version <= lastVersion { return .stale }
    // Everything up to the song being decoded is already audio: unchanged or too late.
    guard old.indices.contains(current), new.indices.contains(current),
      new[...current] == old[...current]
    else { return .tooLate }
    if betweenSongs { return .keep }
    let next = following(current, in: new, mode: newMode, skipping: skipped)
    // Repeat One plays gapless; any other mode may cross-fade.
    let sameTransition = (oldMode == .one) == (newMode == .one)
    func unchanged(_ position: Int) -> Bool {
      old.indices.contains(position) && new.indices.contains(position)
        && old[position] == new[position]
    }
    if let prepared {
      guard let next, next == prepared, sameTransition, unchanged(prepared) else {
        return .tooLate
      }
      return .keep
    }
    guard let next else { return fetching == nil ? .keep : .refetch }
    if fetchIntact, fetching == next, sameTransition, unchanged(next) { return .keep }
    return .refetch
  }
}

/// Where the queue restarts after an edit came too late (#279). The song
/// heard may already be past the one shown when the edit was made (the
/// `anchor`, a position in the edited queue). Songs the edit placed between
/// the two that the listener has not heard (Play next, or a song added after
/// a removed one) play first, from their start; only then the song heard,
/// from where it was. Neither app can be told of another order, so the
/// edited order stands: up to a clock tick of the song heard plays again
/// later from its start. A removed song is never chosen.
enum LateEditRestart {
  /// - Parameters:
  ///   - edited: the edited queue, by entry.
  ///   - heard: the heard song's position in it (nil: the edit removed it).
  ///   - played: entries heard between the anchor and the heard song.
  /// - Returns: the position to start and whether it keeps the heard
  ///   song's place in it; nil when nothing is left to play.
  static func point<Entry: Hashable>(
    edited: [Entry], anchor: Int, heard: Int?, played: Set<Entry>, repeatAll: Bool
  ) -> (index: Int, keepsPosition: Bool)? {
    if let heard, heard <= anchor { return (heard, true) }
    let stop = min(heard ?? edited.count, edited.count)
    if anchor + 1 < stop {
      for position in (anchor + 1)..<stop where !played.contains(edited[position]) {
        return (position, false)
      }
    }
    if let heard { return (heard, true) }
    if repeatAll, !edited.isEmpty { return (0, false) }
    return nil
  }
}
