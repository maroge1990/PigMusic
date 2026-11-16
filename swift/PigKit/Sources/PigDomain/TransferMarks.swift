import Foundation

/// The mark on the right of a transfer on the three Downloads pages (D-109):
/// clock (waiting), progress ring, pause circle, retry mark (failed) or check.
public enum TransferMark: Equatable, Sendable {
  case waiting
  /// The share done, 0 to 1.
  case downloading(Double)
  case paused
  case failed
  case done
}

/// What tapping a mark does.
public enum TransferTap: Equatable, Sendable {
  case pause, resume, retry
}

/// A song's mark and what tapping it does (D-109): tap pauses a waiting or
/// downloading song, resumes a paused one and retries a failed one. A mark
/// with no tap (done, or a step that can't be paused) is only shown.
public struct TransferControl: Equatable, Sendable {
  public let mark: TransferMark
  public let tap: TransferTap?
  public init(_ mark: TransferMark, tap: TransferTap?) {
    self.mark = mark
    self.tap = tap
  }
  /// For VoiceOver: "Pause", "Resume" or "Retry".
  public var label: String? { tap.map(Self.verb) }
  static func verb(_ tap: TransferTap) -> String {
    switch tap {
    case .pause: "Pause"
    case .resume: "Resume"
    case .retry: "Retry"
    }
  }

  /// A download to this iPhone.
  public static func phone(_ record: DownloadRecord, progress: Double?) -> TransferControl {
    if record.isPaused { return TransferControl(.paused, tap: .resume) }
    return switch record.state {
    case "ready": TransferControl(.done, tap: nil)
    case "failed": TransferControl(.failed, tap: .retry)
    case "downloading": TransferControl(.downloading(clamp(progress)), tap: .pause)
    default: TransferControl(.waiting, tap: .pause)
    }
  }

  /// A song sent to Apple Watch. Only the iPhone's preparation of its copy
  /// can be paused; once ready it waits for the Watch, which can't.
  public static func watch(_ record: DownloadRecord, onWatch: Bool, progress: Double?)
    -> TransferControl
  {
    if onWatch { return TransferControl(.done, tap: nil) }
    if record.isPaused { return TransferControl(.paused, tap: .resume) }
    return switch record.state {
    case "ready": TransferControl(.waiting, tap: nil)
    case "failed": TransferControl(.failed, tap: .retry)
    case "downloading": TransferControl(.downloading(clamp(progress)), tap: .pause)
    default: TransferControl(.waiting, tap: .pause)
    }
  }

  /// The server's statuses a request can be paused in.
  public static let pausableRequests: Set<String> = [
    "waiting_for_sources", "queued", "searching", "downloading", "validating",
  ]
  /// A server request for one song, by its status.
  public static func server(status: String, progress: Double?) -> TransferControl {
    switch status {
    case "paused": return TransferControl(.paused, tap: .resume)
    case "failed", "cancelled": return TransferControl(.failed, tap: .retry)
    case "completed", "downloaded": return TransferControl(.done, tap: nil)
    case "downloading": return TransferControl(.downloading(clamp(progress)), tap: .pause)
    default:
      return TransferControl(.waiting, tap: pausableRequests.contains(status) ? .pause : nil)
    }
  }

  static func clamp(_ value: Double?) -> Double { min(1, max(0, value ?? 0)) }
}

/// An album's (or playlist's) mark, which acts on all its songs (D-110): when
/// any failed, the retry mark retries every failed one; otherwise the mark
/// pauses every song still going, or, once those are all paused, resumes them.
public struct TransferGroupControl: Equatable, Sendable {
  public let mark: TransferMark
  public let tap: TransferTap?
  /// The songs the tap acts on.
  public let ids: [String]
  /// Counts for the row's summary.
  public let songs: Int
  public let done: Int
  public let going: Int
  public let waiting: Int
  public let paused: Int
  public let failed: Int
  /// The share of the group done, 0 to 1: finished songs plus the parts
  /// downloaded of those under way.
  public let progress: Double

