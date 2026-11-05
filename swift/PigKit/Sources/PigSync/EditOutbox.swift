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

  /// What an edit changes, for keeping dependent edits in order.
  public nonisolated static func scope(_ mutation: OfflineMutation) -> String {
    if let playlist = mutation.playlistId { return "playlist:" + playlist }
    if let track = mutation.track { return mutation.action + ":" + track.id }
    return mutation.action + ":" + (mutation.name ?? "")
  }

  /// Sends the queued edits in `store` through `apply`. `applied` becomes true
  /// once the server applied one, also when a store error is thrown later.
  /// `isCurrent` is asked before each edit and after each answer, so nothing
  /// is acknowledged for an account that has ended.
  public static func send(
    store: LibraryStore, isCurrent: () -> Bool, applied: inout Bool,
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
        rejected.message = "The server could not apply this edit. Review its song or playlist."
        try await store.updateMutation(rejected)
        blocked.insert(scope)
      } catch {
        break sending
      }
    }
    return .sent
  }
}
