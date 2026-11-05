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

  public init(
    countLimit: Int = 128, totalCostLimit: Int = 24 * 1024 * 1024,
    make: @escaping (CGImage) -> Image
  ) {
    self.make = make
    memory.countLimit = countLimit
    memory.totalCostLimit = totalCostLimit
  }

  /// Catalogue artwork has no content hash on the client: it is keyed by its
  /// alias (kind and id), hashed like a stored cover.
  public static func catalogueRequest(kind: String, id: String, width: Int) -> ArtworkRequest? {
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
  public func load(
    _ request: ArtworkRequest, stopsWhenCancelled: Bool, isCurrent: () -> Bool,
    fetch: @escaping @Sendable (ArtworkRequest) async throws -> Data
  ) async throws -> Image? {
    guard let cache else { return nil }
    let key = request.filename as NSString
    if let image = memory.object(forKey: key) { return image }
    let data = try await cache.data(for: request, fetch: fetch)
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
}
