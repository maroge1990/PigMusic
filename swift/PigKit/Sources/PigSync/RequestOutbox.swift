import Foundation
import PigDomain
import PigStore

/// Song requests made on this iPhone that the server has not confirmed yet
/// (#206). A request acts at once: the song shows Requested, and the request
/// waits here, so being offline or closing the app doesn't lose it, and it
/// is sent the next time the server can be reached. One entry per song.
@MainActor public enum RequestOutbox {
  public enum Outcome: Equatable, Sendable {
    /// Everything waiting was sent, or nothing was waiting.
    case sent(downloadsEnabled: Bool)
    /// The server couldn't be reached: every request stays for the next try.
    case offline
    /// The server answered no to these songs: they leave the outbox.
    case refused([String], String)
    /// The account changed: nothing is sent or acknowledged for it.
    case stale
    /// The server ended this device's session; the requests stay.
    case signedOut
  }

  static let key = "request-outbox"

  /// The requests waiting, oldest first.
  public static func pending(_ store: LibraryStore) async -> [Track] {
    ((try? await store.cached(key, as: [Track].self)) ?? nil) ?? []
  }

  /// Keeps `track`'s request until the server confirms it.
  public static func add(_ track: Track, to store: LibraryStore) async throws {
    try await store.updateCached(key, as: [Track].self) { tracks in
      var list = tracks ?? []
      if !list.contains(where: { $0.id == track.id }) { list.append(track) }
      tracks = list
    }
  }

  /// Sends the waiting requests (at most 500, the server's limit per
  /// request) through `request`. Only the songs sent leave the outbox, so
  /// one requested meanwhile waits for the next send.
  public static func send(
    store: LibraryStore, isCurrent: () -> Bool,
    request: ([Track]) async throws -> AcquisitionResult
  ) async throws -> Outcome {
    let tracks = Array(await pending(store).prefix(500))
    guard isCurrent() else { return .stale }
    guard !tracks.isEmpty else { return .sent(downloadsEnabled: true) }
    let ids = Set(tracks.map(\.id))
    do {
      let result = try await request(tracks)
      guard isCurrent() else { return .stale }
      try await remove(ids, from: store)
      return .sent(downloadsEnabled: result.downloadsEnabled)
    } catch NativeFailure.authentication {
      return .signedOut
    } catch NativeFailure.unavailable(let message) {
      // The server answered and said no: trying again would say no again.
      guard isCurrent() else { return .stale }
      try await remove(ids, from: store)
      return .refused(tracks.map(\.id), message)
    } catch {
      return isCurrent() ? .offline : .stale
    }
  }

  private static func remove(_ ids: Set<String>, from store: LibraryStore) async throws {
    try await store.updateCached(key, as: [Track].self) { tracks in
      tracks = (tracks ?? []).filter { !ids.contains($0.id) }
    }
  }
}
