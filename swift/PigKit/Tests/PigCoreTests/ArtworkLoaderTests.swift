import CoreGraphics
import Foundation
import ImageIO
import PigDomain
import PigStore
import Testing
import UniformTypeIdentifiers

private final class Picture {
  let image: CGImage
  init(_ image: CGImage) { self.image = image }
}

private actor Fetches {
  private(set) var count = 0
  func add() { count += 1 }
}

private func png(size: Int = 8) throws -> Data {
  let context = try #require(
    CGContext(
      data: nil, width: size, height: size, bitsPerComponent: 8,
      bytesPerRow: size * 4, space: CGColorSpaceCreateDeviceRGB(),
      bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue))
  context.setFillColor(CGColor(red: 0.2, green: 0.4, blue: 0.6, alpha: 1))
  context.fill(CGRect(x: 0, y: 0, width: size, height: size))
  let bytes = NSMutableData()
  let output = try #require(
    CGImageDestinationCreateWithData(bytes, UTType.png.identifier as CFString, 1, nil))
  CGImageDestinationAddImage(output, try #require(context.makeImage()), nil)
  #expect(CGImageDestinationFinalize(output))
  return bytes as Data
}

@MainActor struct ArtworkLoaderTests {
  private func makeLoader() throws -> (ArtworkLoader<Picture>, URL) {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    let loader = ArtworkLoader<Picture> { Picture($0) }
    loader.cache = try ArtworkCache(directory: root)
    return (loader, root)
  }

  @Test func aLoadedImageIsKeptInMemory() async throws {
    let (loader, root) = try makeLoader()
    defer { try? FileManager.default.removeItem(at: root) }
    let request = try ArtworkRequest(sha: String(repeating: "c", count: 64))
    let bytes = try png()
    let fetches = Fetches()
    #expect(loader.memoryImage(request) == nil)
    let first = try await loader.load(request, stopsWhenCancelled: true, isCurrent: { true }) {
      _ in
      await fetches.add()
      return bytes
    }
    let image = try #require(first)
    #expect(loader.memoryImage(request) === image)
    let again = try await loader.load(request, stopsWhenCancelled: true, isCurrent: { true }) {
      _ in
      await fetches.add()
      return bytes
    }
    #expect(again === image)
    #expect(await fetches.count == 1)
  }

  /// An image for the old account never lands in the new one's memory.
  @Test func anImageLoadedForAnOldAccountIsDropped() async throws {
    let (loader, root) = try makeLoader()
    defer { try? FileManager.default.removeItem(at: root) }
    let fence = AccountFence()
    let stamp = fence.generation
    let request = try ArtworkRequest(sha: String(repeating: "d", count: 64))
    let bytes = try png()
    let image = try await loader.load(
      request, stopsWhenCancelled: false, isCurrent: { fence.isCurrent(stamp) }
    ) { _ in
      await MainActor.run { fence.end() }  // Signed out while it downloaded.
      return bytes
    }
    #expect(image == nil)
    #expect(loader.memoryImage(request) == nil)
  }

  @Test func withoutADiskCacheNothingLoads() async throws {
    let loader = ArtworkLoader<Picture> { Picture($0) }
    let request = try ArtworkRequest(sha: String(repeating: "e", count: 64))
    let image = try await loader.load(request, stopsWhenCancelled: true, isCurrent: { true }) {
      _ in throw ArtworkFailure.unavailable
    }
    #expect(image == nil)
  }

  @Test func detachingForgetsTheAccountsImages() async throws {
    let (loader, root) = try makeLoader()
    defer { try? FileManager.default.removeItem(at: root) }
    let request = try ArtworkRequest(sha: String(repeating: "f", count: 64))
    let bytes = try png()
    _ = try await loader.load(request, stopsWhenCancelled: true, isCurrent: { true }) { _ in bytes }
    #expect(loader.memoryImage(request) != nil)
    loader.detach()
    #expect(loader.cache == nil)
    #expect(loader.memoryImage(request) == nil)
  }

  /// The server answers "not available" for a catalogue cover it is still
  /// fetching; the load asks again after each wait, and stops at the end.
  @Test func aCoverStillBeingFetchedIsAskedForAgain() async throws {
    let (loader, root) = try makeLoader()
    defer { try? FileManager.default.removeItem(at: root) }
    let request = try ArtworkRequest(sha: String(repeating: "a", count: 64))
    let bytes = try png()
    let fetches = Fetches()
    let image = try await loader.load(
      request, stopsWhenCancelled: false,
      retryAfter: [.milliseconds(5), .milliseconds(5), .milliseconds(5)], isCurrent: { true }
    ) { _ in
      await fetches.add()
      if await fetches.count < 3 { throw ArtworkFailure.unavailable }
      return bytes
    }
    #expect(image != nil)
    #expect(await fetches.count == 3)
    let never = Fetches()
    await #expect(throws: ArtworkFailure.unavailable) {
      _ = try await loader.load(
        try ArtworkRequest(sha: String(repeating: "b", count: 64)), stopsWhenCancelled: false,
        retryAfter: [.milliseconds(5)], isCurrent: { true }
      ) { _ in
        await never.add()
        throw ArtworkFailure.unavailable
      }
    }
    #expect(await never.count == 2)  // One retry, then the miss stands.
    // Without retries a miss is final at once: a stored hash never appears later.
    let once = Fetches()
    await #expect(throws: ArtworkFailure.unavailable) {
      _ = try await loader.load(
        try ArtworkRequest(sha: String(repeating: "c", count: 64)), stopsWhenCancelled: false,
        isCurrent: { true }
      ) { _ in
        await once.add()
        throw ArtworkFailure.unavailable
      }
    }
    #expect(await once.count == 1)
  }

  @Test func catalogueArtworkIsKeyedByItsAlias() throws {
    let id = "8F1B3C1E-5A6B-4C7D-8E9F-0A1B2C3D4E5F"
    let upper = try #require(
      ArtworkLoader<Picture>.catalogueRequest(kind: "release", id: id, width: 512))
    let lower = try #require(
      ArtworkLoader<Picture>.catalogueRequest(kind: "release", id: id.lowercased(), width: 512))
    #expect(upper == lower)
    #expect(upper.sha.count == 64 && upper.width == 512)
    let other = ArtworkLoader<Picture>.catalogueRequest(kind: "artist", id: id, width: 512)
    #expect(other?.sha != upper.sha)
    #expect(ArtworkLoader<Picture>.catalogueRequest(kind: "release", id: id, width: 300) == nil)
  }
}
