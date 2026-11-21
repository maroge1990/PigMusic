import Foundation

/// When the iPhone sends again the songs a queue limit kept in its request
/// outbox (D-128, #242). Past the rate, every later new song would be
/// refused too: the pass over the outbox stops, and the next is at the
/// server's Retry-After. Past a waiting limit, a later song may still join a
/// job already queued: the pass goes on, and the next is after a backoff
/// doubling from a minute to fifteen, or sooner when the server's queue
/// changes (a song finished or was cancelled), at most once a minute.
public struct RequestRetry: Sendable, Equatable {
  public static let rate = "rate"
  /// The limit that kept songs at the last pass; nil when none did.
  public private(set) var limit: String?
  /// Waiting-limit passes in a row that kept songs.
  public private(set) var attempt = 0
  /// When the last pass that kept songs ended (seconds, any clock).
  public private(set) var lastPass: Double?
  public init() {}

  /// Whether a song kept by `limit` ends the pass over the outbox.
  public static func stopsPass(_ limit: String?) -> Bool { limit == rate }

  /// After a pass at `now`: the seconds to wait before the next, or nil
  /// when no song was kept (everything is forgotten).
  public mutating func passed(limit: String?, retryAfter: Int?, at now: Double) -> Double? {
    guard let limit else {
      self = RequestRetry()
      return nil
    }
    self.limit = limit
    lastPass = now
    if limit == Self.rate {
      attempt = 0
      return Double(max(1, retryAfter ?? 60))
    }
    let delay = min(900, 60 * pow(2, Double(attempt)))
    attempt += 1
    return delay
  }

  /// Whether a change of the server's queue at `now` is worth a pass: songs
  /// wait on a waiting limit, and the last pass was a minute ago or more.
  public func passOnQueueChange(at now: Double) -> Bool {
    guard let limit, limit != Self.rate, let lastPass else { return false }
    return now - lastPass >= 60
  }
}
