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

  /// #264: a song the person paused stays paused when added again; it is
  /// reported paused, with where to resume it, never as excluded.
  @Test func aSongPausedBeforeIsReportedPausedNotExcluded() async throws {
    let (store, root) = try makeStore()
    defer { try? FileManager.default.removeItem(at: root) }
    try await RequestOutbox.add(first, to: store)
    let report = try await RequestOutbox.send(store: store, isCurrent: { true }) { _ in
      AcquisitionResult(
        created: 0, existing: 0, skipped: 0, paused: 1, pausedIds: ["request"],
        downloadsEnabled: true)
    }
    #expect(report.paused == ["first"])
    // Under the request the server named, which Resume clears (#276).
    #expect(report.pausedRequests == ["first": ["request"]])
    #expect(report.skipped.isEmpty && report.requested.isEmpty)
    #expect(report.notice(titles: ["first": "First"]) == "Paused. Resume it in Downloads.")
    #expect(await RequestOutbox.pending(store).isEmpty)
  }

  /// D-128: past the rate a song a queue limit left out stays in the
  /// outbox and the pass ends there (every later new song would be refused
  /// too); the notice says how many wait to be sent.
  @Test func aSongPastTheRateIsKeptAndEndsThePass() async throws {
    let (store, root) = try makeStore()
    defer { try? FileManager.default.removeItem(at: root) }
    try await RequestOutbox.add(first, to: store)
    try await RequestOutbox.add(second, to: store)
    var sent: [String] = []
    let report = try await RequestOutbox.send(store: store, isCurrent: { true }) { tracks in
      sent.append(tracks[0].id)
      return .limited(1, limit: "rate", retryAfter: 30)
    }
    #expect(sent == ["first"])
    #expect(report.stop == .limited && report.waiting == 2)
    #expect(report.limit == "rate" && report.retryAfter == 30)
    #expect(report.skipped.isEmpty && report.requested.isEmpty)
    #expect(await RequestOutbox.pending(store).map(\.id) == ["first", "second"])
    #expect(
      report.notice(titles: [:]) == "2 songs will be added later.")
    // Once there is room both go, and nothing waits.
    let later = try await RequestOutbox.send(store: store, isCurrent: { true }) { _ in created }
    #expect(later.requested == ["first", "second"] && later.waiting == 0)
    #expect(later.stop == .done)
    #expect(await RequestOutbox.pending(store).isEmpty)
  }

  /// Codex round 1: a song at a waiting limit at the head of the outbox
  /// kept every later one from being sent, though a later one could join a
  /// job already queued. The pass goes on past it.
  @Test func aSongPastAWaitingLimitIsKeptAndThePassGoesOn() async throws {
    let (store, root) = try makeStore()
    defer { try? FileManager.default.removeItem(at: root) }
    try await RequestOutbox.add(first, to: store)
    try await RequestOutbox.add(second, to: store)
    let report = try await RequestOutbox.send(store: store, isCurrent: { true }) { tracks in
      tracks[0].id == "first"
        ? .limited(1, limit: "person_waiting", retryAfter: nil)
        : AcquisitionResult(created: 0, existing: 1, skipped: 0, downloadsEnabled: true)
    }
    #expect(report.requested == ["second"])
    #expect(report.stop == .limited && report.waiting == 1)
    #expect(report.limit == "person_waiting")
    #expect(await RequestOutbox.pending(store).map(\.id) == ["first"])
  }

  @Test func aLeftOutOrRefusedSongIsExplained() {
    var report = RequestOutbox.Report()
    #expect(report.notice(titles: [:]) == nil)
    report.skipped = ["first"]
    #expect(
      report.notice(titles: ["first": "First"])
        == "Couldn't add First: it's excluded from this server's downloads.")
    report.reason = "No."
    #expect(report.notice(titles: ["first": "First"]) == "No.")
  }

  /// Codex round 1 on batch D: a paused song is still told beside a song
  /// left out or refused.
  @Test func aPausedSongIsToldBesideAnExclusionOrARefusal() {
    var excluded = RequestOutbox.Report()
    excluded.skipped = ["first"]
    excluded.paused = ["second"]
    #expect(
      excluded.notice(titles: ["first": "First"])
        == "Couldn't add First: it's excluded from this server's downloads. Paused. Resume it in Downloads."
    )
    var refused = RequestOutbox.Report()
    refused.refused = ["first"]
    refused.reason = "No."
    refused.paused = ["second"]
    #expect(refused.notice(titles: [:]) == "No. Paused. Resume it in Downloads.")
  }

  /// #245: one drain of the outbox says what was refused once, with how
  /// many songs when several were.
  @Test func severalRefusedSongsAreOneNotice() {
    var drained = RequestOutbox.Report()
    var first = RequestOutbox.Report()
    first.refused = ["first"]
    first.reason = "That didn't work: something went wrong on the server. Reference 01234567"
    drained.absorb(first)
    #expect(drained.notice(titles: [:]) == first.reason)
    var second = RequestOutbox.Report()
    second.refused = ["second"]
    second.reason = "No."
    drained.absorb(second)
    #expect(drained.refused == ["first", "second"])
    #expect(drained.notice(titles: [:]) == "Couldn't add 2 songs: No.")
    var report = RequestOutbox.Report()
    report.refused = ["first", "second", "third"]
    report.reason = "No."
    #expect(report.notice(titles: [:]) == "Couldn't add 3 songs: No.")
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
