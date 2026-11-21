import Foundation

/// When the iPhone sends again the songs a queue limit kept in its request
/// outbox (D-128, #242). Past the rate, every later new song would be
/// refused too: the pass over the outbox stops, and the next is at the
/// server's Retry-After. Past a waiting limit, a later song may still join a
/// job already queued: the pass goes on, and the next is after a backoff
/// doubling from a minute to fifteen, or sooner when the server's queue
/// changes (a song finished or was cancelled), at most once a minute.
///
/// One time, `notBefore`, says when the outbox may be sent again; every
/// send consults it (a refresh, a new Add, a relaunch), so the songs are
/// never sent again early nor forgotten. It is armed by whatever kept songs:
/// a pass over the outbox or an Add sent at once. It is kept with the
/// outbox, so it outlives the app. Times are seconds since 1970.
public struct RequestRetry: Codable, Sendable, Equatable {
  public static let rate = "rate"
  /// The limit that last kept songs; nil when none wait on one.
  public private(set) var limit: String?
  /// Waiting-limit answers in a row that kept songs.
  public private(set) var attempt = 0
  /// When songs were last kept.
  public private(set) var lastPass: Double?
  /// No send of the outbox before this.
  public private(set) var notBefore: Double?
  public init() {}

  /// Whether a song kept by `limit` ends the pass over the outbox.
  public static func stopsPass(_ limit: String?) -> Bool { limit == rate }

  /// Songs were kept at `now` (by a pass, or an Add sent at once): the
  /// seconds to wait before the next send, which `notBefore` now holds; nil
  /// when none were kept (everything is forgotten).
  public mutating func kept(limit: String?, retryAfter: Int?, at now: Double) -> Double? {
    guard let limit else {
      self = RequestRetry()
      return nil
    }
    self.limit = limit
    lastPass = now
    let delay: Double
    if limit == Self.rate {
      attempt = 0
      delay = Double(max(1, retryAfter ?? 60))
    } else {
      delay = min(900, 60 * pow(2, Double(attempt)))
      attempt += 1
    }
    notBefore = now + delay
    return delay
  }

  /// The outbox was sent and nothing waits on a limit any more.
  public mutating func cleared() { self = RequestRetry() }

  /// Seconds still to wait at `now` before the outbox may be sent, or nil
  /// when it may be sent now.
  public func wait(at now: Double) -> Double? {
    guard let notBefore, now < notBefore else { return nil }
    return notBefore - now
  }

  /// Whether a change of the server's queue at `now` is worth a send before
  /// `notBefore`: songs wait on a waiting limit (room may have come), and
  /// the last were kept a minute ago or more.
  public func passOnQueueChange(at now: Double) -> Bool {
    guard let limit, limit != Self.rate, let lastPass else { return false }
    return now - lastPass >= 60
  }

  /// A queue change was worth a send: it may go now.
  public mutating func openNow() { notBefore = nil }
}

/// A quality choice whose replacements a waiting limit kept from being
/// queued (D-128): choosing it again queues them as room allows. The web's
/// words; awaiting the owner.
public enum QualityNotice {
  public static func deferred(_ count: Int) -> String? {
    guard count > 0 else { return nil }
    return "\(count) song\(count == 1 ? "" : "s") will change quality later: too many songs are "
      + "waiting. Choose this quality again once some are added."
  }
}
