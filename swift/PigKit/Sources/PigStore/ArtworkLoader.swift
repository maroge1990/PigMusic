import CoreGraphics
import CryptoKit
import Foundation
import PigDomain

/// Decoded artwork for the screens of one account (#46): a bounded memory
/// of decoded images in front of the account's `ArtworkCache` on disk.
///
/// Generic over the image type so the app keeps `UIImage` and tests use any
/// class. An image that arrives after the account changed is not kept.
@MainActor public final class ArtworkLoader<Image: AnyObject> {
  private let memory = NSCache<NSString, Image>()
  private let make: (CGImage) -> Image
  /// The account's encoded artwork on disk; nil when signed out.
  public var cache: ArtworkCache?
  /// The wait between two asks for a cover the server is still fetching.
  /// It throws when the task is cancelled, which ends the asking; tests
  /// replace it to state the schedule without waiting for it.
  public var sleep: @Sendable (Duration) async throws -> Void = { try await Task.sleep(for: $0) }

  public init(
    countLimit: Int = 128, totalCostLimit: Int = 24 * 1024 * 1024,
    make: @escaping (CGImage) -> Image
  ) {
    self.make = make
    memory.countLimit = countLimit
    memory.totalCostLimit = totalCostLimit
  }

  /// Catalogue artwork has no content hash on the client: it is keyed by its
  /// alias (kind and id), hashed like a stored cover. Nil for an id that is
  /// not of the kind's shape (`ArtworkAlias`), so nothing is asked for it.
  public static func catalogueRequest(kind: String, id: String, width: Int) -> ArtworkRequest? {
    guard ArtworkAlias.accepts(kind: kind, id: id) else { return nil }
    let sha = SHA256.hash(data: Data(("catalogue-\(kind):" + id.lowercased()).utf8)).map {
      String(format: "%02x", $0)
    }.joined()
    return try? ArtworkRequest(sha: sha, width: width)
  }

  /// The image already decoded in memory, for drawing a revisited page at once.
  public func memoryImage(_ request: ArtworkRequest) -> Image? {
    memory.object(forKey: request.filename as NSString)
  }
  public func clearMemory() { memory.removeAllObjects() }

  /// The account ended: pending downloads stop and nothing it loaded stays.
  public func detach() {
    if let cache { Task { await cache.cancelPending() } }
    cache = nil
    memory.removeAllObjects()
  }

  /// The image for `request`: from memory, else the disk cache, else `fetch`.
  /// Returns nil without a disk cache, or when `isCurrent` says the account
  /// changed while it loaded. With `stopsWhenCancelled` a cancelled task
  /// throws `CancellationError` after the read and after decoding. Errors
  /// from `fetch` are thrown, so the caller can end a revoked session.
  ///
  /// `retryAfter`: a cover the server has not fetched yet answers "not
  /// available" while it fetches it (a catalogue alias on first ask); the
  /// load asks again after each of these waits (`CoverRetry.waits` on a
  /// screen) before giving up, so the screen and the lock screen are not
  /// left with a placeholder for a cover that arrives later. Other failures
  /// are not retried, and a cancelled task (the tile left the screen) throws
  /// `CancellationError` from its wait and asks no more.
  public func load(
    _ request: ArtworkRequest, stopsWhenCancelled: Bool, retryAfter: [Duration] = [],
    isCurrent: () -> Bool,
    fetch: @escaping @Sendable (ArtworkRequest) async throws -> Data
  ) async throws -> Image? {
    guard let cache else { return nil }
    let key = request.filename as NSString
    if let image = memory.object(forKey: key) { return image }
    let found = try await asking(retryAfter, isCurrent: isCurrent) {
      try await cache.data(for: request, fetch: fetch)
    }
    guard let data = found else { return nil }
    if stopsWhenCancelled { try Task.checkCancellation() }
    let width = request.width
    let decoded = try await Task.detached(priority: .utility) {
      try ArtworkCache.thumbnail(data, width: width)
    }.value
    if stopsWhenCancelled { try Task.checkCancellation() }
    guard isCurrent() else { return nil }
    let image = make(decoded)
    memory.setObject(image, forKey: key, cost: decoded.bytesPerRow * decoded.height)
    return image
  }

  /// `attempt`, made again after each wait while its answer is "not
  /// available"; the last such answer is thrown. Nil when the account
  /// changed during a wait. A cancelled task stops at its wait.
  private func asking<T: Sendable>(
    _ waits: [Duration], isCurrent: () -> Bool, _ attempt: @MainActor () async throws -> T
  ) async throws -> T? {
    var waits = waits[...]
    while true {
      do {
        return try await attempt()
      } catch ArtworkFailure.unavailable {
        guard let wait = waits.popFirst() else { throw ArtworkFailure.unavailable }
        try await sleep(wait)
        try Task.checkCancellation()
        guard isCurrent() else { return nil }
      }
    }
  }
}
