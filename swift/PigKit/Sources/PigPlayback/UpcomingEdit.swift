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
  static func decide<Item: Equatable>(
    version: Int, lastVersion: Int?, old: [Item], new: [Item], current: Int,
    oldMode: PlaybackRepeat, newMode: PlaybackRepeat,
    prepared: Int?, fetching: Int?, fetchIntact: Bool, betweenSongs: Bool
  ) -> Decision {
    if let lastVersion, version <= lastVersion { return .stale }
    // Everything up to the song being decoded is already audio: unchanged or too late.
    guard old.indices.contains(current), new.indices.contains(current),
      new[...current] == old[...current]
    else { return .tooLate }
    if betweenSongs { return .keep }
    let next = following(current, count: new.count, mode: newMode)
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
