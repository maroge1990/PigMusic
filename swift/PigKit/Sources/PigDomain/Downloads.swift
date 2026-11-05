import Foundation

public struct DeviceCopy: Codable, Sendable {
  public let state: String
  public let quality: String
  public let sha256: String?
  public let sourceSha256: String
  public let bytes: Int64
  public let fileExtension: String
  public let key: String
  enum CodingKeys: String, CodingKey {
    case state, quality, sha256, sourceSha256, bytes, key
    case fileExtension = "extension"
  }
}
public protocol DeviceCopySource: Sendable {
  func deviceCopy(trackId: String, quality: String, create: Bool) async throws -> DeviceCopy
  func copyRequest(trackId: String, quality: String) async throws -> URLRequest
}
public struct RetainedDownload: Codable, Sendable, Equatable {
  public var quality: String
  public var bytes: Int64
  public var fileName: String
  public var checksum: String?
  public var sourceRevision: String?
  public init(
    quality: String, bytes: Int64, fileName: String, checksum: String?, sourceRevision: String?
  ) {
    self.quality = quality
    self.bytes = bytes
    self.fileName = fileName
    self.checksum = checksum
    self.sourceRevision = sourceRevision
  }
}
/// Why a song is on the device: added directly (as a song or album) or as part
/// of a playlist. A song can have several; its file stays until none remain.
public struct DownloadOrigin: Codable, Sendable, Hashable {
  public var playlistId: String?
  public var playlistName: String?
  public static let direct = DownloadOrigin()
  public init(playlistId: String? = nil, playlistName: String? = nil) {
    self.playlistId = playlistId
    self.playlistName = playlistName
  }
  public func same(_ other: DownloadOrigin) -> Bool { playlistId == other.playlistId }
}
public struct DownloadRecord: Codable, Sendable, Identifiable, Equatable {
  public let track: Track
  public var quality: String
  public var state: String
  public var bytes: Int64
  public var total: Int64
  public var fileName: String?
  public var checksum: String?
  public var error: String?
  public var retained: RetainedDownload?
  /// Nil for downloads made before origins were kept; those count as direct.
  public var origins: [DownloadOrigin]?
  /// A finished transfer the person cleared from the activity list; the file stays.
  public var cleared: Bool?
  /// The person paused this song on its own (#127). It waits until they resume
  /// it; the Resume for all downloads and internal restarts leave it paused.
  public var paused: Bool?
  public var isPaused: Bool { paused == true && state != "ready" }
  public var id: String { track.id }
  public var effectiveOrigins: [DownloadOrigin] { origins ?? [.direct] }
  public init(track: Track, quality: String) {
    self.track = track
    self.quality = quality
    state = "queued"
    bytes = 0
    total = 0
  }
}
public enum OfflineFailure: Error, LocalizedError, Sendable {
  case authentication, integrity, quota, unsafeResponse, cancelled
  public var errorDescription: String? {
    switch self {
    case .authentication: "The device session expired. Sign in again."
    case .integrity: "The download did not match the server copy. Retry after refreshing."
    case .quota: "The device download budget is full. Remove downloads or increase the budget."
    case .unsafeResponse: "The server did not return a safe audio download."
    case .cancelled: "Download cancelled."
    }
  }
}

/// Plain-language request states, the same words as the web; the server keeps
/// its technical names.
/// Display names for download sources, as on the web.
public enum RequestSource {
  public static func name(_ id: String) -> String {
    [
      "soulseek": "Soulseek", "freemp3cloud": "FreeMp3Cloud", "zvu4no": "zvu4no",
      "jiosaavn": "JioSaavn", "youtube": "YouTube", "soundcloud": "SoundCloud",
      "monochrome": "Monochrome",
    ][id] ?? id
  }
}

public enum RequestStatus {
  public static func label(_ status: String) -> String {
    switch status {
    case "waiting_for_sources": "Waiting for a source"
    case "searching": "Looking for a copy"
    case "downloading": "Downloading to the server"
    case "validating": "Checking the file"
    case "importing": "Adding to the library"
    case "completed", "downloaded": "In your library"
    case "cancelled": "Cancelled"
    case "failed": "Couldn't get this song"
    case "running", "queued": "Working on it"
    case "done": "Done"
    default: status.replacingOccurrences(of: "_", with: " ").capitalized
    }
  }
}
