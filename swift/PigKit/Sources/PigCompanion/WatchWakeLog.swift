import Foundation

/// When watchOS gave the Watch app time, and what it did with it (#219), kept
/// on the Watch for its diagnostics: launches in the background and the
/// foreground, WatchConnectivity background tasks (and whether anything was
/// waiting for them), scheduled refreshes asked for and run, and songs
/// received in the inbox against songs accepted, with the gap between. It
/// tells "never woken" from "woken but didn't finish accepting".
///
/// Saved on the Watch, so a background launch adds to the same record. Also
/// keeps what the scheduled refresh rule needs (`WatchRefreshPlan`): when the
/// person last opened the app, when a song was last accepted, and the refresh
/// still pending.
public struct WatchWakeLog: Codable, Sendable, Equatable {
  /// Events older than this are dropped.
  public static let keep: TimeInterval = 7 * 86400
  /// Events kept at most, newest last.
  public static let capacity = 1000
  /// Wakes listed one per line at the end of the summary.
  public static let recentLines = 10

  public enum Kind: String, Codable, Sendable {
    /// The app was launched; `active` false when in the background.
    case launched
    /// The person opened the app (it became active).
    case opened
    /// watchOS ran a WatchConnectivity background task; `pending` whether
    /// songs or messages were waiting for it.
    case linkTask
    /// watchOS ran a scheduled background refresh.
    case refreshRan
    /// A background refresh was asked for, `scheduled` the preferred time.
    case refreshAsked
    /// watchOS turned the request down.
    case refreshRefused
    /// A song file reached the inbox.
    case received
    /// A song was accepted; `gap` seconds after it reached the inbox,
    /// `active` whether the app was active then.
    case accepted
  }
  public struct Event: Codable, Sendable, Equatable {
    public var at: Date
    public var kind: Kind
    public var active: Bool?
    public var pending: Bool?
    public var gap: TimeInterval?
    public var scheduled: Date?
  }

  public private(set) var events: [Event] = []
  /// When the person last opened the app.
  public private(set) var lastOpened: Date?
  /// When a song was last accepted.
  public private(set) var lastAccepted: Date?
  /// The preferred time of the refresh asked for and not yet run.
  public private(set) var pendingRefresh: Date?
  public init() {}

  /// A launch not in the background is the person opening the app.
  public mutating func launched(background: Bool, at date: Date = Date()) {
    if !background { lastOpened = date }
    note(Event(at: date, kind: .launched, active: !background))
  }
  public mutating func opened(at date: Date = Date()) {
    lastOpened = date
    note(Event(at: date, kind: .opened))
  }
  public mutating func linkTask(pending: Bool, at date: Date = Date()) {
    note(Event(at: date, kind: .linkTask, pending: pending))
  }
  public mutating func refreshRan(at date: Date = Date()) {
    pendingRefresh = nil
    note(Event(at: date, kind: .refreshRan))
  }
  public mutating func refreshAsked(for scheduled: Date, at date: Date = Date()) {
    pendingRefresh = scheduled
    note(Event(at: date, kind: .refreshAsked, scheduled: scheduled))
  }
  public mutating func refreshRefused(at date: Date = Date()) {
    pendingRefresh = nil
    note(Event(at: date, kind: .refreshRefused))
  }
  public mutating func received(at date: Date = Date()) {
    note(Event(at: date, kind: .received))
  }
  /// A song accepted at `date`; `receivedAt` when it reached the inbox, if known.
  public mutating func accepted(receivedAt: Date?, active: Bool, at date: Date = Date()) {
    lastAccepted = date
    let gap = receivedAt.map { max(0, date.timeIntervalSince($0)) }
    note(Event(at: date, kind: .accepted, active: active, gap: gap))
  }
  private mutating func note(_ event: Event) {
    events.append(event)
    events.removeAll { event.at.timeIntervalSince($0.at) > Self.keep }
    if events.count > Self.capacity { events.removeFirst(events.count - Self.capacity) }
  }
  /// How many events of `kind` in the last seven days (and none from a clock
  /// set far ahead) match `filter`.
  public func count(
    _ kind: Kind, now: Date = Date(), where filter: (Event) -> Bool = { _ in true }
  ) -> Int {
    recent(now: now).filter { $0.kind == kind && filter($0) }.count
  }
  private func recent(now: Date) -> [Event] {
    events.filter { now.timeIntervalSince($0.at) <= Self.keep }
  }

  /// The Watch's wake lines, at the top of the log it sends the iPhone.
  public func summary(now: Date = Date()) -> String {
    func time(_ date: Date) -> String { date.formatted(.iso8601) }
    let link = count(.linkTask, now: now)
    let refresh = count(.refreshRan, now: now)
    let launches = count(.launched, now: now)
    let background = count(.launched, now: now) { $0.active == false }
    let received = count(.received, now: now)
    let accepted = count(.accepted, now: now)
    let away = count(.accepted, now: now) { $0.active == false }
    var lines = [
      "Woken in the background: \(link + refresh) times in the last 7 days "
        + "(\(link) by the iPhone link, \(refresh) by a scheduled refresh).",
      "iPhone link wakes with something waiting: \(count(.linkTask, now: now) { $0.pending == true }) of \(link).",
      "App started: \(launches) times (\(background) in the background); opened by you: \(count(.opened, now: now)) times.",
      "Scheduled refreshes asked for: \(count(.refreshAsked, now: now))"
        + (count(.refreshRefused, now: now) > 0
          ? ", turned down by watchOS: \(count(.refreshRefused, now: now))" : "")
        + (pendingRefresh.map { "; next for \(time($0))." } ?? "; none pending."),
      "Songs received: \(received); songs accepted: \(accepted) (\(away) while the app was not open).",
    ]
    let gaps = recent(now: now).compactMap { $0.kind == .accepted ? $0.gap : nil }
    if !gaps.isEmpty {
      lines.append(
        "Received to accepted: median \(WatchDeliveryLog.duration(WatchDeliveryLog.median(gaps))), "
          + "longest \(WatchDeliveryLog.duration(gaps.max() ?? 0)).")
    }
    if let lastOpened { lines.append("Last opened: \(time(lastOpened)).") }
    if let lastAccepted { lines.append("Last song accepted: \(time(lastAccepted)).") }
    let wakes = recent(now: now).filter { $0.kind == .linkTask || $0.kind == .refreshRan }
    for event in wakes.suffix(Self.recentLines) {
      let what =
        event.kind == .refreshRan
        ? "scheduled refresh"
        : "iPhone link" + (event.pending == true ? ", something waiting" : ", nothing waiting")
      lines.append("\(time(event.at)) woken: \(what)")
    }
    return lines.joined(separator: "\n")
  }
}
