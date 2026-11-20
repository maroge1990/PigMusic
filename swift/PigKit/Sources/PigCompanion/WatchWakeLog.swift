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
  /// Song ids remembered as already listed since the app was last opened.
  /// Once full, every id counts as seen: nothing more reopens the window.
  public static let listedCapacity = 3000
  /// Reopenings by new songs listed allowed between two openings of the app.
  public static let reopeningLimit = 6

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
    /// `active` whether the app was active then, `newSong` false for a copy
    /// the Watch already had.
    case accepted
    /// Two hours passed without a new song: no refresh is asked for until
    /// the app is opened.
    case refreshStopped
    /// A manifest listed songs the previous one didn't: the window reopens.
    case listed
    /// The iPhone's complication push arrived (it handed songs to the link,
    /// D-125); `active` whether the app was on screen. Only new songs in it
    /// move the window on (`phonePushed`).
    case phonePush
  }
  public struct Event: Codable, Sendable, Equatable {
    public var at: Date
    public var kind: Kind
    public var active: Bool?
    public var pending: Bool?
    public var gap: TimeInterval?
    public var scheduled: Date?
    public var newSong: Bool?
  }

  public private(set) var events: [Event] = []
  /// When the person last opened the app.
  public private(set) var lastOpened: Date?
  /// When a song last became ready (a copy the Watch already had doesn't count).
  public private(set) var lastAccepted: Date?
  /// The preferred time of the refresh asked for and not yet run.
  public private(set) var pendingRefresh: Date?
  /// When that refresh was asked for.
  public private(set) var pendingAskedAt: Date?
  /// When a manifest last listed songs the previous one didn't.
  public private(set) var lastListed: Date?
  /// When the 2-hour window ran out with songs waiting; nil while refreshes
  /// may be asked for. Only the app being opened or new songs listed clear it.
  public private(set) var refreshStoppedAt: Date?
  /// Saved optional, so a log saved before they existed still decodes.
  private var listedSeen: Set<String>?
  private var listedReopenings: Int?
  /// PigMusic complications on watch faces, as WidgetKit last said; nil
  /// until it has (D-125).
  public private(set) var faceComplications: Int?
  /// Song ids listed since the app was last opened (Codex round 2 on batch G).
  public var seenSinceOpened: Set<String> { listedSeen ?? [] }
  /// Times new songs reopened the window since the app was last opened.
  public var reopenings: Int { listedReopenings ?? 0 }
  public init() {}

  /// A launch not in the background is the person opening the app.
  public mutating func launched(background: Bool, at date: Date = Date()) {
    if !background { sawPerson(at: date) }
    note(Event(at: date, kind: .launched, active: !background))
  }
  public mutating func opened(at date: Date = Date()) {
    sawPerson(at: date)
    note(Event(at: date, kind: .opened))
  }
  /// The person was using the app until now (it is on screen, or just left
  /// it): the window counts from here and any stop is lifted.
  public mutating func sawPerson(at date: Date = Date()) {
    lastOpened = date
    refreshStoppedAt = nil
    listedSeen = nil
    listedReopenings = nil
  }
  /// A manifest was applied: `ids` it lists, `previous` those the library
  /// the Watch had applied listed. Songs listed in neither that library nor
  /// any other since the app was last opened are new songs the person sent:
  /// the window counts from here and any stop is lifted. So membership that
  /// comes and goes (A, B, A, B) reopens it at most once per song (Codex
  /// round 2 on batch G). Only reopening a window that had closed (stopped,
  /// or run out) uses one of the `reopeningLimit` reopenings allowed until
  /// the app is opened; new songs while it is open only move it on (#300).
  /// Returns whether the window moved on or reopened.
  @discardableResult public mutating func songsListed(
    _ ids: some Sequence<String>, previous: some Sequence<String>, at date: Date = Date()
  ) -> Bool {
    guard newSongs(Array(ids), alsoKnown: Array(previous), at: date) else { return false }
    note(Event(at: date, kind: .listed))
    return true
  }
  /// The iPhone's complication push (D-125). It always counts as a wake
  /// (the caller takes what the link has waiting), but it moves the window
  /// on, or reopens it, only with evidence of new songs: `songs`, the ids it
  /// handed over, judged by the same rule as a manifest's (each song once
  /// since the app was opened, the reopenings charged only when a stopped
  /// window reopens), and only when it is fresh: sent less than `pushMaxAge`
  /// before it arrived, and not further ahead than `clockSlack`. So a retry
  /// of songs already seen, or a push delivered late, keeps nothing awake
  /// (Codex round 1 on batch J). Returns whether the window moved on.
  @discardableResult public mutating func phonePushed(
    songs: [String], sentAt: Date, active: Bool, at date: Date = Date()
  ) -> Bool {
    note(Event(at: date, kind: .phonePush, active: active))
    let age = date.timeIntervalSince(sentAt)
    guard age < Self.pushMaxAge, age > -WatchRefreshPlan.clockSlack else { return false }
    return newSongs(songs, alsoKnown: [], at: date)
  }
  /// A push older than this when it arrives wakes but extends nothing.
  public static let pushMaxAge: TimeInterval = 30 * 60
  /// Songs in `ids` not in `alsoKnown` nor listed since the app was last
  /// opened move the window on (#300: a reopening is used only when a
  /// stopped window reopens). Once the memory is full nothing does.
  private mutating func newSongs(_ ids: [String], alsoKnown: [String], at date: Date) -> Bool {
    var seen = listedSeen ?? []
    let full = seen.count >= Self.listedCapacity
    let fresh = Set(ids).subtracting(alsoKnown).subtracting(seen)
    for id in alsoKnown + ids where seen.count < Self.listedCapacity { seen.insert(id) }
    listedSeen = seen
    guard !full, !fresh.isEmpty else { return false }
    if !windowOpen(at: date) {
      guard reopenings < Self.reopeningLimit else { return false }
      listedReopenings = reopenings + 1
    }
    lastListed = date
    refreshStoppedAt = nil
    return true
  }
  /// WidgetKit's count of PigMusic complications on watch faces.
  public mutating func complicationsOnFaces(_ count: Int) {
    faceComplications = max(0, count)
  }
  /// Whether refreshes may still be asked for: not stopped, and less than
  /// the limit since the later mark.
  private func windowOpen(at date: Date) -> Bool {
    refreshStoppedAt == nil
      && WatchRefreshPlan.windowOpen(
        now: date, lastAccepted: lastAccepted, lastOpened: lastOpened, lastListed: lastListed)
  }
  public mutating func linkTask(pending: Bool, at date: Date = Date()) {
    note(Event(at: date, kind: .linkTask, pending: pending))
  }
  public mutating func refreshRan(at date: Date = Date()) {
    pendingRefresh = nil
    pendingAskedAt = nil
    note(Event(at: date, kind: .refreshRan))
  }
  public mutating func refreshAsked(for scheduled: Date, at date: Date = Date()) {
    pendingRefresh = scheduled
    pendingAskedAt = date
    note(Event(at: date, kind: .refreshAsked, scheduled: scheduled))
  }
  public mutating func refreshRefused(at date: Date = Date()) {
    pendingRefresh = nil
    pendingAskedAt = nil
    note(Event(at: date, kind: .refreshRefused))
  }
  public mutating func received(at date: Date = Date()) {
    note(Event(at: date, kind: .received))
  }
  /// A song accepted at `date`; `receivedAt` when it reached the inbox, if
  /// known; `newSong` false when the Watch already had that copy. In the
  /// background the window is judged first, on the old marks: a song taken
  /// after it ran out never reopens it. Only a new song moves the window on.
  /// `waiting`: songs not ready on the Watch before this one.
  public mutating func accepted(
    receivedAt: Date?, active: Bool, newSong: Bool = true, waiting: Int = 1,
    at date: Date = Date()
  ) {
    if !active { latchRefreshStop(at: date, waiting: waiting) }
    if newSong { lastAccepted = date }
    let gap = receivedAt.map { max(0, date.timeIntervalSince($0)) }
    note(Event(at: date, kind: .accepted, active: active, gap: gap, newSong: newSong))
  }
  /// What to ask watchOS for now (`WatchRefreshPlan`), recording the stop
  /// the first time the window has run out. `waiting`: songs the manifest
  /// lists that are not ready on the Watch.
  public mutating func refreshPlan(now: Date = Date(), waiting: Int) -> WatchRefreshPlan {
    latchRefreshStop(at: now, waiting: waiting)
    return WatchRefreshPlan.next(
      now: now, waiting: waiting, lastAccepted: lastAccepted, lastOpened: lastOpened,
      lastListed: lastListed, stopped: refreshStoppedAt != nil)
  }
  /// With nothing waiting there is nothing to time out: no stop is recorded.
  private mutating func latchRefreshStop(at now: Date, waiting: Int) {
    guard refreshStoppedAt == nil, waiting > 0,
      !WatchRefreshPlan.windowOpen(
        now: now, lastAccepted: lastAccepted, lastOpened: lastOpened, lastListed: lastListed)
    else { return }
    refreshStoppedAt = now
    note(Event(at: now, kind: .refreshStopped))
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
      "Scheduled refreshes asked for: \(count(.refreshAsked, now: now)); ran: \(refresh); "
        + "turned down by watchOS: \(count(.refreshRefused, now: now)).",
      waitingLine(now: now),
      "Songs received: \(received); songs accepted: \(accepted) (\(away) while the app was not open).",
      "Woken by the iPhone's complication push: \(count(.phonePush, now: now)) times "
        + "(\(count(.phonePush, now: now) { $0.active == false }) while the app was not open).",
      "PigMusic complication on an Apple Watch face: "
        + (faceComplications.map { $0 > 0 ? "yes" : "no" } ?? "not known yet") + ".",
    ]
    let gaps = recent(now: now).compactMap { $0.kind == .accepted ? $0.gap : nil }
    if !gaps.isEmpty {
      lines.append(
        "Received to accepted: median \(WatchDeliveryLog.duration(WatchDeliveryLog.median(gaps))), "
          + "longest \(WatchDeliveryLog.duration(gaps.max() ?? 0)).")
    }
    if let refreshStoppedAt {
      lines.append(
        "Scheduled refreshes stopped at \(time(refreshStoppedAt)) (2 h without a new song); "
          + "none asked for until the app is opened or new songs are sent.")
    }
    if count(.refreshAsked, now: now) > 0, refresh == 0 {
      lines.append(
        "No scheduled refresh has run: watchOS may give no background time to an app "
          + "without a watch-face complication.")
    }
    if let lastOpened { lines.append("Last opened: \(time(lastOpened)).") }
    if let lastAccepted { lines.append("Last new song accepted: \(time(lastAccepted)).") }
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
  /// The refresh asked for and not yet run, and how long it has waited.
  private func waitingLine(now: Date) -> String {
    guard let pendingRefresh else { return "Scheduled refresh still waiting: none." }
    let asked = pendingAskedAt.map {
      ", asked \(WatchDeliveryLog.duration(max(0, now.timeIntervalSince($0)))) ago"
    }
    return "Scheduled refresh still waiting: one\(asked ?? ""), for "
      + "\(pendingRefresh.formatted(.iso8601)) "
      + (pendingRefresh < now
        ? "(\(WatchDeliveryLog.duration(now.timeIntervalSince(pendingRefresh))) overdue)."
        : "(not due yet).")
  }
}
