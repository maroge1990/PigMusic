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
    /// A queue limit kept songs (D-128): they stay, to be sent again when
    /// `RequestRetry` says.
    case limited
  }
  /// What the server said, song by song.
  public struct Report: Equatable, Sendable {
    /// Asked for, or already asked for: these songs are requested.
    public var requested: [String] = []
    /// Left out by the server (excluded from the household's downloads).
    public var skipped: [String] = []
    /// Paused by the person before: Add left them paused (#264).
    public var paused: [String] = []
    /// The server requests each paused song was left paused under (#276).
    public var pausedRequests: [String: [String]] = [:]
    /// Refused, with the server's reason.
    public var refused: [String] = []
    public var reason: String?
    /// Songs still in the outbox after a pass a queue limit kept songs in
    /// (D-128): to be sent again later.
    public var waiting = 0
    /// The limit that kept them, and for the rate when one more fits.
    public var limit: String?
    public var retryAfter: Int?
    public var downloadsEnabled = true
    public var stop: Stop = .done
    public init() {}
    /// Adds a later send's answers, so one drain of the outbox says what
    /// happened once (#245); the latest reason and stop win.
    public mutating func absorb(_ later: Report) {
      requested += later.requested
      skipped += later.skipped
      paused += later.paused
      pausedRequests.merge(later.pausedRequests) { $0 + $1 }
      refused += later.refused
      reason = later.reason ?? reason
      waiting = later.waiting
      limit = later.limit
      retryAfter = later.retryAfter
      downloadsEnabled = downloadsEnabled && later.downloadsEnabled
      stop = later.stop
    }
    /// What to tell the person: a refusal's reason, with how many songs
    /// when several were refused (#245), or that a song was left out (Codex
    /// round 2: nothing may vanish without a word).
    public func notice(titles: [String: String]) -> String? {
      RequestNotice.text(
        refused: refused.count, reason: reason,
        excluded: skipped.first.map { titles[$0] ?? "that song" }, paused: !paused.isEmpty,
        waiting: waiting)
    }
  }

  static let key = "request-outbox"
  static let retryKey = "request-outbox-retry"

  /// When the outbox may be sent again after a queue limit kept songs
  /// (D-128), kept beside it so it outlives the app.
  public static func retry(_ store: LibraryStore) async -> RequestRetry {
    ((try? await store.cached(retryKey, as: RequestRetry.self)) ?? nil) ?? RequestRetry()
  }
  public static func saveRetry(_ retry: RequestRetry, to store: LibraryStore) async throws {
    try await store.updateCached(retryKey, as: RequestRetry.self) { $0 = retry }
  }

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
  /// waits for the next send. A song a queue limit left out stays (D-128):
  /// past the rate the pass ends there, as every later new song would be
  /// refused too; past a waiting limit it goes on, as a later song may join
  /// a job already queued. Either way the pass ends `limited`.
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
        let answer = result.outboxAnswer
        if answer == .kept {
          // A queue limit left it out (D-128): it stays, never dropped.
          report.limit = result.limit ?? report.limit ?? "person_waiting"
          report.retryAfter = result.retryAfter
          if RequestRetry.stopsPass(result.limit) { return await limited(report, store) }
          continue
        }
        try await remove(track.id, from: store)
        report.downloadsEnabled = report.downloadsEnabled && result.downloadsEnabled
        switch answer {
        case .requested:
          report.requested.append(track.id)
        case .paused:
          // The person paused it before: it stays paused (#264), not left out.
          report.paused.append(track.id)
          report.pausedRequests[track.id] = result.pausedIds
        case .skipped, .kept:
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
    return report.limit == nil ? report : await limited(report, store)
  }

  /// A pass a queue limit kept songs in: how many wait now.
  private static func limited(_ report: Report, _ store: LibraryStore) async -> Report {
    var report = report
    report.waiting = await pending(store).count
    report.stop = .limited
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
