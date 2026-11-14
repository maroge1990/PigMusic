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
