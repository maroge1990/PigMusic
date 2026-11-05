import Foundation
import PigDomain
import PigStore

/// Sends the listens waiting on this phone to the server (#46).
///
/// Listens older than 30 days, dated more than five minutes ahead, or for a
/// song no longer in the library are dropped. The rest stay queued until the
/// server takes them, and are not acknowledged for an account that ended.
@MainActor public enum HistoryUpload {
  /// Sends one batch. Errors (an authentication failure among them) are
  /// thrown; the listens they left stay queued.
  public static func send(
    store: LibraryStore, now: Int? = nil, isCurrent: () -> Bool,
    record: ([ListeningEvent]) async throws -> Void
  ) async throws {
    let pending = try await store.pendingListens()
    var valid: [ListeningEvent] = []
    var discarded: [String] = []
    let now = now ?? Int(Date().timeIntervalSince1970)
    for event in pending {
      let exists: Bool
      if event.trackId.hasPrefix("mix:") {
        exists = true
      } else {
        exists = try await store.track(id: event.trackId) != nil
      }
      if event.playedAt < now - 30 * 86400 || event.playedAt > now + 300 || !exists {
        discarded.append(event.id)
      } else {
        valid.append(event)
      }
    }
    try await store.acknowledgeListens(discarded)
    guard isCurrent() else { return }
    try await record(valid)
    guard isCurrent() else { return }
    try await store.acknowledgeListens(valid.map(\.id))
  }
}
