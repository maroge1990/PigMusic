import Foundation

/// Whether the app's foreground work (live updates, download preparation,
/// Watch copying) runs, from the scene's activity (#97, #46).
///
/// The work starts at the first activation. Only a real trip to the
/// background stops it; a moment of Control Centre or a notification keeps
/// it running, and coming back from one restarts nothing unless the app was
/// away for `SceneReturn.longAway`.
public struct ForegroundGate: Sendable {
  public enum Change: Equatable, Sendable {
    /// Nothing starts or stops.
    case stay
    /// The app went to the background: stop the foreground work.
    case suspend
    /// The app is active again after `away`. `renewConnections` when it was
    /// away long enough for kept connections to be dead; `restart` when the
    /// foreground work starts again (and the library refreshes).
    case back(away: Duration, renewConnections: Bool, restart: Bool)
  }
  /// Whether the scene is active: live updates start only then.
  public private(set) var active = true
  /// Whether the foreground work is stopped; true until the first activation.
  public private(set) var suspended = true
  /// When the app last stopped being active.
  private var inactiveSince: ContinuousClock.Instant?
  public init() {}

  public mutating func change(
    active: Bool, background: Bool = false, now: ContinuousClock.Instant = .now
  ) -> Change {
    self.active = active
    if !active {
      if inactiveSince == nil { inactiveSince = now }
      guard background, !suspended else { return .stay }
      suspended = true
      return .suspend
    }
    let away = inactiveSince.map { now - $0 } ?? .zero
    inactiveSince = nil
    let restart = SceneReturn.restarts(away: away, suspended: suspended)
    if restart { suspended = false }
    return .back(away: away, renewConnections: away >= SceneReturn.longAway, restart: restart)
  }
}
