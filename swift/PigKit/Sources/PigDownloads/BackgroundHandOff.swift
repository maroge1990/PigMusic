import Foundation
import PigDomain

/// When the app leaves the foreground, the copies still queued are handed to
/// the system's background transfers in one pass instead of waiting for the
/// foreground worker (#219). A copy the server has ready goes at once; one it
/// is still preparing has been asked for, and waits for a later pass (a
/// background refresh or the foreground).
public enum BackgroundHandOff {
  public struct Outcome: Equatable, Sendable {
    /// Songs handed to the background transfers.
    public var handed: [String] = []
    /// Songs the server is still preparing, or not reached in this pass.
    public var waiting: [String] = []
    /// The pass stopped early: a request failed, or it was cancelled.
    public var stopped = false
    public init() {}
  }

  /// Hands queued songs over, at most `limit` of them; songs the person paused
  /// stay. Stops at the first failure (a lost session, no network), leaving
  /// the rest queued for the foreground worker, which reports failures.
  @MainActor public static func run(
    _ records: [DownloadRecord], source: any DeviceCopySource, limit: Int,
    isCancelled: () -> Bool,
    enqueue: (DownloadRecord, DeviceCopy, URLRequest) async throws -> Void
  ) async -> Outcome {
    var outcome = Outcome()
    let queued = records.filter { $0.state == "queued" && $0.paused != true }
    for (index, record) in queued.enumerated() {
      guard index < limit, !isCancelled(), !Task.isCancelled else {
        outcome.stopped = outcome.stopped || index < limit
        outcome.waiting += queued[index...].map(\.id)
        break
      }
      do {
        let copy = try await source.deviceCopy(
          trackId: record.id, quality: record.quality, create: true)
        if ["queued", "preparing"].contains(copy.state) {
          outcome.waiting.append(record.id)
          continue
        }
        let request = try await source.copyRequest(trackId: record.id, quality: record.quality)
        // A Pause, the foreground or another account may have come while the
        // requests were out (Codex round 1): nothing is enqueued after that.
        guard !isCancelled(), !Task.isCancelled else { throw CancellationError() }
        try await enqueue(record, copy, request)
        outcome.handed.append(record.id)
      } catch {
        outcome.stopped = true
        outcome.waiting += queued[index...].map(\.id)
        break
      }
    }
    return outcome
  }
}
