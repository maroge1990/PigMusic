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
  /// How long "Can't reach your iPhone" is shown after it was worked out.
  public static let reachabilityAge: TimeInterval = 30 * 60
  /// When this status stops being shown, falling back to the neutral
  /// "PigMusic" (Codex round 1 on batch J); nil while it holds until the app
  /// writes another. Only "Can't reach your iPhone" goes stale on its own:
  /// the iPhone can come back while the Watch app is suspended and nobody
  /// rewrites it. Every other status changes only when songs arrive, leave
  /// or are turned away, and the app runs, and rewrites it, for each of
  /// those; "3 waiting" stays true whether or not the iPhone is in reach.
  public var expires: Date? {
    kind == .iPhoneAway ? updated.addingTimeInterval(Self.reachabilityAge) : nil
  }
  /// This status if it may still be shown at `now`, else nil (neutral). One
  /// that expires and was worked out more than the clock slack ahead of
  /// `now` (a clock since set back) can't be judged, and isn't shown.
  public func shown(at now: Date) -> WatchComplicationState? {
    guard let expires else { return self }
    if updated.timeIntervalSince(now) > WatchRefreshPlan.clockSlack { return nil }
    return now < expires ? self : nil
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
/// the face or PigMusic is open on Apple Watch already (it takes the songs
/// itself), never for a hand-off that only retries songs an earlier push
/// already announced (they are no new burst, Codex round 1 on batch J),
/// and always keeping `reserve` of the day's allowance. Saved on
/// the iPhone, so a background launch keeps the clock.
public struct WatchComplicationPushes: Codable, Equatable, Sendable {
  public enum Decision: String, Codable, Sendable {
    /// Send one now.
    case send
    /// PigMusic is open on Apple Watch (the link is reachable): no need.
    case watchAppOpen
    /// PigMusic's complication isn't on the active watch face.
    case disabled
    /// What is left of the day's allowance is the reserve.
    case reserve
    /// The last push was less than `spacing` ago.
    case spacing
    /// This burst of hand-offs already had its push.
    case alreadyThisBurst
    /// Every song handed over was in an earlier push: a retry, not a new burst.
    case retry
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
  /// Songs remembered as announced by a push.
  public static let pushedCapacity = 2000

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
  /// The last push sent.
  public private(set) var lastSent: Date?
  /// Songs a delivered push announced in the last `keep`, and when it was sent.
  public private(set) var pushedSongs: [String: Date] = [:]
  /// Songs in a push queued and not yet reported delivered or failed, and
  /// when it was sent (Codex round 2 on batch J). Held at most `pendingKeep`.
  public private(set) var pendingSongs: [String: Date] = [:]
  public static let pendingKeep: TimeInterval = 86400
  public init() {}

  /// Songs (`songs`, their ids) were handed to the Watch link at `now`:
  /// whether to push. A `.send` is recorded only once `sent(at:songs:)`
  /// confirms it was queued. Songs all announced before, or in a push still
  /// on its way, are a retry: no push, and no burst starts. (No ids at all
  /// can't be judged and count as new.)
  public mutating func handed(
    now: Date, songs: [String] = [], enabled: Bool, remaining: Int, watchAppOpen: Bool = false
  ) -> Decision {
    forgetOldSongs(now: now)
    if !songs.isEmpty,
      songs.allSatisfy({ pushedSongs[$0] != nil || pendingSongs[$0] != nil })
    {
      note(Event(at: now, decision: .retry))
      return .retry
    }
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
      burstPushed: burstPushed, watchAppOpen: watchAppOpen)
    if decision != .send, decision != .alreadyThisBurst {
      note(Event(at: now, decision: decision))
    }
    return decision
  }
  /// A push was queued, carrying exactly `songs` (the ids in its payload):
  /// this burst has had its push, and the attempt counts for the spacing
  /// (the reserve is WatchConnectivity's own count). Its songs wait as
  /// pending until `finished` says whether it was delivered.
  public mutating func sent(at date: Date, songs: [String] = []) {
    burstPushed = true
    lastSent = date
    for id in songs { pendingSongs[id] = date }
    forgetOldSongs(now: date)
    note(Event(at: date, decision: .send))
  }
  /// WatchConnectivity reported the push sent at `sentAt`, carrying
  /// `songs`: delivered, its songs are announced; failed, they are released
  /// so a later hand-off may push for them again. A later push of the same
  /// songs keeps them pending.
  public mutating func finished(songs: [String], sentAt: Date, delivered: Bool) {
    for id in songs where pendingSongs[id] == sentAt {
      pendingSongs[id] = nil
      if delivered { pushedSongs[id] = sentAt }
    }
    forgetOldSongs(now: sentAt)
  }
  /// Drops songs announced more than `keep` ago, then the oldest beyond
  /// `pushedCapacity`.
  private mutating func forgetOldSongs(now: Date) {
    pushedSongs = Self.bounded(pushedSongs.filter { now.timeIntervalSince($0.value) <= Self.keep })
    pendingSongs = Self.bounded(
      pendingSongs.filter { now.timeIntervalSince($0.value) <= Self.pendingKeep })
  }
  private static func bounded(_ songs: [String: Date]) -> [String: Date] {
    guard songs.count > pushedCapacity else { return songs }
    let kept = songs.sorted { ($0.value, $0.key) > ($1.value, $1.key) }.prefix(pushedCapacity)
    return Dictionary(uniqueKeysWithValues: kept.map { ($0.key, $0.value) })
  }

  /// The rule. A last push more than `spacing` ahead of `now` is from a
  /// clock since set back and is ignored.
  public static func decide(
    now: Date, enabled: Bool, remaining: Int, lastSent: Date?, burstPushed: Bool,
    watchAppOpen: Bool = false
  ) -> Decision {
    if burstPushed { return .alreadyThisBurst }
    if watchAppOpen { return .watchAppOpen }
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
        + "\(count(.spacing, now: now)) too soon after the last push, "
        + "\(count(.watchAppOpen, now: now)) with PigMusic open on Apple Watch, "
        + "\(count(.retry, now: now)) retrying songs already announced.",
    ]
    if let lastHanded {
      lines.append("Songs last handed to the Apple Watch link: \(lastHanded.formatted(.iso8601)).")
    }
    return lines.joined(separator: "\n")
  }
}

/// The message a complication push carries; the Watch tells it apart from
/// the other messages on the link.
public struct CompanionWake: Codable, Equatable, Sendable {
  /// Song ids carried at most: one new one is evidence enough.
  public static let maxSongs = 500
  public var kind = "wake"
  public var sentAt: Date
  /// The ids of the songs just handed over: the Watch's evidence of new songs.
  public var songs: [String]?
  public init(sentAt: Date, songs: [String] = []) {
    self.sentAt = sentAt
    self.songs = Array(songs.prefix(Self.maxSongs))
  }
  public static func decode(_ data: Data) -> CompanionWake? {
    guard let wake = try? JSONDecoder().decode(CompanionWake.self, from: data), wake.kind == "wake"
    else { return nil }
    return wake
  }
}
