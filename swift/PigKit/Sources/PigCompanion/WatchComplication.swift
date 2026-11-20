import Foundation

extension WatchLibraryStatus.Headline {
  /// The status icon beside the headline (docs/DESIGN.md, D-120), on the
  /// Watch's home and Storage and on its watch-face complication.
  public var symbol: String {
    switch self {
    case .ready: "checkmark.circle.fill"
    case .storageFull, .couldntSave: "exclamationmark.circle"
    case .iPhoneAway: "iphone.slash"
    case .arriving: "clock"
    }
  }
}

/// What the watch-face complication shows (D-125, #219): the Watch app
/// writes it to the App Group whenever its status changes, and the widget
/// extension, which can't read the app's own files, reads it from there.
/// Its words are the Watch home's (`WatchLibraryStatus.title`), except that
/// songs on their way read as a count ("3 waiting"), which fits a corner.
public struct WatchComplicationState: Codable, Equatable, Sendable {
  public enum Kind: String, Codable, Sendable {
    case ready, arriving, iPhoneAway, storageFull, couldntSave
  }
  /// The App Group the Watch app and its complication share (the iPhone
  /// app and its share extension use the same id on the iPhone).
  public static let appGroup = "group.au.com.pig.music"
  /// The file in the App Group's folder.
  public static let fileName = "watch-complication.json"
  /// The widget's kind, which the Watch app names when it asks for a reload.
  public static let widgetKind = "PigMusicWatchStatus"

  public var kind: Kind
  /// The Watch home's headline words for this state.
  public var title: String
  /// Songs not on the Watch yet.
  public var waiting: Int
  /// When the Watch app worked this out.
  public var updated: Date

  public init(kind: Kind, title: String, waiting: Int, updated: Date) {
    self.kind = kind
    self.title = title
    self.waiting = max(0, waiting)
    self.updated = updated
  }
  public init(status: WatchLibraryStatus, at date: Date) {
    let kind: Kind =
      switch status.headline {
      case .ready: .ready
      case .arriving: .arriving
      case .iPhoneAway: .iPhoneAway
      case .storageFull: .storageFull
      case .couldntSave: .couldntSave
      }
    self.init(kind: kind, title: status.title, waiting: status.waiting, updated: date)
  }

  /// The status icon, as on the Watch's home.
  public var symbol: String {
    switch kind {
    case .ready: WatchLibraryStatus.Headline.ready.symbol
    case .arriving: WatchLibraryStatus.Headline.arriving.symbol
    case .iPhoneAway: WatchLibraryStatus.Headline.iPhoneAway.symbol
    case .storageFull: WatchLibraryStatus.Headline.storageFull.symbol
    case .couldntSave: WatchLibraryStatus.Headline.couldntSave(0).symbol
    }
  }
  /// One line: "Ready to play", "3 waiting", "Storage full"...
  public var words: String {
    kind == .arriving && waiting > 0 ? "\(waiting) waiting" : title
  }
  /// The number under the icon in the small circle, while songs wait.
  public var badge: String? {
    kind != .ready && waiting > 0 ? "\(waiting)" : nil
  }
  /// What changes what the complication shows (not the time it was worked out).
  public func shows(sameAs other: WatchComplicationState?) -> Bool {
    guard let other else { return false }
    return kind == other.kind && title == other.title && waiting == other.waiting
  }

  public func encoded() throws -> Data { try JSONEncoder().encode(self) }
  public static func decode(_ data: Data) -> WatchComplicationState? {
    try? JSONDecoder().decode(WatchComplicationState.self, from: data)
  }
}

/// When the Watch app asks WidgetKit to redraw its complication: at once
/// when what it says changes ("3 waiting" to "Ready to play", or a new
/// problem), but a count that only goes up or down redraws at most once per
/// `spacing`, so a burst of arriving songs doesn't spend watchOS's reload
/// budget one song at a time. The deferred redraw is asked for again with
/// the state then current.
public struct WatchComplicationReload: Equatable, Sendable {
  public enum Decision: Equatable, Sendable {
    /// Nothing the complication shows has changed.
    case none
    /// Reload now.
    case now
    /// Ask again at this time (one deferred reload at a time).
    case later(Date)
  }
  public static let spacing: TimeInterval = 5 * 60

  /// What the last reload asked for showed.
  public private(set) var shown: WatchComplicationState?
  public private(set) var lastReload: Date?
  public init() {}

  public mutating func changed(to state: WatchComplicationState, now: Date) -> Decision {
    if state.shows(sameAs: shown) { return .none }
    if let shown, shown.kind == state.kind, shown.title == state.title,
      let lastReload
    {
      let since = now.timeIntervalSince(lastReload)
      // A reload apparently in the future is from a clock set back: not held to it.
      if since >= 0, since < Self.spacing {
        return .later(lastReload.addingTimeInterval(Self.spacing))
      }
    }
    shown = state
    lastReload = now
    return .now
  }
}

