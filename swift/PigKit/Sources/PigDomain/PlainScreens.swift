import Foundation

// The words and arrangement of the iPhone's plain screens (D-120, #251):
// Downloads, the queue, Your music quality and Changes not sent yet.

/// A change waiting in the outbox, in plain words (D-120): never the raw
/// action ("append") or state ("queued").
public struct PendingChangeWords: Sendable, Equatable {
  /// "Add to playlist Road Trip", "Favourite", "New playlist".
  public let title: String
  /// "Song · Artist", or the playlist's name.
  public let detail: String?
  /// "Waiting to send" or "Couldn't send. Try again."
  public let status: String
  /// The server refused it or it clashed with a newer version: Try again
  /// sends it once more.
  public let failed: Bool
  /// The SF Symbol drawn beside it.
  public let symbol: String

  public static let waiting = "Waiting to send"
  public static let couldNotSend = "Couldn't send. Try again."

  /// `playlistName` is the name of the playlist the change is for, where
  /// this iPhone knows it.
  public init(_ pending: PendingMutation, playlistName: String? = nil) {
    let mutation = pending.mutation
    let song = mutation.track.map { "\($0.title) · \($0.artist)" }
    let playlist = playlistName.map { " " + $0 } ?? ""
    switch mutation.action {
    case "append":
      title = "Add to playlist" + playlist
      detail = song
      symbol = "music.note.list"
    case "remove":
      title = "Remove from playlist" + playlist
      detail = song
      symbol = "music.note.list"
    case "create":
      title = "New playlist"
      detail = mutation.name
      symbol = "music.note.list"
    case "delete":
      title = "Delete playlist"
      detail = playlistName ?? mutation.name
      symbol = "trash"
    case "favourite":
      title = mutation.favourite == false ? "Remove from favourites" : "Favourite"
      detail = song
      symbol = "star"
    case "preferences":
      title = "Preferences"
      detail = nil
      symbol = "slider.horizontal.3"
    default:
      title = "Change"
      detail = song ?? mutation.name
      symbol = "arrow.up.circle"
    }
    failed = pending.state != "queued"
    status = failed ? Self.couldNotSend : Self.waiting
  }

  /// The page's one button: "Send 3 changes now".
  public static func sendLabel(count: Int) -> String {
    count == 1 ? "Send 1 change now" : "Send \(count.formatted()) changes now"
  }
}

extension PendingMutation {
  /// Sent again on the next try: waiting once more, its old refusal gone.
  public func retried() -> PendingMutation {
    var copy = self
    copy.state = "queued"
    copy.message = nil
    return copy
  }
}

/// Your music quality as a list with a tick (D-120): your household's
/// default first, then each quality. A tap saves at once.
public struct QualityChoices: Sendable, Equatable {
  public struct Row: Sendable, Equatable, Identifiable {
    /// nil: the household default.
    public let value: String?
    public let title: String
    /// The default's own quality, in words ("MP3 320 kbps").
    public let detail: String?
    public var id: String { value ?? "inherit" }
  }
  public let rows: [Row]
  /// The row ticked now.
  public let selected: String?

  public init(_ settings: QualitySettings, scope: String = "system", id: String = "*") {
    let label = { (value: String) in
      settings.options.first { $0.value == value }?.label ?? value
    }
    rows =
      [Row(value: nil, title: "Household default", detail: label(settings.householdDefault))]
      + settings.options.map { Row(value: $0.value, title: $0.label, detail: nil) }
    let chosen = settings.preferences.first { $0.scope == scope && $0.itemId == id }?.preferred
    selected = chosen.flatMap { value in settings.options.contains { $0.value == value } ? value : nil }
  }

  /// What the Account row shows: the chosen quality, or the default's.
  public var summary: String? {
    rows.first { $0.value == selected }.flatMap { $0.value == nil ? $0.detail : $0.title }
  }
}

/// The queue under the playing song (D-120): the songs you chose, with their
/// count and time, then similar songs as their own section.
public struct UpNextLayout<Item: Sendable>: Sendable {
  public let chosen: [Item]
  public let similar: [Item]
  /// No song of yours follows: "Nothing next".
  public var nothingNext: Bool { chosen.isEmpty }
  /// "Then similar songs" shows under Nothing next where Keep playing is on,
  /// before any have arrived.
  public let keepPlaying: Bool
  public var showsSimilarHeading: Bool { !similar.isEmpty || (keepPlaying && chosen.isEmpty) }
  /// No song waits at all: offer Shuffle your library.
  public var offersShuffle: Bool { chosen.isEmpty && similar.isEmpty }

