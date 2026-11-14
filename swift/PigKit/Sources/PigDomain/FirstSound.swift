import Foundation

/// How long a start took to be heard, from the tap (or skip) to the first
/// sound, for the parity targets (#191). The web's StartClock does the same.
public struct FirstSound: Sendable, Equatable {
  public let ms: Int
  /// "library", "mix", "remote" or "other", as the server counts them.
  public let source: String
  /// "play", "skip", "previous" or "seek".
  public let action: String
  public init(ms: Int, source: String, action: String) {
    self.ms = ms
    self.source = source
    self.action = action
  }

  /// The server's kind of source for a song in the queue.
  public static func source(of track: Track?) -> String {
    guard let track else { return "other" }
    if track.id.hasPrefix("mix:") { return "mix" }
    switch track.serverState {
    case nil, "in_library": return "library"
    case "not_in_library", "requested", "failed": return "remote"
    default: return "other"
    }
  }
}

/// One start at a time: a newer start replaces an unheard one, a pause or a
/// failure before the sound drops it (the person's wait, not the server's),
/// and each start is reported once.
public struct StartClock: Sendable {
  private var pending: (action: String, at: ContinuousClock.Instant)?
  public init() {}
  public var waiting: Bool { pending != nil }
  public mutating func begin(_ action: String, at: ContinuousClock.Instant = .now) {
    pending = (action, at)
  }
  public mutating func cancel() { pending = nil }
  /// The start heard now, if one was waiting.
  public mutating func heard(_ track: Track?, at: ContinuousClock.Instant = .now) -> FirstSound? {
    guard let start = pending else { return nil }
    pending = nil
    let ms = max(0, Int(((at - start.at) / .milliseconds(1)).rounded()))
    return FirstSound(ms: ms, source: FirstSound.source(of: track), action: start.action)
  }
}
