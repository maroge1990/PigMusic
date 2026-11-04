import Foundation
import PigDomain

/// Which of the Watch's pending listens to hand to the phone now. Every
/// acknowledgement flushed the pending listens again, and each one already
/// delivered but not yet acknowledged crossed again: N listens cost about
/// N²/2 messages, each a phone write and history upload (#97). A listen now
/// goes once, and again only after a reconnect or `CompanionSendLog.retryAfter`
/// without its acknowledgement.
public struct ListenOutbox: Sendable, Equatable {
  private var log = CompanionSendLog()
  public init() {}
  /// The pending listens not handed over recently, at most `limit`; they are
  /// marked as handed over.
  public mutating func take(_ pending: [ListeningEvent], limit: Int = 50, now: Date = Date())
    -> [ListeningEvent]
  {
    let due = pending.filter { log.due("listen:" + $0.id, now: now) }.prefix(limit)
    for event in due { log.mark("listen:" + event.id, at: now) }
    return Array(due)
  }
  /// A reconnect: listens not acknowledged yet go again.
  public mutating func reset() { log.reset() }
}