  /// `isSimilar` says which songs the queue added itself; each song is
  /// judged on its own, so one you added after them stays yours (Codex
  /// round 1 on batch E).
  public init(
    _ upcoming: [Item], keepPlaying: Bool, isSimilar: (Item) -> Bool
  ) {
    chosen = upcoming.filter { !isSimilar($0) }
    similar = upcoming.filter(isSimilar)
    self.keepPlaying = keepPlaying
  }

  public static var nothingNextTitle: String { "Nothing next" }
  public static var similarTitle: String { "Then similar songs" }
  public static var shuffleTitle: String { "Shuffle your library" }
}

/// "4 songs · 16 min": a count and the time the songs take.
public enum SongSpan {
  public static func summary(count: Int, seconds: Double) -> String {
    let songs = "\(count.formatted()) song\(count == 1 ? "" : "s")"
    guard seconds >= 1 else { return songs }
    let minutes = max(1, Int((seconds / 60).rounded()))
    let time =
      minutes < 60
      ? "\(minutes) min"
      : "\(minutes / 60) h" + (minutes % 60 == 0 ? "" : " \(minutes % 60) min")
    return songs + " · " + time
  }
  public static func summary(_ tracks: [Track]) -> String {
    summary(count: tracks.count, seconds: tracks.reduce(0) { $0 + max(0, $1.duration ?? 0) })
  }
}

/// A device's storage on the Downloads tab (D-120): how full it is and
/// "4.1 GB of 10 GB". The limit is chosen in whole binary gigabytes
/// ("5 GB" is 5 × 1024³ bytes), so both figures are read that way.
public struct StorageGauge: Sendable, Equatable {
  public let used: Int64
  public let limit: Int64
  public init(used: Int64, limit: Int64) {
    self.used = max(0, used)
    self.limit = max(0, limit)
  }
  /// 0 to 1, for the bar.
  public var fraction: Double {
    limit > 0 ? min(1, Double(used) / Double(limit)) : 0
  }
  /// "4.1 GB of 10 GB Storage limit".
  public var text: String { Self.size(used) + " of " + Self.size(limit) + " Storage limit" }
  static func size(_ bytes: Int64) -> String {
    let gigabyte = 1024.0 * 1024 * 1024
    let value = Double(bytes)
    if value < gigabyte {
      let megabytes = Int((value / (1024 * 1024)).rounded())
      return "\(megabytes) MB"
    }
    let tenths = (value / gigabyte * 10).rounded() / 10
    return tenths == tenths.rounded()
      ? "\(Int(tenths)) GB" : String(format: "%.1f GB", tenths)
  }
}

extension DownloadPlace {
  /// A Downloads tab card's line (D-120): its songs and what is moving now.
  /// "312 songs · 2 downloading", "70 songs · 4 waiting for Apple Watch",
  /// "1,284 songs in your library · 3 adding".
  public func hubSummary(songs: Int, active: Int) -> String {
    let count = "\(songs.formatted()) song\(songs == 1 ? "" : "s")"
    let lead = self == .server ? count + " in your library" : count
    guard active > 0 else { return lead }
    let moving: String =
      switch self {
      case .server: "adding"
      case .phone: "downloading"
      case .watch: "waiting for Apple Watch"
      }
    return lead + " · \(active.formatted()) " + moving
  }
}

/// Preferences' Playback section (D-120): every playback switch in one
/// place. Seamless playback and Crossfade moved here from Account; Crossfade
/// only means something with Seamless playback on.
public enum PlaybackSwitch: String, Sendable, CaseIterable {
  case keepPlaying, seamless, crossfade
  public var title: String {
    switch self {
    case .keepPlaying: "Keep playing similar songs"
    case .seamless: "Seamless playback"
    case .crossfade: "Crossfade"
    }
  }
  public static func shown(seamless: Bool) -> [PlaybackSwitch] {
    seamless ? allCases : [.keepPlaying, .seamless]
  }
}