  public init(_ songs: [(id: String, control: TransferControl)]) {
    func ids(_ tap: TransferTap) -> [String] {
      songs.filter { $0.control.tap == tap }.map(\.id)
    }
    var done = 0
    var going = 0
    var waiting = 0
    var paused = 0
    var failed = 0
    var share = 0.0
    for song in songs {
      switch song.control.mark {
      case .done:
        done += 1
        share += 1
      case .downloading(let part):
        going += 1
        share += part
      case .waiting: waiting += 1
      case .paused: paused += 1
      case .failed: failed += 1
      }
    }
    self.songs = songs.count
    self.done = done
    self.going = going
    self.waiting = waiting
    self.paused = paused
    self.failed = failed
    progress = songs.isEmpty ? 1 : share / Double(songs.count)
    let retry = ids(.retry)
    let pause = ids(.pause)
    let resume = ids(.resume)
    if !retry.isEmpty {
      (mark, tap, self.ids) = (.failed, .retry, retry)
    } else if !pause.isEmpty {
      (mark, tap, self.ids) = (going > 0 ? .downloading(progress) : .waiting, .pause, pause)
    } else if !resume.isEmpty {
      (mark, tap, self.ids) = (.paused, .resume, resume)
    } else if done == songs.count {
      (mark, tap, self.ids) = (.done, nil, [])
    } else {
      (mark, tap, self.ids) = (going > 0 ? .downloading(progress) : .waiting, nil, [])
    }
  }
  /// For VoiceOver: "Pause album", "Resume playlist", "Retry album".
  public func label(_ noun: String = "album") -> String? {
    tap.map { TransferControl.verb($0) + " " + noun }
  }
  /// "2 of 5 songs on your phone · 1 downloading · 1 waiting", in the
  /// words of the Server page's summary.
  public func summary(done place: String) -> String {
    var parts = ["\(done) of \(songs) song\(songs == 1 ? "" : "s") \(place)"]
    if going > 0 { parts.append("\(going) downloading") }
    if waiting > 0 { parts.append("\(waiting) waiting") }
    if paused > 0 { parts.append("\(paused) paused") }
    if failed > 0 { parts.append("\(failed) failed") }
    return parts.joined(separator: " · ")
  }
}

/// Downloads grouped the way they were added: by album, or by playlist (#209,
/// D-109). A song added both ways appears in both groups. Playlists first,
/// then albums, each by title; an album's songs in disc and track order, a
/// playlist's in the order they were added.
public struct DownloadGrouping: Sendable, Identifiable {
  public let id: String
  public let title: String
  /// The album's artist, or "Playlist".
  public let lead: String
  public let origin: DownloadOrigin
  public let records: [DownloadRecord]

  public static func build(_ records: [DownloadRecord]) -> [DownloadGrouping] {
    var playlists: [String: (name: String, records: [DownloadRecord])] = [:]
    var albums: [String: [DownloadRecord]] = [:]
    for record in records {
      for origin in record.effectiveOrigins {
        if let id = origin.playlistId {
          playlists[id, default: (origin.playlistName ?? "Playlist", [])].records.append(record)
        } else {
          let track = record.track
          let key = track.albumId ?? "\(track.albumArtist ?? track.artist)|\(track.album ?? "")"
          albums[key, default: []].append(record)
        }
      }
    }
    let playlistGroups = playlists.map { id, value in
      DownloadGrouping(
        id: "playlist:" + id, title: value.name, lead: "Playlist",
        origin: DownloadOrigin(playlistId: id, playlistName: value.name), records: value.records)
    }
    let albumGroups = albums.map { key, records in
      let sorted = records.sorted {
        ($0.track.discNumber ?? 1, $0.track.trackNumber ?? 0, $0.track.title)
          < ($1.track.discNumber ?? 1, $1.track.trackNumber ?? 0, $1.track.title)
      }
      let first = sorted[0].track
      return DownloadGrouping(
        id: "album:" + key, title: first.album.flatMap { $0.isEmpty ? nil : $0 } ?? first.title,
        lead: first.albumArtist ?? first.artist, origin: .direct, records: sorted)
    }
    func order(_ groups: [DownloadGrouping]) -> [DownloadGrouping] {
      groups.sorted { $0.title.localizedStandardCompare($1.title) == .orderedAscending }
    }
    return order(playlistGroups) + order(albumGroups)
  }

  /// Transfers grouped the same way, groups with a song still to finish
  /// first, as on the Server page.
  public static func transfers(
    _ records: [DownloadRecord], finished: (DownloadRecord) -> Bool
  ) -> [DownloadGrouping] {
    let groups = build(records)
    let open = groups.filter { !$0.records.allSatisfy(finished) }
    return open + groups.filter { $0.records.allSatisfy(finished) }
  }
}
