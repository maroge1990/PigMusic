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

  /// Codex round 1 on batch 13 (#212): a favourite the server refuses must
  /// read as refused, not as saved on this phone, and must not count as the
  /// song's favourite state afterwards.
  @Test func aRefusedFavouriteReadsAsRefusedAndNeverAsTheState() async throws {
    let (store, root) = try makeStore()
    defer { try? FileManager.default.removeItem(at: root) }
    let refused = OfflineMutation(action: "favourite", track: song, favourite: true)
    let other = Track(id: "other", title: "Other", artist: "Artist")
    let saved = OfflineMutation(action: "favourite", track: other, favourite: true)
    try await store.enqueueMutation(refused)
    try await store.enqueueMutation(saved)
    let server = Server()
    server.answers = [refused.id: MutationRejection.rejected]
    var applied = false
    var confirmed: Set<String> = []
    _ = try await EditOutbox.send(
      store: store, isCurrent: { true }, applied: &applied, confirmed: { confirmed.insert($0) }
    ) { try server.apply($0) }
    let pending = try await store.pendingMutations()
    #expect(
      EditOutbox.outcome(of: refused.id, confirmed: confirmed.contains(refused.id), pending: pending)
        == .refused)
    #expect(
      EditOutbox.outcome(of: saved.id, confirmed: confirmed.contains(saved.id), pending: pending)
        == .confirmed)
    #expect(EditOutbox.favourite(song.id, in: pending) == nil)
    // Offline, an edit waits and its favourite is the state shown.
    let waiting = OfflineMutation(action: "favourite", track: other, favourite: false)
    try await store.enqueueMutation(waiting)
    server.answers = [waiting.id: Offline()]
    _ = try await EditOutbox.send(store: store, isCurrent: { true }, applied: &applied) {
      try server.apply($0)
    }
    let later = try await store.pendingMutations()
    #expect(EditOutbox.outcome(of: waiting.id, confirmed: false, pending: later) == .queued)
    #expect(EditOutbox.favourite(other.id, in: later) == false)
    // An edit the person discarded is refused too: nothing confirmed it.
    #expect(EditOutbox.outcome(of: "gone", confirmed: false, pending: later) == .refused)
  }

  /// Codex round 2 on batch 13 (#212): a favourite tapped offline and refused
  /// on a later send leaves the outbox, its song shows the state before it
  /// (or a newer tap still waiting), and the next tap is sent.
  @Test func aDeferredFavouriteRefusalIsSettledAndLaterTapsAreSent() async throws {
    let (store, root) = try makeStore()
    defer { try? FileManager.default.removeItem(at: root) }
    let other = Track(id: "other", title: "Other", artist: "Artist")
    let tap = OfflineMutation(action: "favourite", track: song, favourite: true)
    let first = OfflineMutation(action: "favourite", track: other, favourite: true)
    let newer = OfflineMutation(action: "favourite", track: other, favourite: false)
    for edit in [tap, first, newer] { try await store.enqueueMutation(edit) }
    let server = Server()
    var applied = false
    // Offline: everything waits.
    server.answers = [tap.id: Offline()]
    _ = try await EditOutbox.send(store: store, isCurrent: { true }, applied: &applied) {
      try server.apply($0)
    }
    #expect(try await store.pendingMutations().count == 3)
    // Later the server refuses two; the newer tap waits behind its song's.
    server.answers = [tap.id: MutationRejection.rejected, first.id: MutationRejection.rejected]
    _ = try await EditOutbox.send(store: store, isCurrent: { true }, applied: &applied) {
      try server.apply($0)
    }
    let settled = try await EditOutbox.settleRefusedFavourites(store: store)
    #expect(
      settled.map(\.trackId) == [song.id, other.id]
        // No tap left to show for song: it is read from the server again.
        && settled.map(\.value) == [nil, false])
    #expect(try await store.pendingMutations().map(\.id) == [newer.id])
    // The next tap on the refused song, and the waiting one, are sent.
    let again = OfflineMutation(action: "favourite", track: song, favourite: true)
    try await store.enqueueMutation(again)
    server.answers = [:]
    server.seen = []
    _ = try await EditOutbox.send(store: store, isCurrent: { true }, applied: &applied) {
      try server.apply($0)
    }
    #expect(server.seen == [newer.id, again.id])
    #expect(try await store.pendingMutations().isEmpty)
    #expect(try await EditOutbox.settleRefusedFavourites(store: store).isEmpty)
  }

  /// Codex round 2 on batch G (#285): a new playlist the server refuses
  /// leaves the outbox with its songs, so nothing waits behind a playlist
  /// that will never exist; an edit to another playlist queued in between
  /// stays and is sent. A create that only failed to connect takes nothing.
  @Test func aRefusedNewPlaylistLeavesWithItsSongs() async throws {
    let (store, root) = try makeStore()
    defer { try? FileManager.default.removeItem(at: root) }
    let group = NewPlaylistEdits.make(name: "Road trip", songs: [song, song])
    let other = OfflineMutation(action: "rename", playlistId: "existing", name: "B", revision: 1)
    try await store.enqueueMutations(group)
    try await store.enqueueMutation(other)
    let server = Server()
    var applied = false
    server.answers = [group[0].id: Offline()]
    _ = try await EditOutbox.send(store: store, isCurrent: { true }, applied: &applied) {
      try server.apply($0)
    }
    #expect(try await EditOutbox.settleRefusedCreates(store: store).isEmpty)
    #expect(try await store.pendingMutations().count == 4)
    server.answers = [group[0].id: MutationRejection.rejected]
    server.seen = []
    _ = try await EditOutbox.send(store: store, isCurrent: { true }, applied: &applied) {
      try server.apply($0)
    }
    #expect(server.seen == [group[0].id, other.id])
    #expect(try await EditOutbox.settleRefusedCreates(store: store) == ["Road trip"])
    #expect(try await store.pendingMutations().isEmpty)
  }

  /// Codex round 2 on batch G: discarding a new playlist's create by hand
  /// discards the songs waiting to be added to it, and nothing else.
  @Test func discardingANewPlaylistDiscardsItsSongs() async throws {
    let (store, root) = try makeStore()
    defer { try? FileManager.default.removeItem(at: root) }
    let group = NewPlaylistEdits.make(name: "Road trip", songs: [song])
    let favourite = OfflineMutation(action: "favourite", track: song, favourite: true)
    try await store.enqueueMutations(group)
    try await store.enqueueMutation(favourite)
    let result = await EditOutbox.discard(group[0].id, store: store) { true }
    #expect(result?.0 == .discarded)
    #expect(result?.1.map(\.id) == [favourite.id])
  }

  /// Codex confirmation on #212: refusals on successive sends must never
  /// show a value that neither the server nor a waiting tap holds. With no
  /// tap left, the song's state is unknown until the server is read again.
  @Test(arguments: [[true, false], [true, false, true]])
  func successiveRefusalsNeverInventAState(taps values: [Bool]) async throws {
    let (store, root) = try makeStore()
    defer { try? FileManager.default.removeItem(at: root) }
    var states = FavouriteStates()
    states.answer(song.id, false, since: states.revision(song.id))  // Confirmed false.
    var edits: [OfflineMutation] = []
    for value in values {
      let tap = states.tap(song.id)
      #expect(tap?.value == value)
      let edit = OfflineMutation(action: "favourite", track: song, favourite: value)
      try await store.enqueueMutation(edit)
      edits.append(edit)
      // Its save ends once it waits in the outbox, as in the app.
      if let tap { states.settled(tap) }
      // The app caches each tap as the star shows it.
      try await store.cache(value, key: EditOutbox.favouriteCacheKey(song.id))
    }
    let server = Server()
    var applied = false
    // Each send refuses the first tap still waiting; the rest wait behind it.
    for (index, edit) in edits.enumerated() {
      server.answers = [edit.id: MutationRejection.rejected]
      _ = try await EditOutbox.send(store: store, isCurrent: { true }, applied: &applied) {
        try server.apply($0)
      }
      let settled = try await EditOutbox.settleRefusedFavourites(store: store)
      #expect(settled.count == 1 && settled.first?.trackId == song.id)
      if index < edits.count - 1 {
        // The newest tap still waiting is what shows.
        #expect(settled.first?.value == values.last)
      } else {
        #expect(settled.first?.value == nil)
      }
      states.refusal(settled[0])
    }
    #expect(try await store.pendingMutations().isEmpty)
    #expect(states[song.id] == nil)  // Unknown, not an unsaved guess.
    // Nor does the phone's cache bring the unsaved tap back on a later load.
    #expect(
      try await store.cached(EditOutbox.favouriteCacheKey(song.id), as: Bool.self) == nil)
    // The server's answer, read again, is what shows.
    let reread = states.revision(song.id)
    states.answer(song.id, false, since: reread)
    #expect(states[song.id] == false)
  }
}
