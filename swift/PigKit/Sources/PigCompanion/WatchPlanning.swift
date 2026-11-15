import Foundation
import PigDomain

/// What following the playlists sent to the Watch means after a sync: which
/// songs leave, which are sent, and the subscriptions and exclusions to keep.
/// Pure, so the rules are tested without WatchConnectivity or a device.
public struct WatchPlaylistPlan: Equatable, Sendable {
  public struct Change: Equatable, Sendable {
    public var playlistId: String
    public var name: String?
    public var songIds: [String]
    public init(playlistId: String, name: String?, songIds: [String]) {
      self.playlistId = playlistId
      self.name = name
      self.songIds = songIds
    }
  }
  /// Songs a playlist has on the Watch now (from download records).
  public struct Sent: Sendable {
    public var name: String?
    public var songIds: Set<String>
    public init(name: String?, songIds: Set<String>) {
      self.name = name
      self.songIds = songIds
    }
  }
  /// A playlist as the synced library has it; nil songs when not loaded.
  public struct Listed: Sendable {
    public var name: String
    public var songIds: [String]?
    public init(name: String, songIds: [String]?) {
      self.name = name
      self.songIds = songIds
    }
  }
  public var release: [Change] = []
  public var send: [Change] = []
  public var subscriptions: [String: String]
  public var exclusions: [String: Set<String>]

  /// - Parameters:
  ///   - onDevice: every song with a Watch download record, for the 1,000-song limit.
  public static func make(
    subscriptions: [String: String], exclusions: [String: Set<String>],
    sent: [String: Sent], playlists: [String: Listed], onDevice: Set<String>,
    limit: Int = 1000
  ) -> WatchPlaylistPlan {
    var plan = WatchPlaylistPlan(subscriptions: subscriptions, exclusions: exclusions)
    var room = limit - onDevice.count
    var followed: [String: Sent] = [:]
    for (id, name) in subscriptions { followed[id] = Sent(name: name, songIds: []) }
    for (id, value) in sent {
      followed[id, default: Sent(name: value.name, songIds: [])].songIds.formUnion(value.songIds)
    }
    for id in followed.keys.sorted() {
      let current = followed[id]!
      // A deleted playlist leaves the Watch and is no longer followed.
      guard let listed = playlists[id] else {
        if !current.songIds.isEmpty {
          plan.release.append(
            Change(playlistId: id, name: current.name, songIds: current.songIds.sorted()))
        }
        plan.subscriptions[id] = nil
        plan.exclusions[id] = nil
        continue
      }
      guard let songs = listed.songIds else { continue }
      plan.subscriptions[id] = listed.name
      let listedSet = Set(songs)
      // An exclusion ends once its song leaves the playlist.
      let excluded = plan.exclusions[id, default: []].intersection(listedSet)
      plan.exclusions[id] = excluded.isEmpty ? nil : excluded
      let wanted = listedSet.subtracting(excluded)
      let gone = current.songIds.subtracting(wanted)
      if !gone.isEmpty {
        plan.release.append(Change(playlistId: id, name: listed.name, songIds: gone.sorted()))
      }
      // New songs, or every song when the playlist was renamed (to carry the name).
      let refresh = listed.name == current.name ? wanted.subtracting(current.songIds) : wanted
      var chosen: [String] = []
      for songId in refresh.sorted() {
        if onDevice.contains(songId) || current.songIds.contains(songId) {
          chosen.append(songId)
        } else if room > 0 {
          room -= 1
          chosen.append(songId)
        }
      }
      if !chosen.isEmpty {
        plan.send.append(Change(playlistId: id, name: listed.name, songIds: chosen))
      }
    }
    return plan
  }
}

