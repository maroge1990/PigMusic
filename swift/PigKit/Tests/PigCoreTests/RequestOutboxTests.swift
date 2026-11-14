import Foundation
import PigDomain
import PigStore
import PigSync
import Testing

private struct Offline: Error {}

/// #206: a song request acts at once and waits on the iPhone until the
/// server confirms it, so being offline neither loses it nor locks the screen.
@MainActor struct RequestOutboxTests {
  private let first = Track(id: "first", title: "First", artist: "Artist")
  private let second = Track(id: "second", title: "Second", artist: "Artist")
  private let created = AcquisitionResult(
    created: 1, existing: 0, skipped: 0, downloadsEnabled: true)

  private func makeStore() throws -> (LibraryStore, URL) {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    return (try LibraryStore(path: root.appendingPathComponent("requests.sqlite").path), root)
  }

  @Test func eachSongIsRequestedOnceAndOnItsOwn() async throws {
    let (store, root) = try makeStore()
    defer { try? FileManager.default.removeItem(at: root) }
    try await RequestOutbox.add(first, to: store)
    try await RequestOutbox.add(second, to: store)
    try await RequestOutbox.add(first, to: store)
    #expect(await RequestOutbox.pending(store).map(\.id) == ["first", "second"])
    var sent: [[String]] = []
    let report = try await RequestOutbox.send(store: store, isCurrent: { true }) {
      sent.append($0.map(\.id))
      return created
    }
    #expect(report.stop == .done && report.requested == ["first", "second"])
    #expect(sent == [["first"], ["second"]])
    #expect(await RequestOutbox.pending(store).isEmpty)
  }

  @Test func aSongTheServerLeavesOutIsNotRequested() async throws {
    // Codex round 2: an answer of 200 with skipped=1 created no request.
    let (store, root) = try makeStore()
    defer { try? FileManager.default.removeItem(at: root) }
    try await RequestOutbox.add(first, to: store)
    try await RequestOutbox.add(second, to: store)
    let report = try await RequestOutbox.send(store: store, isCurrent: { true }) { tracks in
      tracks[0].id == "first"
        ? AcquisitionResult(created: 0, existing: 0, skipped: 1, downloadsEnabled: true)
        : AcquisitionResult(created: 0, existing: 1, skipped: 0, downloadsEnabled: false)
    }
    #expect(report.skipped == ["first"] && report.requested == ["second"])
    #expect(!report.downloadsEnabled)
    #expect(await RequestOutbox.pending(store).isEmpty)
  }

  @Test func aLeftOutOrRefusedSongIsExplained() {
    var report = RequestOutbox.Report()
    #expect(report.notice(titles: [:]) == nil)
    report.skipped = ["first"]
    #expect(
      report.notice(titles: ["first": "First"])
        == "First is excluded from this server's downloads, so it wasn't requested.")
    report.reason = "No."
    #expect(report.notice(titles: ["first": "First"]) == "No.")
  }

  @Test func offlineKeepsEveryRequestForTheNextTry() async throws {
    let (store, root) = try makeStore()
    defer { try? FileManager.default.removeItem(at: root) }
    try await RequestOutbox.add(first, to: store)
    try await RequestOutbox.add(second, to: store)
    var calls = 0
    let report = try await RequestOutbox.send(store: store, isCurrent: { true }) { _ in
      calls += 1
      throw Offline()
    }
    #expect(report.stop == .offline && calls == 1)
    #expect(await RequestOutbox.pending(store).map(\.id) == ["first", "second"])
    // The app closing in between changes nothing: the requests are stored.
    let reopened = try LibraryStore(path: root.appendingPathComponent("requests.sqlite").path)
    #expect(await RequestOutbox.pending(reopened).map(\.id) == ["first", "second"])
  }

  @Test func goingOfflineHalfwayKeepsOnlyTheUnanswered() async throws {
    let (store, root) = try makeStore()
    defer { try? FileManager.default.removeItem(at: root) }
    try await RequestOutbox.add(first, to: store)
    try await RequestOutbox.add(second, to: store)
    let report = try await RequestOutbox.send(store: store, isCurrent: { true }) { tracks in
      if tracks[0].id == "second" { throw Offline() }
      return created
    }
    #expect(report.stop == .offline && report.requested == ["first"])
    #expect(await RequestOutbox.pending(store).map(\.id) == ["second"])
  }

  @Test func aRequestMadeWhileSendingWaitsForTheNextSend() async throws {
    let (store, root) = try makeStore()
    defer { try? FileManager.default.removeItem(at: root) }
    try await RequestOutbox.add(first, to: store)
    let report = try await RequestOutbox.send(store: store, isCurrent: { true }) { _ in
      try await RequestOutbox.add(second, to: store)
      return created
    }
    #expect(report.requested == ["first"])
    #expect(await RequestOutbox.pending(store).map(\.id) == ["second"])
  }

  @Test func aRefusedRequestIsDroppedAndSaysWhy() async throws {
    let (store, root) = try makeStore()
    defer { try? FileManager.default.removeItem(at: root) }
    try await RequestOutbox.add(first, to: store)
    try await RequestOutbox.add(second, to: store)
    let report = try await RequestOutbox.send(store: store, isCurrent: { true }) { tracks in
      if tracks[0].id == "first" { throw NativeFailure.unavailable("No.") }
      return created
    }
    #expect(report.refused == ["first"] && report.reason == "No.")
    #expect(report.requested == ["second"] && report.stop == .done)
    #expect(await RequestOutbox.pending(store).isEmpty)
  }

  @Test func anEndedAccountSendsNothingAndKeepsNothingFromTheAnswer() async throws {
    let (store, root) = try makeStore()
    defer { try? FileManager.default.removeItem(at: root) }
    try await RequestOutbox.add(first, to: store)
    var asked = false
    let before = try await RequestOutbox.send(store: store, isCurrent: { false }) { _ in
      asked = true
      return created
    }
    #expect(before.stop == .stale && !asked)
    var current = true
    let after = try await RequestOutbox.send(store: store, isCurrent: { current }) { _ in
      current = false
      return created
    }
    #expect(after.stop == .stale && after.requested.isEmpty)
    #expect(await RequestOutbox.pending(store).map(\.id) == ["first"])
    let signedOut = try await RequestOutbox.send(store: store, isCurrent: { true }) { _ in
      throw NativeFailure.authentication
    }
    #expect(signedOut.stop == .signedOut)
    #expect(await RequestOutbox.pending(store).map(\.id) == ["first"])
  }
}
