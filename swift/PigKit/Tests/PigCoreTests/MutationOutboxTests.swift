import Foundation
import PigDomain
import PigStore
import Testing

struct MutationOutboxTests {
  @Test func durableOrderingAndConflictsStayWithinAccountDatabase() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: root) }
    let path = root.appendingPathComponent("person-one.sqlite").path
    let first = OfflineMutation(action: "create", name: "Queued playlist")
    let second = OfflineMutation(
      action: "append", playlistId: first.id,
      track: Track(id: "song", title: "Song", artist: "Artist"))
    let store = try LibraryStore(path: path)
    try await store.enqueueMutation(first)
    try await store.enqueueMutation(first)
    try await store.enqueueMutation(second)
    let restored = try LibraryStore(path: path)
    #expect(try await restored.pendingMutations().map(\.id) == [first.id, second.id])
    var pending = try #require(await restored.pendingMutations().first)
    pending.state = "conflict"
    pending.message = "Review current playlist"
    try await restored.updateMutation(pending)
    #expect(try await store.pendingMutations().first?.state == "conflict")
    let other = try LibraryStore(path: root.appendingPathComponent("person-two.sqlite").path)
    #expect(try await other.pendingMutations().isEmpty)
    try await restored.acknowledgeMutation(first.id)
    #expect(try await store.pendingMutations().map(\.id) == [second.id])
  }

  /// Codex round 1 on batch G (#285): a new playlist and its songs enter the
  /// outbox together or not at all. With 999 edits waiting, the create used
  /// to take the last place and its first song was refused.
  @Test func aGroupThatDoesNotFitLeavesNothingBehind() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: root) }
    let store = try LibraryStore(path: root.appendingPathComponent("person.sqlite").path)
    for index in 0..<999 {
      try await store.enqueueMutation(OfflineMutation(action: "create", name: "List \(index)"))
    }
    let song = Track(id: "song", title: "Song", artist: "Artist")
    let group = NewPlaylistEdits.make(name: "Road trip", songs: [song])
    await #expect(throws: MutationRejection.self) { try await store.enqueueMutations(group) }
    #expect(try await store.pendingMutations().count == 999)
    // One that fits goes in whole, in order.
    try await store.acknowledgeMutation(store.pendingMutations()[0].id)
    try await store.enqueueMutations(group)
    #expect(try await store.pendingMutations().suffix(2).map(\.id) == group.map(\.id))
  }
}
