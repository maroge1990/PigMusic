import Foundation
import PigDomain

/// When the app leaves the foreground, the copies still queued are handed to
/// the system's background transfers in one pass instead of waiting for the
/// foreground worker (#219, D-113). The server is asked for all of them at
/// once, a request per fifty songs, and starts preparing any it isn't
/// already; a copy it has ready goes to the background downloader at once,
/// and one it is still preparing waits for a later pass (a finished
/// background download, a background refresh or the foreground). No pass
/// polls.
public enum BackgroundHandOff {
  public struct Outcome: Equatable, Sendable {
    /// Songs handed to the background transfers.
    public var handed: [String] = []
    /// Songs the server is still preparing, or not reached in this pass.
    public var waiting: [String] = []
    /// Songs the server answered without a copy (no longer in the person's
    /// library, or its file unavailable): they stay queued for the
    /// foreground worker, which reports why, and no refresh waits for them.
    public var unavailable: [String] = []
    /// The pass stopped early: a request failed, or it was cancelled.
    public var stopped = false
    public init() {}
  }

  /// Songs asked for in one pass, at most: the background session's own limit.
  public static let askLimit = 128

  /// Hands queued songs over, at most `limit` of them; songs the person paused
  /// stay. A failed request ends the asking (a lost session, no network): the
  /// songs it didn't answer stay queued for the foreground worker, which
  /// reports failures. Nothing is enqueued once `isCancelled`.
  @MainActor public static func run(
    _ records: [DownloadRecord], source: any DeviceCopySource, limit: Int,
    isCancelled: () -> Bool,
    enqueue: (DownloadRecord, DeviceCopy, URLRequest) async throws -> Void
  ) async -> Outcome {
    var outcome = Outcome()
    let queued = records.filter { $0.state == "queued" && $0.paused != true }
    guard !queued.isEmpty else { return outcome }
    let asked = queued.prefix(askLimit).map { (id: $0.id, quality: $0.quality) }
    let answers = await DeviceCopyBatch.ask(asked, source: source, isCancelled: isCancelled)
    outcome.stopped = answers.failed
    for (index, record) in queued.enumerated() {
      guard answers.answered.contains(record.id) else {
        outcome.waiting.append(record.id)
        continue
      }
      guard let copy = answers.copies[record.id] else {
        outcome.unavailable.append(record.id)
        continue
      }
      if ["queued", "preparing"].contains(copy.state) || outcome.handed.count >= limit {
        outcome.waiting.append(record.id)
        continue
      }
      do {
        guard !isCancelled(), !Task.isCancelled else { throw CancellationError() }
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
