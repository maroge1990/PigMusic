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

  private struct LostReply: Error {}

  /// Sends the outbox, recording what reached the API.
  private func send(
    _ store: LibraryStore, seen: inout [String], failing: Bool = false
  ) async throws {
    var applied = false
    var reached: [String] = []
    _ = try await EditOutbox.send(store: store, isCurrent: { true }, applied: &applied) {
      reached.append($0.id)
      if failing { throw LostReply() }
      return OfflineMutationReceipt(operationId: $0.id, applied: true)
    }
    seen += reached
  }

  /// Codex confirmation on batch E: a playlist deleted offline, whose send
  /// was tried and lost on reconnecting, is Discarded: nothing more reaches
  /// the API; it says it couldn't tell, and Remove from this list forgets it.
  @Test func discardAfterALostSendNeverSendsIt() async throws {
    let (store, root) = try makeStore()
    defer { try? FileManager.default.removeItem(at: root) }
    let delete = OfflineMutation(action: "delete", playlistId: "p", revision: 3)
    try await store.enqueueMutation(delete)
    var seen: [String] = []
    try await send(store, seen: &seen, failing: true)
    #expect(seen == [delete.id])
    let discard = await EditOutbox.discard(delete.id, store: store) { true }
    #expect(discard?.0 == .unknown)
    #expect(discard?.1.first?.withdrawn == true)
    seen = []
    try await send(store, seen: &seen)
    #expect(seen.isEmpty)
    let row = try await store.pendingMutations()
    #expect(PendingChangeWords(row[0]).status
      == "Couldn't tell whether this change was sent. Check the playlist.")
    #expect(!PendingChangeWords(row[0]).retryable)
    #expect(try await EditOutbox.retry(delete.id, store: store) { true }?.first?.withdrawn == true)
    #expect(try await EditOutbox.forget(delete.id, store: store) { true }?.isEmpty == true)
    // A later edit to the same playlist isn't held back by it.
    let later = OfflineMutation(action: "append", playlistId: "p")
    try await store.enqueueMutation(later)
    #expect(await EditOutbox.discard(later.id, store: store) { true }?.0 == .discarded)
  }

  /// An edit saved by an older build has no send marker: it may have been
  /// sent, so Discard withdraws it and it is never sent.
  @Test func discardWithdrawsALegacyEdit() async throws {
    let (store, root) = try makeStore()
    defer { try? FileManager.default.removeItem(at: root) }
    let delete = OfflineMutation(action: "delete", playlistId: "p", revision: 3)
    try await store.enqueueMutation(delete)
    var legacy = try await store.pendingMutations()[0]
    legacy.attempted = nil
    try await store.updateMutation(legacy)
    #expect(await EditOutbox.discard(delete.id, store: store) { true }?.0 == .unknown)
    var seen: [String] = []
    try await send(store, seen: &seen)
    #expect(seen.isEmpty)
  }

  /// An edit never sent is simply discarded, and never sent.
  @Test func discardRemovesAnEditNeverSent() async throws {
    let (store, root) = try makeStore()
    defer { try? FileManager.default.removeItem(at: root) }
    let edit = OfflineMutation(action: "create", name: "Run")
    try await store.enqueueMutation(edit)
    #expect(await EditOutbox.discard(edit.id, store: store) { true }?.0 == .discarded)
    var seen: [String] = []
    try await send(store, seen: &seen)
    #expect(seen.isEmpty)
    #expect(try await store.pendingMutations().isEmpty)
  }

  /// Codex round 2 on batch E: the outbox read at the end of a send is shown
  /// only if its account is still the current one when the read returns.
  @Test func anOutboxReadOutlivingItsAccountIsNotShown() async throws {
    let (store, root) = try makeStore()
    defer { try? FileManager.default.removeItem(at: root) }
    try await store.enqueueMutation(OfflineMutation(action: "create", name: "Run"))
    var checks = 0
    // Current when the read starts, switched by the time it returns.
    let read = try await EditOutbox.pending(store: store) {
      checks += 1
      return checks < 2
    }
    #expect(read == nil)
    #expect(try await EditOutbox.pending(store: store) { true }?.count == 1)
  }
}