extension WatchPlaylistPlan {
  /// Releases and sends what the plan says, stopping as soon as `isCurrent`
  /// fails after any wait (Codex round 2 on #209): a Remove all or removal
  /// made meanwhile must not be undone by a stale plan. True when it ran to
  /// the end, so the caller may save the plan's subscriptions and exclusions.
  @MainActor public func apply(
    isCurrent: () -> Bool,
    release: (_ ids: [String], _ origin: DownloadOrigin) async -> Void,
    lookup: (String) async -> Track?,
    enqueue: (_ tracks: [Track], _ origin: DownloadOrigin) async -> Void
  ) async -> Bool {
    for change in self.release {
      guard isCurrent() else { return false }
      await release(
        change.songIds, DownloadOrigin(playlistId: change.playlistId, playlistName: change.name))
    }
    for change in send {
      var tracks: [Track] = []
      for songId in change.songIds {
        guard isCurrent() else { return false }
        if let track = await lookup(songId) { tracks.append(track) }
      }
      guard isCurrent() else { return false }
      if !tracks.isEmpty {
        await enqueue(
          tracks, DownloadOrigin(playlistId: change.playlistId, playlistName: change.name))
      }
    }
    return isCurrent()
  }
}

extension CompanionManifest {
  /// Whether a queue keeps playing when this manifest replaces `previous`:
  /// every queued song must still be present with the same file.
  public func keepsPlaying(_ queued: [String], replacing previous: CompanionManifest) -> Bool {
    let next = Dictionary(songs.map { ($0.id, $0.checksum) }, uniquingKeysWith: { first, _ in first })
    let old = Dictionary(
      previous.songs.map { ($0.id, $0.checksum) }, uniquingKeysWith: { first, _ in first })
    return queued.allSatisfy { id in old[id] != nil && old[id] == next[id] }
  }
}

/// The one thing the iPhone's Watch Downloads page says, only when Mark must
/// act (D-105): nothing while all is well or while a state is settling.
public enum WatchAttention: Equatable, Sendable {
  /// The connection failed outright; shown with Retry.
  case unreachable
  /// The Watch app has been missing for `missingGrace` since activation settled.
  case notInstalled
  /// Songs have waited `waitLimit` or more to reach the Watch.
  case openToFinish

  /// A TestFlight update reports the Watch app missing for a moment.
  public static let missingGrace: TimeInterval = 2 * 60
  public static let waitLimit: TimeInterval = 30 * 60

  /// `missingSince`: when the Watch app was first reported not installed
  /// after activation settled (see `missing(since:...)`); `oldestWait`: when
  /// the longest-waiting song started waiting for the Watch.
  public init?(
    activated: Bool, error: String?, missingSince: Date?, oldestWait: Date?, now: Date
  ) {
    if error != nil {
      self = .unreachable
    } else if activated, let missingSince, now.timeIntervalSince(missingSince) >= Self.missingGrace
    {
      self = .notInstalled
    } else if let oldestWait, now.timeIntervalSince(oldestWait) >= Self.waitLimit {
      self = .openToFinish
    } else {
      return nil
    }
  }

  /// When the Watch app started being reported missing, carried from
  /// `previous`: nil until activation settled with a Watch paired, and again
  /// as soon as the app is reported installed.
  public static func missing(
    since previous: Date?, activated: Bool, paired: Bool, installed: Bool, now: Date
  ) -> Date? {
    guard activated, paired, !installed else { return nil }
    return previous ?? now
  }

  /// When the longest-waiting of `waiting` (songs ready on the iPhone and
  /// not yet on the Watch) started waiting: its copy's readiness, else its send.
  public static func oldestWait(_ waiting: some Sequence<String>, log: WatchDeliveryLog) -> Date? {
    waiting.compactMap { id in log.entries[id].map { $0.ready ?? $0.sent } }.min()
  }

  public var text: String {
    switch self {
    case .unreachable: "Can't reach your Watch"
    case .notInstalled: "PigMusic isn't on your Watch"
    case .openToFinish: "Open PigMusic on your Watch to finish"
    }
  }
}

/// "Ready for your run": only songs the Watch confirmed count.
public struct WatchReadinessSummary: Equatable, Sendable {
  public var ready: Int
  public var waiting: Int
  public var failed: Int
  public var seconds: Double
  public var bytes: Int64
  public var complete: Bool { waiting == 0 && failed == 0 }

  public init(ready: Int, waiting: Int, failed: Int, seconds: Double, bytes: Int64) {
    self.ready = ready
    self.waiting = waiting
    self.failed = failed
    self.seconds = seconds
    self.bytes = bytes
  }

  /// Whole hours and minutes, as shown on both devices.
  public var listening: String {
    let minutes = Int(seconds / 60)
    return minutes >= 60 ? "\(minutes / 60) h \(minutes % 60) min" : "\(minutes) min"
  }
}
