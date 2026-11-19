import Foundation
@testable import PigDomain
import Testing

/// Codex round 1 on batch E (D-120, #251).
struct PlainScreensChangesTests {
  private func song(_ id: String, _ seconds: Double? = 240) -> Track {
    var track = Track(id: id, title: "Song " + id, artist: "Artist")
    track.duration = seconds
    return track
  }

  /// Codex round 1 on batch E: a change refused because its playlist changed
  /// can't succeed again as it is; it says so and offers no Try again.
  @Test func aConflictedChangeOffersNoTryAgain() {
    var pending = PendingMutation(
      mutation: OfflineMutation(action: "append", playlistId: "p", track: song("1")))
    pending.state = "conflict"
    let conflicted = PendingChangeWords(pending)
    #expect(conflicted.failed)
    #expect(!conflicted.retryable)
    #expect(conflicted.status == "Couldn't send: the playlist changed.")
    #expect(!pending.retryable)
    pending.state = "rejected"
    #expect(PendingChangeWords(pending).retryable)
    #expect(PendingChangeWords(pending).status == "Couldn't send. Try again.")
    pending.state = "queued"
    #expect(!PendingChangeWords(pending).retryable)
  }

  @Test func discardSaysWhatHappened() {
    #expect(PendingChangeWords.discarded(.discarded) == "Discarded")
    #expect(PendingChangeWords.discarded(.alreadyGone) == "Already sent")
    #expect(PendingChangeWords.discarded(.failed) == "Couldn't discard. Try again.")
  }

  /// Codex round 1 on batch E: a Discard waits for a send under way, and a
  /// new send can't start while the Discard runs.
  @MainActor @Test func aDiscardWaitsForTheSendUnderWay() async {
    let gate = TurnGate()
    var log: [String] = []
    #expect(gate.tryEnter())
    let discard = Task { @MainActor in
      await gate.enter()
      log.append("discard")
      #expect(!gate.tryEnter())
      gate.leave()
    }
    await Task.yield()
    await Task.yield()
    log.append("send done")
    gate.leave()
    await discard.value
    #expect(log == ["send done", "discard"])
    #expect(!gate.busy)
  }
}
