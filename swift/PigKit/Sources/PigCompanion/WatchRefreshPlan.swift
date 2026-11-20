import Foundation

/// When the Watch app asks watchOS to wake it to take songs the iPhone has
/// handed to the link (#219), with D-113's stop: only while songs are
/// waiting, about every half hour (watchOS picks the real time), and never
/// once two hours pass without a newly accepted song, counted from the later
/// of the last newly ready song and the last time the person had the app
/// open. Once that has happened the stop holds (`WatchWakeLog` keeps it)
/// until the app is opened again: a song taken later in the background never
/// restarts it (Codex round 1 on batch G).
public enum WatchRefreshPlan: Equatable, Sendable {
  /// Ask for nothing: no refresh is pending after this.
  case none
  /// Ask for a refresh at this preferred time.
  case at(Date)

  /// How far out each refresh is asked for.
  public static let interval: TimeInterval = 30 * 60
  /// How long refreshes go on without a new song (D-113's two hours).
  public static let quietLimit = WatchBackgroundWork.quietLimit
  /// A mark this far ahead of the clock is from a clock since set back.
  public static let clockSlack = WatchBackgroundWork.clockSlack

  /// The rule. `waiting`: songs the current manifest lists that are not yet
  /// ready on the Watch. A mark more than `clockSlack` ahead of `now` is
  /// ignored, so a clock set back can't stop refreshes until it catches up
  /// or keep them going past the limit; with neither mark usable, none.
  /// `stopped`: the window ran out earlier and the app hasn't been opened since.
  public static func next(
    now: Date, waiting: Int, lastAccepted: Date?, lastOpened: Date?, stopped: Bool = false
  ) -> WatchRefreshPlan {
    guard waiting > 0, !stopped,
      windowOpen(now: now, lastAccepted: lastAccepted, lastOpened: lastOpened)
    else { return .none }
    return .at(now.addingTimeInterval(interval))
  }
  /// Whether less than `quietLimit` has passed since the later usable mark.
  public static func windowOpen(now: Date, lastAccepted: Date?, lastOpened: Date?) -> Bool {
    let usable = [lastAccepted, lastOpened].compactMap { $0 }.filter {
      $0.timeIntervalSince(now) < clockSlack
    }
    return WatchBackgroundWork.allows(
      now: now, leftForeground: usable.max(), lastReceived: nil, stopped: false)
  }

  /// One pending refresh at a time: the time to ask for now, or nil when
  /// nothing should be asked. A pending refresh is kept while it is still
  /// due (not more than `quietLimit` overdue, and not further ahead than a
  /// fresh request would be, which only a clock set back makes it).
  public static func request(
    _ plan: WatchRefreshPlan, pending: Date?, now: Date
  ) -> Date? {
    guard case .at(let date) = plan else { return nil }
    if let pending {
      let ahead = pending.timeIntervalSince(now)
      if ahead > -quietLimit, ahead <= interval + clockSlack { return nil }
    }
    return date
  }
}
