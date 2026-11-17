import Foundation

public enum MusicYear: Codable, Sendable, Hashable {
  case text(String)
  case number(Int)
  public var text: String {
    switch self {
    case .text(let value): value
    case .number(let value): String(value)
    }
  }
  public init(from decoder: any Decoder) throws {
    let container = try decoder.singleValueContainer()
    if let number = try? container.decode(Int.self) {
      self = .number(number)
    } else {
      self = .text(try container.decode(String.self))
    }
  }
  public func encode(to encoder: any Encoder) throws {
    var container = encoder.singleValueContainer()
    switch self {
    case .text(let value): try container.encode(value)
    case .number(let value): try container.encode(value)
    }
  }
}
public struct AcquisitionResult: Codable, Sendable {
  public let created: Int
  public let existing: Int
  public let skipped: Int
  public let downloadsEnabled: Bool
  public init(created: Int, existing: Int, skipped: Int, downloadsEnabled: Bool) {
    self.created = created
    self.existing = existing
    self.skipped = skipped
    self.downloadsEnabled = downloadsEnabled
  }
}
public struct AcquisitionRequest: Codable, Sendable, Identifiable {
  public let id: String
  public let title: String
  public let artist: String
  public let album: String
  public let status: String
  public let receivedBytes: Int
  public let totalBytes: Int
  public let requestProgress: Double?
  public let error: String?
}
/// One song in a request group, with where it came from and its quality.
public struct RequestGroupItem: Codable, Sendable, Identifiable {
  public let id: String
  public let title: String
  public let status: String
  public let requestProgress: Double?
  public let error: String?
  public let source: String?
  public let quality: String?
}
/// One request on the Downloads page: an album request, or a lone song.
public struct RequestGroup: Codable, Sendable, Identifiable {
  public let id: String
  public let kind: String
  public let title: String
  public let artist: String
  public let releaseId: String?
  /// "active", "waiting", "paused", "done" or "failed".
  public let state: String
  public let songs: Int
  public let done: Int
  public let active: Int
  public let waiting: Int
  public let failed: Int
  /// Songs paused on the server (#228); nil from a server before pausing.
  public let paused: Int?
  public let progress: Double
  public let updatedAt: String
  public let items: [RequestGroupItem]
  /// "3 of 13 songs in your library · 2 waiting", or a lone song's state.
  public var summary: String {
    if songs == 1, let item = items.first { return RequestStatus.label(item.status) }
    var parts = ["\(done) of \(songs) songs in your library"]
    if active > 0 { parts.append("\(active) adding") }
    if waiting > 0 { parts.append("\(waiting) waiting") }
    if failed > 0 { parts.append("\(failed) couldn't add") }
    if let paused, paused > 0 { parts.append("\(paused) paused") }
    return parts.joined(separator: " · ")
  }
}
public struct RequestGroupPage: Codable, Sendable {
  public let groups: [RequestGroup]
  public let total: Int
  public let active: Int
  public let nextOffset: Int?
  public let downloadsEnabled: Bool
}
public struct AcquisitionPage: Codable, Sendable {
  public let items: [AcquisitionRequest]
  public let total: Int
  public let active: Int
  public let offset: Int
  public let nextOffset: Int?
  public let downloadsEnabled: Bool
}
