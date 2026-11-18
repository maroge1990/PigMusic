import Foundation
import PigDomain
import PigStore

/// Song requests made on this iPhone that the server has not confirmed yet
/// (#206). A request acts at once: the song shows Requested, and the request
/// waits here, so being offline or closing the app doesn't lose it, and it
/// is sent the next time the server can be reached. One entry per song.
@MainActor public enum RequestOutbox {
  /// Why sending stopped.
  public enum Stop: Equatable, Sendable {
    /// Every request waiting was answered.
    case done
    /// The server couldn't be reached or was busy: the rest stay for later.
    case offline
    /// The account changed: nothing more is sent or acknowledged for it.
    case stale
    /// The server ended this device's session; the rest stay.
    case signedOut
  }
  /// What the server said, song by song.
  public struct Report: Equatable, Sendable {
    /// Asked for, or already asked for: these songs are requested.
    public var requested: [String] = []
    /// Left out by the server (excluded from the household's downloads).
    public var skipped: [String] = []
    /// Paused by the person before: Add left them paused (#264).
    public var paused: [String] = []
    /// Refused, with the server's reason.
    public var refused: [String] = []
    public var reason: String?
    public var downloadsEnabled = true
    public var stop: Stop = .done
    public init() {}
    /// Adds a later send's answers, so one drain of the outbox says what
    /// happened once (#245); the latest reason and stop win.
    public mutating func absorb(_ later: Report) {
      requested += later.requested
      skipped += later.skipped
      paused += later.paused
      refused += later.refused
      reason = later.reason ?? reason
      downloadsEnabled = downloadsEnabled && later.downloadsEnabled
      stop = later.stop
    }
    /// What to tell the person: a refusal's reason, with how many songs
    /// when several were refused (#245), or that a song was left out (Codex
    /// round 2: nothing may vanish without a word).
    public func notice(titles: [String: String]) -> String? {
      if let reason {
        return refused.count > 1 ? "Couldn't add \(refused.count) songs: \(reason)" : reason
      }
      if let id = skipped.first {
        return
          "Couldn't add \(titles[id] ?? "that song"): it's excluded from this server's downloads."
      }
      return paused.isEmpty ? nil : AcquisitionResult.pausedNotice
    }
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

  /// Sends the waiting requests through `request`, one song per request, so
  /// the server's counts say what happened to each song (Codex round 2: a
  /// batch answer can't tell which song was left out). A song leaves the
  /// outbox once the server has answered for it; a song requested meanwhile
  /// waits for the next send.
  public static func send(
    store: LibraryStore, isCurrent: () -> Bool,
    request: ([Track]) async throws -> AcquisitionResult
  ) async throws -> Report {
    var report = Report()
    for track in await pending(store) {
      guard isCurrent() else {
        report.stop = .stale
        return report
      }
      do {
        let result = try await request([track])
        guard isCurrent() else {
          report.stop = .stale
          return report
        }
        try await remove(track.id, from: store)
        report.downloadsEnabled = report.downloadsEnabled && result.downloadsEnabled
        if result.created + result.existing > 0 {
          report.requested.append(track.id)
        } else if result.paused > 0 {
          // The person paused it before: it stays paused (#264), not left out.
          report.paused.append(track.id)
        } else {
          report.skipped.append(track.id)
        }
      } catch NativeFailure.authentication {
        report.stop = .signedOut
        return report
      } catch let failure as NativeFailure where failure.refusesRequest {
        // The server answered and said no, or failed in a way asking again
        // won't mend (#239): trying again would fail again. (A busy or
        // restarting server throws ServerBusy and is kept; so is a busy
        // answer that can't confirm it was saved, as a request repeated is
        // answered `existing`, never twice.)
        guard isCurrent() else {
          report.stop = .stale
          return report
        }
        try await remove(track.id, from: store)
        report.refused.append(track.id)
        report.reason = failure.errorDescription
      } catch {
        report.stop = isCurrent() ? .offline : .stale
        return report
      }
    }
    return report
  }

  private static func remove(_ id: String, from store: LibraryStore) async throws {
    try await store.updateCached(key, as: [Track].self) { tracks in
      tracks = (tracks ?? []).filter { $0.id != id }
    }
  }
}

extension NativeFailure {
  /// A request the outbox drops, with this as the reason: a refusal, or a
  /// server error (#239).
  fileprivate var refusesRequest: Bool {
    switch self {
    case .unavailable, .serverError: true
    default: false
    }
  }
}
