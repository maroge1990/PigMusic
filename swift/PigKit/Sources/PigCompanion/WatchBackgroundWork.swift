import Foundation

/// Watch copies prepared while the iPhone app is away, and the rule that ends
/// that work on its own (D-113, #219). After the app leaves the foreground the
/// iPhone may hand Watch copies to the background downloader, send finished
/// ones to the Watch and ask iOS for a background refresh, but only while the
/// Watch keeps receiving songs: once two hours pass with no new song
/// confirmed by the Watch (counted from when the app left, or from the last
/// song the Watch confirmed, whichever is later), it starts nothing further
/// in the background until PigMusic is opened again. The stop holds even if
/// a confirmation comes in later; only the foreground lifts it.
///
/// Saved on the phone, so a launch for a background refresh or download
/// keeps the clock it left with; nothing recorded counts as stopped. Also
/// keeps the times of the background events for the diagnostics export.
public struct WatchBackgroundWork: Codable, Sendable, Equatable {
  /// How long background work goes on without the Watch receiving a song.
  public static let quietLimit: TimeInterval = 2 * 60 * 60
  /// A clock this far behind the last mark has been changed: work stops.
  public static let clockSlack: TimeInterval = 5 * 60
  /// Background events kept for the export, newest last.
  public static let eventCapacity = 200

  public enum Kind: String, Codable, Sendable {
    /// The app left the foreground; the clock starts.
    case left
    /// The Watch confirmed new songs (`count`).
    case received
    /// Copies handed to the background downloader (`count`), with `waiting`
    /// still being prepared by the server.
    case queued
    /// A background download finished (`count` copies ready on the iPhone).
    case finished
    /// Songs handed to the Watch link while the app was away (`count`).
    case sent
    /// iOS was asked for a background refresh.
    case refreshAsked
    /// iOS ran a background refresh.
    case refreshRan
    /// The two-hour limit was reached: nothing more starts in the background.
    case stopped
    /// The app came back to the foreground; the stop is lifted.
    case resumed
  }
  public struct Event: Codable, Sendable, Equatable {
    public var at: Date
    public var kind: Kind
    public var count: Int?
    public var waiting: Int?
  }

  /// When the app last left the foreground; nil while it is there.
  public private(set) var leftForeground: Date?
  /// When the phone last learned of a song new on the Watch.
  public private(set) var lastReceived: Date?
  /// When the limit stopped background work; nil until it does.
  public private(set) var stoppedAt: Date?
  public private(set) var events: [Event] = []
  public init() {}

  /// The rule itself. Background work may start only while nothing stopped
  /// it, and less than `quietLimit` has passed since the later of the app
  /// leaving and the Watch's last new song. With neither recorded, or a
  /// clock set back, it may not.
  public static func allows(
    now: Date, leftForeground: Date?, lastReceived: Date?, stopped: Bool
  ) -> Bool {
    guard !stopped, let since = [leftForeground, lastReceived].compactMap({ $0 }).max() else {
      return false
    }
    let quiet = now.timeIntervalSince(since)
    return quiet > -clockSlack && quiet < quietLimit
  }

  /// Whether background work may start now. The first refusal records the
  /// stop, which lasts until `entered(at:)`.
  public mutating func mayStart(at now: Date = Date()) -> Bool {
    latchExpiry(at: now)
  }
  /// Judges the clock as it stands at `now` and records the stop the first
  /// time it has run out; returns whether work may start.
  @discardableResult private mutating func latchExpiry(at now: Date) -> Bool {
    if Self.allows(
      now: now, leftForeground: leftForeground, lastReceived: lastReceived,
      stopped: stoppedAt != nil)
    {
      return true
    }
    if stoppedAt == nil {
      stoppedAt = now
      note(.stopped, at: now)
    }
    return false
  }
  /// Whether the limit has stopped background work (without deciding again).
  public var isStopped: Bool { stoppedAt != nil }

  public mutating func left(at date: Date = Date()) {
    leftForeground = date
    stoppedAt = nil
    note(.left, at: date)
  }
  public mutating func entered(at date: Date = Date()) {
    let wasAway = leftForeground != nil || stoppedAt != nil
    leftForeground = nil
    stoppedAt = nil
    if wasAway { note(.resumed, at: date) }
  }
  /// The Watch confirmed `count` songs it didn't have. Restarts the clock,
  /// but never lifts a stop. `away`: the app is not in the foreground (by
  /// default, when it left and hasn't come back). While away, the old clock
  /// is judged first: iOS may have kept the app suspended past the limit
  /// with nothing asking, and a confirmation coming in later must not
  /// restart work the limit had already ended (Codex round 1 on batch 24).
  public mutating func received(_ count: Int, at date: Date = Date(), away: Bool? = nil) {
    guard count > 0 else { return }
    if away ?? (leftForeground != nil) { latchExpiry(at: date) }
    lastReceived = max(lastReceived ?? date, date)
    if leftForeground != nil { note(.received, count: count, at: date) }
  }
  public mutating func note(_ kind: Kind, count: Int? = nil, waiting: Int? = nil, at date: Date = Date())
  {
    events.append(Event(at: date, kind: kind, count: count, waiting: waiting))
    if events.count > Self.eventCapacity { events.removeFirst(events.count - Self.eventCapacity) }
  }

  /// The "Watch background work" lines of the diagnostics export.
  public func summary(now: Date = Date()) -> String {
    func time(_ date: Date) -> String { date.formatted(.iso8601) }
    var lines = [
      "Limit: background work stops after \(Int(Self.quietLimit / 3600)) h without a new song on Apple Watch."
    ]
    if let stoppedAt {
      lines.append(
        "Stopped at \(time(stoppedAt)): nothing more starts in the background until PigMusic is opened.")
    } else if let leftForeground {
      let since = max(leftForeground, lastReceived ?? leftForeground)
      lines.append(
        "In the background since \(time(leftForeground)); background work may start until "
          + "\(time(since.addingTimeInterval(Self.quietLimit))).")
    } else {
      lines.append("The app is in the foreground.")
    }
    if let lastReceived { lines.append("Last new song confirmed by Apple Watch: \(time(lastReceived)).") }
    if events.isEmpty { lines.append("No background events yet.") }
    for event in events {
      lines.append("\(time(event.at)) \(Self.describe(event))")
    }
    return lines.joined(separator: "\n")
  }
  static func describe(_ event: Event) -> String {
    let count = event.count ?? 0
    switch event.kind {
    case .left: return "app left the foreground"
    case .received: return "Apple Watch confirmed \(count) new songs"
    case .queued:
      return "\(count) copies queued for background download, \(event.waiting ?? 0) still being prepared"
    case .finished: return "background download finished (\(count) copies ready on the iPhone)"
    case .sent: return "\(count) songs handed to the Apple Watch link"
    case .refreshAsked: return "background refresh asked of iOS"
    case .refreshRan: return "background refresh ran"
    case .stopped: return "stopped by the 2-hour limit"
    case .resumed: return "app opened: background work allowed again"
    }
  }
}
