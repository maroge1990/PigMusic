import Foundation
import PigCompanion
import Testing

/// #219, Codex round 2 on batch G: what an accepted file changed is judged
/// from the archive's library just before the write.
struct CompanionAcceptanceTests {
  @Test func aCopyNotReadyBeforeIsNew() {
    let judged = CompanionAcceptance.judge(
      listed: ["a", "b", "c"], removed: [], ready: ["a": "1"], songId: "b", checksum: "2")
    #expect(judged == CompanionAcceptance(newlyReady: true, waitingBefore: 2))
  }

  @Test func aCopyTheWatchAlreadyHadIsNotNew() {
    let judged = CompanionAcceptance.judge(
      listed: ["a", "b"], removed: [], ready: ["a": "1", "b": "2"], songId: "b", checksum: "2")
    #expect(judged == CompanionAcceptance(newlyReady: false, waitingBefore: 0))
  }

  @Test func aReplacedEncodingIsNewAndRemovedSongsDontWait() {
    // The library lists b with a new checksum: the old copy isn't ready.
    let judged = CompanionAcceptance.judge(
      listed: ["a", "b", "c"], removed: ["c"], ready: ["a": "1"], songId: "b", checksum: "3")
    #expect(judged == CompanionAcceptance(newlyReady: true, waitingBefore: 1))
  }
}
