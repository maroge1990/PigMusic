import Foundation
import PigCompanion
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
}
