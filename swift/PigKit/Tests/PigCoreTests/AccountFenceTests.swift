import PigDomain
import Testing

/// A slow server answer the test releases by hand.
@MainActor private final class Pending {
  private var waiting: CheckedContinuation<String, Never>?
  private var started: CheckedContinuation<Void, Never>?
  private var asked = false
  func answer() async -> String {
    asked = true
    started?.resume()
    started = nil
    return await withCheckedContinuation { waiting = $0 }
  }
  func waitUntilAsked() async {
    guard !asked else { return }
    await withCheckedContinuation { started = $0 }
  }
  func release(_ value: String) {
    waiting?.resume(returning: value)
    waiting = nil
  }
}

@MainActor struct AccountFenceTests {
  @Test func aStampIsCurrentUntilTheAccountChanges() throws {
    let fence = AccountFence()
    let stamp = fence.generation
    #expect(fence.isCurrent(stamp))
    try fence.check(stamp)
    fence.advance()
    #expect(!fence.isCurrent(stamp))
    #expect(throws: CancellationError.self) { try fence.check(stamp) }
    fence.end()
    #expect(fence.generation == stamp + 2)
  }

  /// The account fence: an answer started under one account never lands in
  /// the next one (the sign-out-while-loading race of #101).
  @Test func anAnswerForTheOldAccountIsDropped() async {
    let fence = AccountFence()
    let server = Pending()
    let load = Task { @MainActor () -> String? in
      try? await fence.fenced { await server.answer() }
    }
    await server.waitUntilAsked()
    fence.end()  // Signed out while the server was answering.
    fence.advance()  // And signed in to another account.
    server.release("old account's songs")
    let shown = await load.value
    #expect(shown == nil)
  }

  @Test func anAnswerForTheSameAccountIsKept() async throws {
    let fence = AccountFence()
    let value = try await fence.fenced { "songs" }
    #expect(value == "songs")
  }

  @Test func endingTheAccountCancelsItsTasks() async {
    let fence = AccountFence()
    let live = Task<Void, Never> {
      while !Task.isCancelled { try? await Task.sleep(for: .milliseconds(5)) }
    }
    fence.own("live", live)
    #expect(fence.task("live") != nil)
    fence.end()
    await live.value  // Returns only because it was cancelled.
    #expect(live.isCancelled)
    #expect(fence.task("live") == nil)
  }

  @Test func advancingKeepsTasksRunningButEndingStopsThem() {
    let fence = AccountFence()
    let poll = Task<Void, Never> { try? await Task.sleep(for: .seconds(60)) }
    fence.own("poll", poll)
    fence.advance()
    #expect(!poll.isCancelled)
    #expect(fence.task("poll") != nil)
    fence.end()
    #expect(poll.isCancelled)
  }

  @Test func replacingOrCancellingATaskByKeyCancelsIt() {
    let fence = AccountFence()
    let first = Task<Void, Never> { try? await Task.sleep(for: .seconds(60)) }
    let second = Task<Void, Never> { try? await Task.sleep(for: .seconds(60)) }
    fence.own("poll", first)
    fence.own("poll", second)
    #expect(first.isCancelled)
    #expect(!second.isCancelled)
    fence.cancel("poll")
    #expect(second.isCancelled)
    #expect(fence.task("poll") == nil)
  }
}
