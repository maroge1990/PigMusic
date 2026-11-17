import Foundation

/// When each song sent to the Watch passed each step (#219), kept on the phone
/// for the diagnostics export: sent (queued on the phone), its AAC copy ready
/// on the phone, handed to WatchConnectivity, and arrived on the Watch. The
/// phone also notes when its app went to the background and came back, so the
/// export can say how many songs arrived while it was away.
public struct WatchDeliveryLog: Codable, Sendable, Equatable {
  public struct Entry: Codable, Sendable, Equatable {
    public var sent: Date
    public var ready: Date?
    public var handed: Date?
    public var arrived: Date?
    /// Whether the phone app was in the foreground when the song arrived; nil
    /// when unknown (no transition was noted before then).
    public var phoneForeground: Bool?
  }
  public struct Transition: Codable, Sendable, Equatable {
    public var at: Date
    public var foreground: Bool
  }
  /// Songs at most, newest kept.
  public static let capacity = 1000
  /// Foreground and background changes kept.
  public static let transitionCapacity = 500
  /// Entries older than this (from their send) are dropped.
  public static let keep: TimeInterval = 30 * 86400
  public private(set) var entries: [String: Entry] = [:]
  public private(set) var transitions: [Transition] = []
  public init() {}

  /// Songs queued for the Watch. A song already followed keeps its first send.
  public mutating func sent(_ ids: some Sequence<String>, at date: Date = Date()) {
    for id in ids where entries[id] == nil { entries[id] = Entry(sent: date) }
    trim(now: date)
  }
  public mutating func ready(_ id: String, at date: Date = Date()) {
    guard entries[id]?.ready == nil, entries[id] != nil else { return }
    entries[id]?.ready = date
  }
  public mutating func handed(_ id: String, at date: Date = Date()) {
    guard entries[id]?.handed == nil, entries[id] != nil else { return }
    entries[id]?.handed = date
  }
  /// The Watch confirmed the song, `at` the time it stored it when it said so,
  /// otherwise when the phone learned of it.
  public mutating func arrived(_ id: String, at date: Date) {
    guard var entry = entries[id], entry.arrived == nil else { return }
    entry.arrived = max(date, entry.sent)
    entry.phoneForeground = foreground(at: date)
    entries[id] = entry
  }
  /// How many of these songs, confirmed by the Watch, are sent songs whose
  /// arrival isn't recorded yet: the Watch's new songs, each counted once
  /// however often it confirms them (D-113's clock).
  public func newArrivals(_ ids: some Sequence<String>) -> Int {
    Set(ids).filter { entries[$0].map { $0.arrived == nil } ?? false }.count
  }
  /// Songs no longer sent to the Watch are forgotten, so a later send starts again.
  public mutating func retain(_ ids: Set<String>) {
    entries = entries.filter { ids.contains($0.key) }
  }
  public mutating func phone(foreground: Bool, at date: Date = Date()) {
    guard transitions.last?.foreground != foreground else { return }
    transitions.append(Transition(at: date, foreground: foreground))
    if transitions.count > Self.transitionCapacity {
      transitions.removeFirst(transitions.count - Self.transitionCapacity)
    }
  }
  /// The phone app's state at `date`, from the last transition before it.
  public func foreground(at date: Date) -> Bool? {
    transitions.last(where: { $0.at <= date })?.foreground
  }
  private mutating func trim(now: Date) {
    entries = entries.filter { now.timeIntervalSince($0.value.sent) <= Self.keep }
    guard entries.count > Self.capacity else { return }
    let newest = entries.sorted { $0.value.sent > $1.value.sent }.prefix(Self.capacity)
    entries = Dictionary(uniqueKeysWithValues: newest.map { ($0.key, $0.value) })
  }

  /// The "Watch delivery" lines of the diagnostics export.
  public func summary() -> String {
    let all = Array(entries.values)
    let arrived = all.filter { $0.arrived != nil }
    guard !all.isEmpty else { return "No songs sent to Apple Watch in the last 30 days." }
    var lines = [
      "\(all.count) songs sent in the last 30 days, \(arrived.count) arrived, "
        + "\(all.count - arrived.count) still on their way."
    ]
    func spans(_ from: KeyPath<Entry, Date?>, _ to: KeyPath<Entry, Date?>) -> [TimeInterval] {
      all.compactMap { entry in
        guard let start = entry[keyPath: from], let end = entry[keyPath: to] else { return nil }
        return max(0, end.timeIntervalSince(start))
      }
    }
    let sentKey: KeyPath<Entry, Date?> = \.sentDate
    let total = spans(sentKey, \.arrived)
    if !total.isEmpty {
      lines.append(
        "Send to arrival: median \(Self.duration(Self.median(total))), longest \(Self.duration(total.max() ?? 0)).")
    }
    let stages: [(String, KeyPath<Entry, Date?>, KeyPath<Entry, Date?>)] = [
      ("Send to copy ready on the iPhone", sentKey, \Entry.ready),
      ("Copy ready to handed to the Apple Watch link", \Entry.ready, \Entry.handed),
      ("Handed to arrival on Apple Watch", \Entry.handed, \Entry.arrived),
    ]
    for (label, from, to) in stages {
      let values = spans(from, to)
      guard !values.isEmpty else { continue }
      lines.append(
        "\(label): median \(Self.duration(Self.median(values))), longest \(Self.duration(values.max() ?? 0)).")
    }
    if !arrived.isEmpty {
      let background = arrived.filter { $0.phoneForeground == false }.count
      let unknown = arrived.filter { $0.phoneForeground == nil }.count
      lines.append(
        "Arrived while the iPhone app was in the background: \(background) of \(arrived.count)"
          + (unknown > 0 ? " (\(unknown) unknown)." : "."))
    }
    return lines.joined(separator: "\n")
  }
  static func median(_ values: [TimeInterval]) -> TimeInterval {
    let sorted = values.sorted()
    guard !sorted.isEmpty else { return 0 }
    let middle = sorted.count / 2
    return sorted.count % 2 == 1 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2
  }
  static func duration(_ seconds: TimeInterval) -> String {
    let whole = Int(seconds.rounded())
    if whole < 60 { return "\(whole) s" }
    if whole < 3600 { return "\(whole / 60) min \(whole % 60) s" }
    return "\(whole / 3600) h \(whole % 3600 / 60) min"
  }
}

extension WatchDeliveryLog.Entry {
  fileprivate var sentDate: Date? { sent }
}
