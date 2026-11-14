import Foundation
import PigDomain
import PigStore
import PigSync
import Testing

/// A server that answers each edit as the test says, and records what it saw.
@MainActor private final class Server {
  var answers: [String: any Error] = [:]
  var seen: [String] = []
  var afterEach: () -> Void = {}
  func apply(_ mutation: OfflineMutation) throws -> OfflineMutationReceipt {
    seen.append(mutation.id)
    afterEach()
    if let failure = answers[mutation.id] { throw failure }
    return OfflineMutationReceipt(operationId: mutation.id, applied: true)
  }
}

private struct Offline: Error {}

@MainActor struct EditOutboxTests {
  private let song = Track(id: "song", title: "Song", artist: "Artist")

  private func makeStore() throws -> (LibraryStore, URL) {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    return (try LibraryStore(path: root.appendingPathComponent("edits.sqlite").path), root)
  }

  @Test func sendsEveryEditInOrderAndAcknowledgesIt() async throws {
    let (store, root) = try makeStore()
    defer { try? FileManager.default.removeItem(at: root) }
    let first = OfflineMutation(action: "create", name: "Run")
    let second = OfflineMutation(action: "favourite", track: song, favourite: true)
    try await store.enqueueMutation(first)
    try await store.enqueueMutation(second)
    let server = Server()
    var applied = false
    let stop = try await EditOutbox.send(store: store, isCurrent: { true }, applied: &applied) {
      try server.apply($0)
    }
    #expect(stop == .sent && applied)
    #expect(server.seen == [first.id, second.id])
    #expect(try await store.pendingMutations().isEmpty)
  }

  @Test func aConflictHoldsLaterEditsToTheSamePlaylistOnly() async throws {
    let (store, root) = try makeStore()
    defer { try? FileManager.default.removeItem(at: root) }
    let conflicting = OfflineMutation(action: "append", playlistId: "a", track: song)
    let sameList = OfflineMutation(action: "remove", playlistId: "a", position: 0)
    let otherList = OfflineMutation(action: "append", playlistId: "b", track: song)
    let rejected = OfflineMutation(action: "favourite", track: song, favourite: true)
    for edit in [conflicting, sameList, otherList, rejected] { try await store.enqueueMutation(edit) }
    let server = Server()
    server.answers = [
      conflicting.id: NativeFailure.changed, rejected.id: MutationRejection.rejected,
    ]
    var applied = false
    let stop = try await EditOutbox.send(store: store, isCurrent: { true }, applied: &applied) {
      try server.apply($0)
    }
    #expect(stop == .sent && applied)
    #expect(server.seen == [conflicting.id, otherList.id, rejected.id])
    let left = try await store.pendingMutations()
    #expect(left.map(\.id) == [conflicting.id, sameList.id, rejected.id])
    #expect(left.map(\.state) == ["conflict", "queued", "rejected"])
  }

  @Test func aNetworkFailureStopsEverything() async throws {
    let (store, root) = try makeStore()
    defer { try? FileManager.default.removeItem(at: root) }
    let first = OfflineMutation(action: "create", name: "One")
    let second = OfflineMutation(action: "create", name: "Two")
    try await store.enqueueMutation(first)
    try await store.enqueueMutation(second)
    let server = Server()
    server.answers = [first.id: Offline()]
    var applied = false
    let stop = try await EditOutbox.send(store: store, isCurrent: { true }, applied: &applied) {
      try server.apply($0)
    }
    #expect(stop == .sent && !applied)
    #expect(server.seen == [first.id])
    #expect(try await store.pendingMutations().count == 2)
  }

  /// The account fence: an edit answered after a sign-out is not acknowledged
  /// and nothing more is sent.
  @Test func anAccountChangeStopsAtOnce() async throws {
    let (store, root) = try makeStore()
    defer { try? FileManager.default.removeItem(at: root) }
    let first = OfflineMutation(action: "create", name: "One")
    let second = OfflineMutation(action: "create", name: "Two")
    try await store.enqueueMutation(first)
    try await store.enqueueMutation(second)
    let fence = AccountFence()
    let stamp = fence.generation
    let server = Server()
    server.afterEach = { fence.end() }
    var applied = false
    let stop = try await EditOutbox.send(
      store: store, isCurrent: { fence.isCurrent(stamp) }, applied: &applied
    ) { try server.apply($0) }
    #expect(stop == .stale && !applied)
    #expect(server.seen == [first.id])
    #expect(try await store.pendingMutations().count == 2)
  }

  @Test func aRevokedSessionStopsAndSaysSo() async throws {
    let (store, root) = try makeStore()
    defer { try? FileManager.default.removeItem(at: root) }
    let first = OfflineMutation(action: "create", name: "One")
    let second = OfflineMutation(action: "create", name: "Two")
    try await store.enqueueMutation(first)
    try await store.enqueueMutation(second)
    let server = Server()
    server.answers = [first.id: NativeFailure.authentication]
    var applied = false
    let stop = try await EditOutbox.send(store: store, isCurrent: { true }, applied: &applied) {
      try server.apply($0)
    }
    #expect(stop == .signedOut && !applied)
    #expect(server.seen == [first.id])
  }

  @Test func editsAreScopedByWhatTheyChange() {
    #expect(
      EditOutbox.scope(OfflineMutation(action: "append", playlistId: "p", track: song))
        == "playlist:p")
    #expect(
      EditOutbox.scope(OfflineMutation(action: "favourite", track: song, favourite: true))
        == "favourite:song")
    #expect(EditOutbox.scope(OfflineMutation(action: "create", name: "Run")) == "create:Run")
  }

  @Test func onlyEditsTheServerAppliedAreConfirmed() async throws {
    // Codex (batch 9): "confirmed" meant "no longer waiting", which a discard
    // also gives, so a refused removal could shift the next one.
    let (store, root) = try makeStore()
    defer { try? FileManager.default.removeItem(at: root) }
    let refused = OfflineMutation(action: "remove", playlistId: "a", revision: 0, position: 0)
    let other = OfflineMutation(action: "append", playlistId: "b", track: song)
    for edit in [refused, other] { try await store.enqueueMutation(edit) }
    let server = Server()
    server.answers = [refused.id: NativeFailure.changed]
    var applied = false
    var confirmations: [String] = []
    _ = try await EditOutbox.send(
      store: store, isCurrent: { true }, applied: &applied, confirmed: { confirmations.append($0) }
    ) { try server.apply($0) }
    #expect(confirmations == [other.id])
    // Discarded after the conflict: gone from the outbox, still not confirmed.
    try await store.acknowledgeMutation(refused.id)
    #expect(try await store.pendingMutations().isEmpty && confirmations == [other.id])
  }
}
