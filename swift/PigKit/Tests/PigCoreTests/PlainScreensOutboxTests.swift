import Foundation
import PigDomain
import PigStore
import PigSync
import Testing

/// Codex round 1 on batch E: Try again and Discard on Changes not sent yet.
@MainActor struct PlainScreensOutboxTests {
  private func makeStore() throws -> (LibraryStore, URL) {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    return (try LibraryStore(path: root.appendingPathComponent("edits.sqlite").path), root)
  }

  /// The account ends while Try again reads the outbox: nothing read is
  /// handed back to be shown, sent or refreshed for the next account.
  @Test func tryAgainForAnEndedAccountHandsNothingBack() async throws {
    let (store, root) = try makeStore()
    defer { try? FileManager.default.removeItem(at: root) }
    let edit = OfflineMutation(action: "append", playlistId: "p")
    try await store.enqueueMutation(edit)
    var refused = try await store.pendingMutations()[0]
    refused.state = "rejected"
    try await store.updateMutation(refused)
    // Current when asked, ended once the outbox has been read.
    var checks = 0
    let stale = try await EditOutbox.retry(edit.id, store: store) {
      checks += 1
      return checks < 2
    }
    #expect(stale == nil)
    let fresh = try await EditOutbox.retry(edit.id, store: store) { true }
    #expect(fresh?.first?.state == "queued")
  }

  /// A conflicted edit is never put back in line by Try again.
  @Test func tryAgainLeavesAConflictedEditAlone() async throws {
    let (store, root) = try makeStore()
    defer { try? FileManager.default.removeItem(at: root) }
    let edit = OfflineMutation(action: "append", playlistId: "p")
    try await store.enqueueMutation(edit)
    var conflicted = try await store.pendingMutations()[0]
    conflicted.state = "conflict"
    try await store.updateMutation(conflicted)
    let after = try await EditOutbox.retry(edit.id, store: store) { true }
    #expect(after?.first?.state == "conflict")
  }

  /// Discard says whether it took the edit out or found it already sent.
  @Test func discardSaysWhetherTheEditWasStillWaiting() async throws {
    let (store, root) = try makeStore()
    defer { try? FileManager.default.removeItem(at: root) }
    let edit = OfflineMutation(action: "create", name: "Run")
    try await store.enqueueMutation(edit)
    let first = await EditOutbox.discard(edit.id, store: store) { true }
    #expect(first?.0 == .discarded)
    #expect(first?.1.isEmpty == true)
    let again = await EditOutbox.discard(edit.id, store: store) { true }
    #expect(again?.0 == .alreadyGone)
    #expect(await EditOutbox.discard(edit.id, store: store) { false } == nil)
  }
}
