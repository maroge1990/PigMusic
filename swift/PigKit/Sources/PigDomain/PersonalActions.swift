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
  /// Songs the person paused before: Add leaves them paused (#264).
  public let paused: Int
  /// The server requests those songs were left paused under (#276).
  public let pausedIds: [String]
  public let downloadsEnabled: Bool
  public init(
    created: Int, existing: Int, skipped: Int, paused: Int = 0, pausedIds: [String] = [],
    downloadsEnabled: Bool
  ) {
    self.created = created
    self.existing = existing
    self.skipped = skipped
    self.paused = paused
    self.pausedIds = pausedIds
    self.downloadsEnabled = downloadsEnabled
  }
  private enum CodingKeys: String, CodingKey {
    case created, existing, skipped, paused, pausedIds, downloadsEnabled
  }
  public init(from decoder: any Decoder) throws {
    let values = try decoder.container(keyedBy: CodingKeys.self)
    created = try values.decode(Int.self, forKey: .created)
    existing = try values.decode(Int.self, forKey: .existing)
    skipped = try values.decode(Int.self, forKey: .skipped)
    // An older server doesn't say.
    paused = try values.decodeIfPresent(Int.self, forKey: .paused) ?? 0
    pausedIds = try values.decodeIfPresent([String].self, forKey: .pausedIds) ?? []
    downloadsEnabled = try values.decode(Bool.self, forKey: .downloadsEnabled)
  }
  /// Nothing new was asked for and a paused song stays paused (#264): the
  /// person is told where to resume it, never that it was left out.
  public var onlyPaused: Bool { created == 0 && paused > 0 }
  /// Whether the server is getting every one of the `count` songs sent
  /// together (#259): none left out, none left paused. Otherwise the answer
  /// gives only totals and can't say which song was taken.
  public func tookAll(_ count: Int) -> Bool {
    skipped == 0 && paused == 0 && created + existing == count
  }
  /// One song sent alone: the server is getting it, new or already asked
  /// for, not left out or left paused.
  public var tookIt: Bool { created + existing > 0 }
  public static let pausedNotice = "Paused. Resume it in Downloads."
}
extension AcquisitionResult {
  /// What a request to add songs did, in the agreed words (D-115, #246):
  /// songs the server is getting now or already was (another person's job,
  /// a household copy) are being added; skipped ones (excluded, or already
  /// in your library) are counted as skipped.
  public var sentence: String {
    func songs(_ count: Int) -> String { "\(count) song\(count == 1 ? "" : "s")" }
    if onlyPaused && existing == 0 { return Self.pausedNotice }
    let adding = created + existing
    let parts = [
      adding > 0 ? "Adding \(songs(adding)) to your library." : nil,
      skipped > 0 ? "Skipped \(songs(skipped))." : nil,
      // The one sentence for it, as on the web (DESIGN.md's table).
      paused > 0 ? Self.pausedNotice : nil,
    ].compactMap { $0 }
    return parts.isEmpty ? "Already in your library." : parts.joined(separator: " ")
  }
}
extension AcquisitionResult {
  /// Several answers (a playlist's batches, a household add and a request)
  /// as one, paused songs included (Codex round 2 on batch D).
  public static func combined(_ results: [AcquisitionResult]) -> AcquisitionResult {
    AcquisitionResult(
      created: results.reduce(0) { $0 + $1.created },
      existing: results.reduce(0) { $0 + $1.existing },
      skipped: results.reduce(0) { $0 + $1.skipped },
      paused: results.reduce(0) { $0 + $1.paused },
      downloadsEnabled: results.allSatisfy(\.downloadsEnabled))
  }
  /// A playlist's Add of its missing songs in one line: household copies
  /// added, then the server's answer for the rest (paused songs included).
  public static func playlistNotice(added: Int, requested: [AcquisitionResult]) -> String {
    let line = [
      added > 0 ? "Added \(added) song\(added == 1 ? "" : "s") to your library." : nil,
      requested.isEmpty ? nil : combined(requested).sentence,
    ].compactMap { $0 }.joined(separator: " ")
    return line.isEmpty ? "These songs are already being added." : line
  }
  public static let downloadsOffNotice = "Adding. Downloads are off on the server, so it will wait."
  /// What a menu's Add says: nil when a success haptic is enough; else that
  /// downloads are off, or where to resume paused songs (alone or beside
  /// added ones).
  public var menuNotice: String? {
    let waiting = created + existing > 0
    guard paused > 0 else { return downloadsEnabled ? nil : Self.downloadsOffNotice }
    // Where to resume is always said; downloads being off too when other
    // songs wait (Codex confirmation on batch D).
    if !downloadsEnabled {
      return waiting ? Self.downloadsOffNotice + " " + Self.pausedNotice : Self.pausedNotice
    }
    return waiting ? sentence : Self.pausedNotice
  }
}

/// The one notice for a drain of the request outbox (#245): a refusal's
/// reason (with how many songs when several), else the song left out; then
/// the paused sentence when a song stayed paused (#264, Codex round 1 on
/// batch D: it is never hidden by the rest).
public enum RequestNotice {
  public static func text(refused: Int, reason: String?, excluded: String?, paused: Bool)
    -> String?
  {
    var parts: [String] = []
    if let reason {
      parts.append(refused > 1 ? "Couldn't add \(refused) songs: \(reason)" : reason)
    } else if let excluded {
      parts.append("Couldn't add \(excluded): it's excluded from this server's downloads.")
    }
    if paused { parts.append(AcquisitionResult.pausedNotice) }
    return parts.isEmpty ? nil : parts.joined(separator: " ")
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
  /// The song's own artist and album, to match the iPhone's marks.
  public let artist: String?
  public let album: String?
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
