import Foundation
import PigDomain
import PigStore
import PigSync
import Testing

@MainActor private final class Recorder {
  var batches: [[String]] = []
  var failure: (any Error)?
  var afterRecord: () -> Void = {}
  func record(_ events: [ListeningEvent]) throws {
    if let failure { throw failure }
    batches.append(events.map(\.id))
    afterRecord()
  }
}

@MainActor struct HistoryUploadTests {
  private let now = 1_800_000_000

  private func makeStore() async throws -> (LibraryStore, URL) {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    let store = try LibraryStore(path: root.appendingPathComponent("history.sqlite").path)
    try await store.replace(
      LibrarySnapshot(revision: "one", tracks: [Track(id: "song", title: "Song", artist: "A")]))
    return (store, root)
  }
  private func listen(_ id: String, track: String = "song", at playedAt: Int) -> ListeningEvent {
    ListeningEvent(eventId: id, trackId: track, playedAt: playedAt, seconds: 30)
  }

  @Test func sendsCurrentListensAndDropsTheRest() async throws {
    let (store, root) = try await makeStore()
    defer { try? FileManager.default.removeItem(at: root) }
    for event in [
      listen("kept", at: now - 60),
      listen("mix", track: "mix:radio-1", at: now - 60),
      listen("old", at: now - 31 * 86400),
      listen("future", at: now + 600),
      listen("gone", track: "removed", at: now - 60),
    ] {
      try await store.recordListen(event)
    }
    let recorder = Recorder()
    try await HistoryUpload.send(store: store, now: now, isCurrent: { true }) {
      try recorder.record($0)
    }
    #expect(recorder.batches.map { Set($0) } == [["kept", "mix"]])
    #expect(try await store.pendingListens().isEmpty)
  }

  @Test func aFailedSendKeepsTheListensQueued() async throws {
    let (store, root) = try await makeStore()
    defer { try? FileManager.default.removeItem(at: root) }
    try await store.recordListen(listen("kept", at: now - 60))
    let recorder = Recorder()
    recorder.failure = NativeFailure.authentication
    await #expect(throws: NativeFailure.authentication) {
      try await HistoryUpload.send(store: store, now: now, isCurrent: { true }) {
        try recorder.record($0)
      }
    }
    #expect(try await store.pendingListens().map(\.id) == ["kept"])
  }

  /// The account fence: listens are neither sent nor acknowledged for an
  /// account that ended meanwhile.
  @Test func anAccountChangeStopsTheUpload() async throws {
    let (store, root) = try await makeStore()
    defer { try? FileManager.default.removeItem(at: root) }
    try await store.recordListen(listen("kept", at: now - 60))
    let fence = AccountFence()
    let stamp = fence.generation
    let recorder = Recorder()
    recorder.afterRecord = { fence.end() }
    try await HistoryUpload.send(store: store, now: now, isCurrent: { fence.isCurrent(stamp) }) {
      try recorder.record($0)
    }
    #expect(recorder.batches == [["kept"]])
    #expect(try await store.pendingListens().map(\.id) == ["kept"])
    // Ended before sending: nothing goes.
    let later = Recorder()
    try await HistoryUpload.send(store: store, now: now, isCurrent: { false }) {
      try later.record($0)
    }
    #expect(later.batches.isEmpty)
  }
}
