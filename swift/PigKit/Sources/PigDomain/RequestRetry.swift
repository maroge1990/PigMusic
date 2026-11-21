import Foundation

/// When the iPhone sends again the songs a queue limit kept in its request
/// outbox (D-128, #242). Past the rate, every later new song would be
/// refused too: the pass over the outbox stops, and the next is at the
/// server's Retry-After. Past a waiting limit, a later song may still join a
/// job already queued: the pass goes on, and the next is after a backoff
/// doubling from a minute to fifteen, or sooner when the server's queue
/// changes (a song finished or was cancelled), at most once a minute.
///
/// Two times say when the outbox may be sent again: the rate's, a floor no
/// later answer lowers and no queue change opens (Codex confirmation round:
/// a waiting answer replaced it), and the waiting limit's backoff. Every
/// send consults both (a refresh, a new Add, a relaunch), so the songs are
/// never sent again early nor forgotten. They are armed by whatever kept
/// songs: a pass over the outbox or an Add sent at once. They are kept with
/// the outbox, so they outlive the app. Times are seconds since 1970.
public struct RequestRetry: Codable, Sendable, Equatable {
  public static let rate = "rate"
  /// No send before this: the latest Retry-After of the rate.
  public private(set) var rateNotBefore: Double?
  /// No send before this either: the waiting limit's backoff.
  public private(set) var waitingNotBefore: Double?
  /// The waiting limit that last kept songs; nil when none do.
  public private(set) var waitingLimit: String?
  /// Waiting-limit answers in a row that kept songs.
  public private(set) var attempt = 0
  /// When a waiting limit last kept songs.
  public private(set) var lastWaiting: Double?
  public init() {}

  /// Whether a song kept by `limit` ends the pass over the outbox.
  public static func stopsPass(_ limit: String?) -> Bool { limit == rate }

  /// Songs were kept at `now` (by a pass, or an Add sent at once): the
  /// seconds to wait before the next send; nil when none were kept
  /// (everything is forgotten).
  public mutating func kept(limit: String?, retryAfter: Int?, at now: Double) -> Double? {
    guard let limit else {
      self = RequestRetry()
      return nil
    }
    if limit == Self.rate {
      // Never earlier than a deadline already given.
      rateNotBefore = max(rateNotBefore ?? 0, now + Double(max(1, retryAfter ?? 60)))
    } else {
      waitingLimit = limit
      lastWaiting = now
      waitingNotBefore = now + min(900, 60 * pow(2, Double(attempt)))
      attempt += 1
    }
    return wait(at: now) ?? 0
  }

  /// The outbox was sent and nothing waits on a limit any more.
  public mutating func cleared() { self = RequestRetry() }

  /// Seconds still to wait at `now` before the outbox may be sent, or nil
  /// when it may be sent now: until the later of the two times.
  public func wait(at now: Double) -> Double? {
    let until = max(rateNotBefore ?? 0, waitingNotBefore ?? 0)
    return now < until ? until - now : nil
  }

  /// Whether a change of the server's queue at `now` is worth opening the
  /// waiting backoff early: songs wait on a waiting limit (room may have
  /// come), the last were kept a minute ago or more. The rate's time still
  /// holds.
  public func passOnQueueChange(at now: Double) -> Bool {
    guard waitingLimit != nil, let lastWaiting else { return false }
    return now - lastWaiting >= 60 && waitingNotBefore.map { now < $0 } == true
  }

  /// A queue change was worth it: the waiting backoff is over (the rate's
  /// time, if any, is not).
  public mutating func openNow() { waitingNotBefore = nil }
}
