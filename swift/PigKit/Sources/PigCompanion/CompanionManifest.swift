import Foundation
import PigDomain

/// The phone publishes a monotonically increasing revision across account changes.
/// No server credentials or server URLs cross this boundary.
public struct CompanionContext: Codable, Sendable, Equatable {
  public var version = 1
  public var revision: Int64
  public var account: String?
  public init(revision: Int64, account: String?) {
    self.revision = revision
    self.account = account
  }
  public enum Arrival: Sendable, Equatable {
    /// A newer context: apply it.
    case apply
    /// The context in use, delivered again: nothing changes, so it is only
    /// confirmed. Re-applying it made each confirmation draw another copy
    /// from the phone (25 Oct diagnostics: hundreds a minute).
    case repeated
    /// Older, or a different context under the same revision: ignored.
    case stale
  }
  /// What this context, just received, means against `current`.
  public func arrival(over current: CompanionContext) -> Arrival {
    if self == current { return .repeated }
    return version == 1 && revision > current.revision ? .apply : .stale
  }
}
public struct CompanionSong: Codable, Sendable, Identifiable, Hashable {
  public var track: Track
  public var checksum: String
  public var bytes: Int64
  public var id: String { track.id }
  public init(track: Track, checksum: String, bytes: Int64) {
    self.track = track
    self.checksum = checksum
    self.bytes = bytes
  }
}
public struct CompanionCollection: Codable, Sendable, Identifiable, Equatable {
  public var id: String
  public var name: String
  public var songIds: [String]
  public var favourites: Bool
  public init(id: String, name: String, songIds: [String], favourites: Bool = false) {
    self.id = id
    self.name = name
    self.songIds = songIds
    self.favourites = favourites
  }
}
public struct CompanionManifest: Codable, Sendable, Equatable {
  public var context: CompanionContext
  public var songs: [CompanionSong]
  public var collections: [CompanionCollection]
  public init(
    context: CompanionContext, songs: [CompanionSong], collections: [CompanionCollection] = []
  ) {
    self.context = context
    self.songs = songs
    self.collections = collections
  }
}
public struct CompanionFile: Codable, Sendable {
  public var context: CompanionContext
  public var songId: String
  public var checksum: String
  public init(context: CompanionContext, songId: String, checksum: String) {
    self.context = context
    self.songId = songId
    self.checksum = checksum
  }
}
public struct CompanionReceipt: Codable, Sendable {
  public var context: CompanionContext
  public var ready: [String: String]
  public var usedBytes: Int64
  public var budgetBytes: Int64
  public var manifestApplied: Bool
  public init(
    context: CompanionContext, ready: [String: String], usedBytes: Int64, budgetBytes: Int64,
    manifestApplied: Bool = true
  ) {
    self.context = context
    self.ready = ready
    self.usedBytes = usedBytes
    self.budgetBytes = budgetBytes
    self.manifestApplied = manifestApplied
  }
}
public enum CompanionFailure: Error, LocalizedError {
  case stale, invalid, integrity, quota
  public var errorDescription: String? {
    switch self {
    case .stale: "The phone changed this Watch library. Refresh transfers on your phone."
    case .invalid: "The Watch transfer is not compatible with this app."
    case .integrity: "The transferred audio failed verification. Retry from your phone."
    case .quota: "Watch storage is full. Remove Watch downloads on your phone."
    }
  }
}

/// Compact acknowledgement fits WatchConnectivity's context limit even for 1,000 songs.
public struct CompanionAcknowledgement: Codable, Sendable {
  public var context: CompanionContext
  public var positions: [Int]
  public var usedBytes: Int64
  public var budgetBytes: Int64
  public var manifestApplied: Bool
  public init(receipt: CompanionReceipt, manifest: CompanionManifest) {
    context = receipt.context
    positions = manifest.songs.indices.filter {
      receipt.ready[manifest.songs[$0].id] == manifest.songs[$0].checksum
    }
    usedBytes = receipt.usedBytes
    budgetBytes = receipt.budgetBytes
    manifestApplied = receipt.manifestApplied
  }
  public func receipt(for manifest: CompanionManifest) throws -> CompanionReceipt {
    guard context == manifest.context, positions.count <= manifest.songs.count,
      Set(positions).count == positions.count,
      positions.allSatisfy({ manifest.songs.indices.contains($0) }),
      usedBytes >= 0, budgetBytes >= 128 * 1024 * 1024, budgetBytes <= 8 * 1024 * 1024 * 1024
    else { throw CompanionFailure.invalid }
    let ready = Dictionary(
      uniqueKeysWithValues: positions.map { (manifest.songs[$0].id, manifest.songs[$0].checksum) })
    return CompanionReceipt(
      context: context, ready: ready, usedBytes: usedBytes, budgetBytes: budgetBytes,
      manifestApplied: manifestApplied)
  }
}
