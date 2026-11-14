import Foundation

public struct AccountPreferences: Codable, Sendable, Equatable {
  public var appearance: String
  public var defaultTab: String
  /// Similar songs when a queue ends (#202): on unless turned off.
  public var keepPlaying: Bool
  public init(appearance: String = "system", defaultTab: String = "artists", keepPlaying: Bool = true)
  {
    self.appearance = appearance
    self.defaultTab = defaultTab
    self.keepPlaying = keepPlaying
  }
  private enum CodingKeys: String, CodingKey { case appearance, defaultTab, keepPlaying }
  /// Tolerant: a copy saved by an older build, or an older server's answer,
  /// has no `keepPlaying` and means on.
  public init(from decoder: Decoder) throws {
    let values = try decoder.container(keyedBy: CodingKeys.self)
    appearance = try values.decodeIfPresent(String.self, forKey: .appearance) ?? "system"
    defaultTab = try values.decodeIfPresent(String.self, forKey: .defaultTab) ?? "artists"
    keepPlaying = try values.decodeIfPresent(Bool.self, forKey: .keepPlaying) ?? true
  }
}
/// Numbers successive local changes so that only the latest one's outcome is
/// kept: a slower save or a server read that began before a newer change
/// must not put back an older value.
public struct LatestChange: Sendable {
  public private(set) var current = 0
  public init() {}
  /// Starts a change and returns its number.
  public mutating func begin() -> Int {
    current += 1
    return current
  }
  public func isLatest(_ change: Int) -> Bool { change == current }
}
public struct AccountSession: Codable, Sendable, Identifiable {
  public let id: String
  public let kind: String
  public let deviceName: String
  public let platform: String
  public let lastSeenAt: Double
  public let expiresAt: Double
  public let revokedAt: Double?
  /// The session this app is using ("This device"); nil from older servers.
  public let current: Bool?
  /// "iPhone or iPad app", "Apple Watch app" or "Web".
  public var platformName: String {
    ["ios": "iPhone or iPad app", "watchos": "Apple Watch app", "web": "Web"][platform]
      ?? platform
  }
}
public struct AccountSessions: Codable, Sendable { public let sessions: [AccountSession] }
public struct QualityOption: Codable, Sendable, Identifiable {
  public let value: String
  public let label: String
  public var id: String { value }
}
public struct QualityItem: Codable, Sendable {
  public let scope: String
  public let itemId: String
  public let preferred: String
}
public struct QualitySettings: Codable, Sendable {
  public let preferences: [QualityItem]
  public let options: [QualityOption]
  public let householdDefault: String
}
public struct ImportResult: Codable, Sendable {
  public let playlists: Int
  public let tracks: Int
}
public struct RemovalItem: Codable, Sendable, Identifiable {
  public let trackId: String
  public let deleteFile: Bool
  public let keptFor: [String]
  public var id: String { trackId }
}
public struct RemovalPreview: Codable, Sendable {
  public let confirmation: String
  public let items: [RemovalItem]
  public let deleteCount: Int
  public let keepCount: Int
}
public struct RemovalResult: Codable, Sendable {
  public let id: String
  public let removed: Int
  public let deleted: Int
  public let kept: Int
  public let undoUntil: Double
}

public struct ImportPreview: Codable, Sendable {
  public let playlists: Int
  public let tracks: Int
  public let saved: Int
  public let household: Int
  public let missing: Int
}

public struct ArtistAcquisition: Codable, Sendable {
  public let status: String
  public let monitor: Bool
  public let kinds: [String]
  public let total: Int
  public let done: [String]
  public let created: Int
  public let error: String?
}

public struct CancelledRequest: Codable, Sendable {
  public var cancelled: Bool
  public var continuesForOthers: Bool
}
