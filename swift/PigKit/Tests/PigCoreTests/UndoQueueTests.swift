import Foundation
import PigDomain
import Testing

// #183 (D-79): destructive actions act at once with an Undo on the iPhone and
// the Watch, as on the web.

@MainActor private final class Sent {
  var ids: [String] = []
}

@MainActor @Test func anUndoneActionIsNeverSentAndAWaitingOneIsSentWhenItsTimeRunsOut()
  async throws
{
  let sent = Sent()
  let queue = UndoQueue(wait: .milliseconds(50), linger: .zero)
  queue.schedule("a", label: "Removed A") {
    sent.ids.append("a")
    return true
  }
  queue.schedule("b", label: "Removed B") {
    sent.ids.append("b")
    return true
  }
  #expect(queue.hides("a") && queue.hides("b"))
  #expect(queue.items.map(\.label) == ["Removed A", "Removed B"])
  queue.undo("a")
  #expect(!queue.hides("a") && queue.items.map(\.id) == ["b"])
  for _ in 0..<200 where sent.ids.isEmpty || queue.hides("b") {
    try await Task.sleep(for: .milliseconds(10))
  }
  #expect(sent.ids == ["b"] && queue.items.isEmpty && !queue.hides("b"))
}

@MainActor @Test func leavingTheAppSendsEverythingAtOnceInOrder() async {
  let sent = Sent()
  let queue = UndoQueue(wait: .seconds(600), linger: .zero)
  for id in ["1", "2", "3"] {
    queue.schedule(id, label: id) {
      sent.ids.append(id)
      return true
    }
  }
  queue.schedule("1", label: "again") {
    sent.ids.append("again")
    return true
  }
  await queue.sendAllAndWait()
  #expect(sent.ids == ["1", "2", "3"])
  #expect(queue.items.isEmpty)
  queue.undo("2")  // Too late: already sent.
  #expect(sent.ids == ["1", "2", "3"])
}

@MainActor @Test func aSentThingStaysHiddenBrieflyButAFailedOneComesBack() async throws {
  let queue = UndoQueue(wait: .seconds(600), linger: .milliseconds(80))
  queue.schedule("kept", label: "kept") { true }
  queue.schedule("failed", label: "failed") { false }
  await queue.sendAllAndWait()
  #expect(queue.hides("kept") && !queue.hides("failed"))
  for _ in 0..<200 where queue.hides("kept") { try await Task.sleep(for: .milliseconds(10)) }
  #expect(!queue.hides("kept"))
}

@MainActor @Test func anEndedAccountsActionsAreDroppedEvenOnesQueuedBehindASend() async throws {
  // Codex (batch 9, high): revocation flushed actions into the next account.
  let sent = Sent()
  let queue = UndoQueue(wait: .seconds(600), linger: .zero)
  let gate = AsyncStream<Void>.makeStream()
  var started = false
  queue.schedule("slow", label: "slow") {
    started = true
    for await _ in gate.stream { break }
    sent.ids.append("slow")
    return true
  }
  queue.schedule("behind", label: "behind") {
    sent.ids.append("behind")
    return true
  }
  queue.schedule("waiting", label: "waiting") {
    sent.ids.append("waiting")
    return true
  }
  queue.sendAll()  // "slow" starts; "behind" and "waiting" queue behind it.
  for _ in 0..<200 where !started { try await Task.sleep(for: .milliseconds(5)) }
  queue.discardAll()
  gate.continuation.yield()
  gate.continuation.finish()
  await queue.sendAllAndWait()
  #expect(sent.ids == ["slow"])
  #expect(queue.items.isEmpty && !queue.hides("behind") && !queue.hides("waiting"))
}

@Test func queuedRemovalsFromOnePlaylistVersionShiftOnlyForEachOther() {
  // Codex (batch 9, high): entry IDs are positions, so a removal must keep
  // the position and revision it was made on. From [A, B, C] at revision 4,
  // remove A (0) then B (1): B is sent as position 0 at revision 5.
  #expect(PlaylistRemoval.rebased(position: 0, revision: 4, after: []) == .init(0, 4))
  #expect(PlaylistRemoval.rebased(position: 1, revision: 4, after: [0]) == .init(0, 5))
  // Removed in the other order: C's position is untouched by a later song.
  #expect(PlaylistRemoval.rebased(position: 0, revision: 4, after: [2]) == .init(0, 5))
  #expect(PlaylistRemoval.rebased(position: 2, revision: 4, after: [0, 1]) == .init(0, 6))
}

@Test func aRemovalOnlyQueuedOfflineNeverShiftsTheNextOne() {
  // Codex confirmation (batch 9, high): a removal queued offline may conflict
  // and be discarded; if the next one had been shifted over it, it would
  // remove another song. From [A, B, C] at revision 0: A is only queued.
  var ledger = PlaylistRemovalLedger()
  #expect(ledger.plan(playlist: "p", position: 0, revision: 0) == .init(0, 0))
  ledger.record(playlist: "p", position: 0, revision: 0, applied: false)
  // B keeps its own position and revision: refused for review if A is gone.
  #expect(ledger.plan(playlist: "p", position: 1, revision: 0) == .init(1, 0))
  // Once the server applies a removal, the next one from that version shifts.
  ledger.record(playlist: "p", position: 0, revision: 0, applied: true)
  #expect(ledger.plan(playlist: "p", position: 1, revision: 0) == .init(0, 1))
  #expect(ledger.plan(playlist: "q", position: 1, revision: 0) == .init(1, 0))
}

@Test func aSongAlreadyRemovedFromAVersionIsNeverRemovedAgain() {
  // Codex confirmation (batch 9, high): a stale cached copy showed A again
  // after its removal; removing it again was sent as position 0 at
  // revision 1 and removed B.
  var ledger = PlaylistRemovalLedger()
  ledger.record(playlist: "p", position: 0, revision: 0, applied: true)
  #expect(ledger.removed(playlist: "p", position: 0, revision: 0))
  #expect(ledger.plan(playlist: "p", position: 0, revision: 0) == nil)
  #expect(ledger.plan(playlist: "p", position: 1, revision: 0) == .init(0, 1))
  #expect(!ledger.removed(playlist: "p", position: 0, revision: 1))
}
