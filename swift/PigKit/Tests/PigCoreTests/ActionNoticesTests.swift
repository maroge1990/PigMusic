import Foundation
import PigDomain
import Testing

// #245: a failed action says so on every page, beside the Undo bar.

@MainActor @Test func aNoticeShowsUntilItsTimeRunsOutOrItIsTapped() async throws {
  let notices = ActionNotices(lifetime: .milliseconds(60))
  let first = notices.post("That didn't work.")
  #expect(notices.current == first && notices.stack(over: nil) == [.notice(first)])
  notices.dismiss(first.id)
  #expect(notices.current == nil && notices.stack(over: nil).isEmpty)

  let second = notices.post("Still didn't work.", reference: "abc")
  #expect(second.id != first.id && second.reference == "abc")
  for _ in 0..<200 where notices.current != nil {
    try await Task.sleep(for: .milliseconds(10))
  }
  #expect(notices.current == nil)
}

@MainActor @Test func aNewerNoticeReplacesTheOlderAndKeepsItsOwnTime() async throws {
  let notices = ActionNotices(lifetime: .seconds(1))
  let older = notices.post("Older")
  try await Task.sleep(for: .milliseconds(300))
  let newer = notices.post("Newer")
  #expect(notices.current == newer)
  // Tapping away the older one, already replaced, leaves the newer showing.
  notices.dismiss(older.id)
  #expect(notices.current == newer)
  // The older one's time running out doesn't hide the newer one.
  try await Task.sleep(for: .milliseconds(800))
  #expect(notices.current == newer)
  notices.clear()
  #expect(notices.current == nil)
}

/// Codex on #245: a notice never hides a waiting Undo, whose time runs on;
/// it shows just above it.
@MainActor @Test func aNoticeLeavesAWaitingUndoShown() async throws {
  let undo = UndoQueue(wait: .seconds(600), linger: .zero)
  let notices = ActionNotices(lifetime: .seconds(600))
  undo.schedule("removed", label: "Removed Song") { true }
  let waiting = try #require(undo.items.last)
  #expect(notices.stack(over: waiting) == [.undo(waiting)])
  let notice = notices.post("That didn't work.")
  #expect(notices.stack(over: waiting) == [.notice(notice), .undo(waiting)])
  undo.schedule("hidden", label: "Hidden from Mix") { true }
  let newer = try #require(undo.items.last)
  #expect(notices.stack(over: newer) == [.notice(notice), .undo(newer)])
  notices.dismiss(notice.id)
  #expect(notices.stack(over: newer) == [.undo(newer)])
  undo.discardAll()
}
