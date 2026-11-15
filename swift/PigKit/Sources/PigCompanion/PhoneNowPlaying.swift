import Foundation

/// What the iPhone is playing, for the Watch's home (D-104, #220). Sent as
/// user info on a song or play/pause change; a newer one replaces any still
/// queued, and the Watch keeps only the latest. With no title the phone has
/// stopped: its queue emptied or its account signed out.
public struct PhoneNowPlaying: Codable, Equatable, Sendable {
  public var kind = "phone-now-playing"
  public var version = 1
  public var title: String?
  public var artist: String?
  public var playing: Bool
  /// A small JPEG of the cover, when the phone had one at hand.
  public var cover: Data?
  public var sentAt: Date

  /// Title and artist, in UTF-8 bytes (a character can be many).
  public static let maximumTextBytes = 400
  public static let maximumCoverBytes = 15 * 1024
  /// The cover's longest side in pixels, as the phone draws it.
  public static let coverPixels = 96
  /// The whole payload, well under WatchConnectivity's user info limit.
  public static let maximumBytes = 32 * 1024
  /// How long a state stands without a newer one: the phone may have ended
  /// without saying so. A playing phone sends again on every song.
  public static let playingLifetime: TimeInterval = 4 * 3600
  public static let pausedLifetime: TimeInterval = 30 * 60

  /// Text is cut to the limit and an oversized cover is left out, so the
  /// phone never sends what the Watch would refuse.
  public init(title: String, artist: String, playing: Bool, cover: Data?, sentAt: Date = Date()) {
    self.title = Self.clipped(title)
    self.artist = Self.clipped(artist)
    self.playing = playing
    self.cover = cover.flatMap { Self.validCover($0) ? $0 : nil }
    self.sentAt = sentAt
  }
  private init(stoppedAt sentAt: Date) {
    title = nil
    artist = nil
    playing = false
    cover = nil
    self.sentAt = sentAt
  }
  public static func stopped(at date: Date = Date()) -> PhoneNowPlaying {
    PhoneNowPlaying(stoppedAt: date)
  }
  public var isStopped: Bool { title == nil }

  /// Decodes only a valid now-playing payload, never another message kind.
  public static func decode(_ data: Data) -> PhoneNowPlaying? {
    guard data.count <= maximumBytes,
      let value = try? JSONDecoder().decode(Self.self, from: data),
      value.kind == "phone-now-playing", value.version == 1, value.sentAt.timeIntervalSince1970 > 0
    else { return nil }
    if let title = value.title {
      guard let artist = value.artist, title.utf8.count <= maximumTextBytes,
        artist.utf8.count <= maximumTextBytes
      else { return nil }
      if let cover = value.cover, !validCover(cover) { return nil }
    } else {
      guard value.artist == nil, value.cover == nil, !value.playing else { return nil }
    }
    return value
  }
  /// Whole characters, up to the byte limit.
  private static func clipped(_ text: String) -> String {
    var result = ""
    var bytes = 0
    for character in text {
      bytes += character.utf8.count
      guard bytes <= maximumTextBytes else { break }
      result.append(character)
    }
    return result
  }
  /// A JPEG within the size limit.
  private static func validCover(_ data: Data) -> Bool {
    data.count >= 4 && data.count <= maximumCoverBytes && data.prefix(2) == Data([0xFF, 0xD8])
  }

  /// Whether the Watch's home shows the iPhone's song: the phone has one,
  /// recently, and the Watch is not playing its own.
  public static func showsRow(_ state: PhoneNowPlaying?, watchPlaying: Bool, now: Date) -> Bool {
    guard let state, !state.isStopped, !watchPlaying else { return false }
    let age = now.timeIntervalSince(state.sentAt)
    return age <= (state.playing ? playingLifetime : pausedLifetime)
  }
  /// Whether `self` may replace `current`: delivery order is not trusted.
  public func supersedes(_ current: PhoneNowPlaying?) -> Bool {
    guard let current else { return true }
    return sentAt >= current.sentAt
  }
}
