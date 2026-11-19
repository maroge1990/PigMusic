import Foundation
import PigDomain
import PigStore

/// Sends the edits waiting on this phone to the server, oldest first (#46).
///
/// Edits to one playlist (or one song's favourite) stay in order: once one
/// conflicts or is rejected, later edits to the same thing wait for review.
/// Edits to anything else still go through. A network failure stops all.
@MainActor public enum EditOutbox {
  public enum Stop: Equatable, Sendable {
    /// Every edit that could be sent was tried.
    case sent
    /// The account changed, or the server's receipt did not match: stop at
    /// once and touch nothing more for this account.
    case stale
    /// The server ended this device's session.
    case signedOut
  }

  /// What became of one edit after a send (Codex on #212).
  public enum Outcome: Equatable, Sendable {
    /// The server's receipt says it applied it.
    case confirmed
    /// Still waiting on this phone (offline, or behind another edit).
    case queued
    /// Rejected, in conflict, or discarded: it will not be applied as made.
    case refused
  }
  public nonisolated static func outcome(
    of id: String, confirmed: Bool, pending: [PendingMutation]
  ) -> Outcome {
    if confirmed { return .confirmed }
    guard let item = pending.first(where: { $0.id == id }) else { return .refused }
    return item.state == "queued" ? .queued : .refused
  }
  /// A song's favourite as the newest edit still waiting to be sent has it;
  /// a refused one is not the song's state.
  public nonisolated static func favourite(_ trackId: String, in pending: [PendingMutation])
    -> Bool?
  {
    pending.last {
      $0.state == "queued" && $0.mutation.action == "favourite" && $0.mutation.track?.id == trackId
    }?.mutation.favourite
  }

  /// Where this phone caches a song's favourite state.
  public nonisolated static func favouriteCacheKey(_ trackId: String) -> String {
    "favourite:" + trackId
  }

  /// Favourite edits the server refused, on any send, also one made long
  /// after the tap (Codex round 2 on #212): each leaves the outbox, so later
  /// taps on its song aren't held behind it, and its song shows the newest
  /// favourite still waiting, or none (read it from the server again). One
  /// answer per song, in the order the refused edits were made.
  public static func settleRefusedFavourites(store: LibraryStore) async throws
    -> [RefusedFavourite]
  {
    let refused = try await store.pendingMutations().filter {
      $0.mutation.action == "favourite" && $0.state != "queued"
    }
    guard !refused.isEmpty else { return [] }
    for item in refused { try await store.acknowledgeMutation(item.id) }
    let remaining = try await store.pendingMutations()
    var songs: [String] = []
    for id in refused.compactMap(\.mutation.track?.id) where !songs.contains(id) {
      songs.append(id)
    }
    // A refused edit never says what the server holds: only a newer tap
    // still waiting is shown; without one the state is read again
    // (Codex confirmation on #212).
    // An unknown state leaves no cached guess behind either: a later load
    // reads the server, and offline the star stays unknown.
    for id in songs where favourite(id, in: remaining) == nil {
      try await store.removeCached(favouriteCacheKey(id))
    }
    return songs.map { id in
      RefusedFavourite(
        trackId: id, value: favourite(id, in: remaining),
        track: refused.last { $0.mutation.track?.id == id }?.mutation.track)
    }
  }

  /// Try again (D-120): a refused edit that can be sent again waits once
  /// more. The outbox as it now stands, or nil when the account ended
  /// meanwhile: nothing read here may be shown for another account (Codex
  /// round 1 on batch E). A conflicted edit is never retried.
  public static func retry(_ id: String, store: LibraryStore, isCurrent: () -> Bool)
    async throws -> [PendingMutation]?
  {
    guard isCurrent() else { return nil }
    let pending = try await store.pendingMutations()
    guard isCurrent() else { return nil }
    guard let item = pending.first(where: { $0.id == id }), item.retryable else { return pending }
    try await store.updateMutation(item.retried())
    guard isCurrent() else { return nil }
    let after = try await store.pendingMutations()
    return isCurrent() ? after : nil
  }

  /// Discard: takes the edit out of the outbox if it is still there. Run it
  /// in the outbox's turn (`TurnGate`), so no send is under way. nil when
  /// the account ended meanwhile.
  public static func discard(_ id: String, store: LibraryStore, isCurrent: () -> Bool)
    async -> (DiscardResult, [PendingMutation])?
  {
    guard isCurrent() else { return nil }
    do {
      let pending = try await store.pendingMutations()
      guard isCurrent() else { return nil }
      guard pending.contains(where: { $0.id == id }) else { return (.alreadyGone, pending) }
      try await store.acknowledgeMutation(id)
      guard isCurrent() else { return nil }
      let after = try await store.pendingMutations()
      return isCurrent() ? (.discarded, after) : nil
    } catch {
      let pending = (try? await store.pendingMutations()) ?? []
      return isCurrent() ? (.failed, pending) : nil
    }
  }

  /// What an edit changes, for keeping dependent edits in order.
  public nonisolated static func scope(_ mutation: OfflineMutation) -> String {
    if let playlist = mutation.playlistId { return "playlist:" + playlist }
    if let track = mutation.track { return mutation.action + ":" + track.id }
    return mutation.action + ":" + (mutation.name ?? "")
  }

  /// Sends the queued edits in `store` through `apply`. `applied` becomes true
  /// once the server applied one, also when a store error is thrown later.
  /// `isCurrent` is asked before each edit and after each answer, so nothing
  /// is acknowledged for an account that has ended. `confirmed` hears the ID
  /// of each edit the server's receipt says it applied, and of no other: an
  /// edit leaving the outbox can also mean the person discarded it (Codex).
  public static func send(
    store: LibraryStore, isCurrent: () -> Bool, applied: inout Bool,
    confirmed: (String) -> Void = { _ in },
    apply: (OfflineMutation) async throws -> OfflineMutationReceipt
  ) async throws -> Stop {
    var blocked: Set<String> = []
    sending: for item in try await store.pendingMutations() {
      guard isCurrent() else { return .stale }
      let scope = Self.scope(item.mutation)
      guard item.state == "queued", !blocked.contains(scope) else {
        blocked.insert(scope)
        continue
      }
      do {
        let receipt = try await apply(item.mutation)
        guard receipt.applied, receipt.operationId == item.id, isCurrent() else { return .stale }
        try await store.acknowledgeMutation(item.id)
        applied = true
        confirmed(item.id)
      } catch NativeFailure.authentication {
        return .signedOut
      } catch NativeFailure.changed {
        var changed = item
        changed.state = "conflict"
        changed.message =
          "This playlist changed on the server. Discard this edit or review the current playlist before reapplying it."
        try await store.updateMutation(changed)
        blocked.insert(scope)
      } catch is MutationRejection {
        var rejected = item
        rejected.state = "rejected"
        rejected.message = "The server couldn't apply this edit. Review its song or playlist."
        try await store.updateMutation(rejected)
        blocked.insert(scope)
      } catch {
        break sending
      }
    }
    return .sent
  }
}
