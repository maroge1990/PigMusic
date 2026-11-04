import Foundation
import PigCompanion
import PigDomain
import Testing

struct CompanionSendLogTests {
  /// #70: the phone cleared its whole send record on every reachability change,
  /// so every song not yet confirmed crossed again whenever the Watch app opened.
  @Test func reconnectingResendsTheManifestButNotSongsOnTheirWay() {
    let start = Date(timeIntervalSince1970: 1_800_000_000)
    var log = CompanionSendLog()
    log.mark("manifest:7", at: start)
    log.mark("audio:song:abc", at: start)
    #expect(!log.due("manifest:7", now: start.addingTimeInterval(1)))
    log.forgetManifests()  // The Watch app came to the foreground.
    #expect(log.due("manifest:7", now: start.addingTimeInterval(1)))
    #expect(!log.due("audio:song:abc", now: start.addingTimeInterval(1)))
    // Unconfirmed songs still retry after ten minutes.
    #expect(log.due("audio:song:abc", now: start.addingTimeInterval(601)))
    #expect(log.due("audio:other:def", now: start))
    log.reset()
    #expect(log.due("audio:song:abc", now: start.addingTimeInterval(1)))
  }

  /// Messages the Watch hands over while the phone acknowledges `count`
  /// listens one at a time, each acknowledgement flushing again. Delivered
  /// messages are no longer outstanding, so the old flush sent them again.
  private func messages(_ count: Int, flush: ([ListeningEvent]) -> [ListeningEvent]) -> Int {
    var pending = (0..<count).map {
      ListeningEvent(eventId: "e\($0)", trackId: "t", playedAt: 0, seconds: 30)
    }
    var sent = flush(pending).count
    while !pending.isEmpty {
      pending.removeFirst()  // The phone acknowledged the oldest.
      sent += flush(pending).count
    }
    return sent
  }

  /// #97: each acknowledgement made the Watch resend every pending listen.
  @Test func eachListenCrossesOnceUntilAReconnect() {
    let old = messages(10) { Array($0.prefix(50)) }
    #expect(old == 55)
    var outbox = ListenOutbox()
    #expect(messages(10) { outbox.take($0) } == 10)
    // Not acknowledged: they go again after a reconnect, or after the retry time.
    let start = Date(timeIntervalSince1970: 1_800_000_000)
    let waiting = [ListeningEvent(eventId: "x", trackId: "t", playedAt: 0, seconds: 30)]
    var later = ListenOutbox()
    #expect(later.take(waiting, now: start).count == 1)
    #expect(later.take(waiting, now: start.addingTimeInterval(1)).isEmpty)
    #expect(later.take(waiting, now: start.addingTimeInterval(601)).count == 1)
    later.reset()
    #expect(later.take(waiting, now: start.addingTimeInterval(602)).count == 1)
  }
}