/// The iPhone's wake-ups for the Watch app (D-125, #219): when it hands
/// songs to the Watch link and PigMusic's complication is on the active
/// watch face, it may send one `transferCurrentComplicationUserInfo`, which
/// watchOS delivers by waking the Watch app. Those are rationed by watchOS
/// (`remainingComplicationUserInfoTransfers`, about 50 a day), so: at most
/// one per hand-off burst (hand-offs less than `burstGap` apart), never
/// within `spacing` of the last one, never when the complication isn't on
/// the face, and always keeping `reserve` of the day's allowance. Saved on
/// the iPhone, so a background launch keeps the clock.
public struct WatchComplicationPushes: Codable, Equatable, Sendable {
  public enum Decision: String, Codable, Sendable {
    /// Send one now.
    case send
    /// PigMusic's complication isn't on the active watch face.
    case disabled
    /// What is left of the day's allowance is the reserve.
    case reserve
    /// The last push was less than `spacing` ago.
    case spacing
    /// This burst of hand-offs already had its push.
    case alreadyThisBurst
  }
  public struct Event: Codable, Equatable, Sendable {
    public var at: Date
    public var decision: Decision
  }
  /// Hand-offs closer than this belong to one burst.
  public static let burstGap: TimeInterval = 10 * 60
  /// The least time between two pushes.
  public static let spacing: TimeInterval = 20 * 60
  /// Pushes of the day's allowance never used.
  public static let reserve = 10
  /// Events older than this are dropped.
  public static let keep: TimeInterval = 7 * 86400
  public static let capacity = 500

  /// Pushes sent and hand-offs that sent none (not those in a burst that
  /// already had one), newest last.
  public private(set) var events: [Event] = []
  /// When songs were last handed to the link.
  public private(set) var lastHanded: Date?
  /// Whether the burst `lastHanded` belongs to has had its push.
  public private(set) var burstPushed = false
  /// What WatchConnectivity last said: complication on the face, pushes left today.
  public private(set) var enabled: Bool?
  public private(set) var remaining: Int?
  public init() {}

  /// The last push sent.
  public var lastSent: Date? { events.last { $0.decision == .send }?.at }

  /// Songs were handed to the Watch link at `now`: whether to push. A
  /// `.send` is recorded only once `sent(at:)` confirms it went.
  public mutating func handed(now: Date, enabled: Bool, remaining: Int) -> Decision {
    if let lastHanded {
      let since = now.timeIntervalSince(lastHanded)
      if since >= Self.burstGap || since < -Self.burstGap { burstPushed = false }
    } else {
      burstPushed = false
    }
    lastHanded = now
    self.enabled = enabled
    self.remaining = remaining
    let decision = Self.decide(
      now: now, enabled: enabled, remaining: remaining, lastSent: lastSent,
      burstPushed: burstPushed)
    if decision != .send, decision != .alreadyThisBurst {
      note(Event(at: now, decision: decision))
    }
    return decision
  }
  /// The push went: this burst has had its push.
  public mutating func sent(at date: Date) {
    burstPushed = true
    note(Event(at: date, decision: .send))
  }

  /// The rule. A last push more than `spacing` ahead of `now` is from a
  /// clock since set back and is ignored.
  public static func decide(
    now: Date, enabled: Bool, remaining: Int, lastSent: Date?, burstPushed: Bool
  ) -> Decision {
    if burstPushed { return .alreadyThisBurst }
    if !enabled { return .disabled }
    if remaining <= reserve { return .reserve }
    if let lastSent {
      let since = now.timeIntervalSince(lastSent)
      if since < spacing, since > -spacing { return .spacing }
    }
    return .send
  }

  private mutating func note(_ event: Event) {
    events.append(event)
    events.removeAll { event.at.timeIntervalSince($0.at) > Self.keep }
    if events.count > Self.capacity { events.removeFirst(events.count - Self.capacity) }
  }
  public func count(_ decision: Decision, now: Date = Date()) -> Int {
    events.filter { $0.decision == decision && now.timeIntervalSince($0.at) <= Self.keep }.count
  }

  /// The lines for the iPhone's diagnostics export, in its Watch delivery
  /// section. `enabled`, `remaining`: what WatchConnectivity says now (nil
  /// when the link isn't active).
  public func summary(now: Date = Date(), enabled: Bool?, remaining: Int?) -> String {
    let face = (enabled ?? self.enabled).map { $0 ? "yes" : "no" } ?? "not known"
    var lines = [
      "PigMusic complication on the active Apple Watch face: \(face).",
      "Pushes that wake PigMusic on Apple Watch: \(count(.send, now: now)) in the last 7 days"
        + (lastSent.map { "; last at \($0.formatted(.iso8601))." } ?? "."),
      "Pushes left today: \((remaining ?? self.remaining).map(String.init) ?? "not known") "
        + "(\(Self.reserve) are always kept).",
      "Hand-offs with no push: \(count(.disabled, now: now)) with no complication on the face, "
        + "\(count(.reserve, now: now)) to keep the reserve, "
        + "\(count(.spacing, now: now)) too soon after the last push.",
    ]
    if let lastHanded { lines.append("Songs last handed to the Apple Watch link: \(lastHanded.formatted(.iso8601)).") }
    return lines.joined(separator: "\n")
  }
}

/// The message a complication push carries; the Watch tells it apart from
/// the other messages on the link.
public struct CompanionWake: Codable, Equatable, Sendable {
  public var kind = "wake"
  public var sentAt: Date
  public init(sentAt: Date) { self.sentAt = sentAt }
  public static func decode(_ data: Data) -> CompanionWake? {
    guard let wake = try? JSONDecoder().decode(CompanionWake.self, from: data), wake.kind == "wake"
    else { return nil }
    return wake
  }
}
