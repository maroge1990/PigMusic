import Foundation

/// Which songs the iPhone shows as Requested (#206). A song is marked while
/// its request waits in the outbox, and after it is sent until the server's
/// request list changes in a way the phone can't match to songs (a request
/// cancelled, finished requests cleared): then the sent marks go, so a song
/// never stays Requested after its request is gone (Codex round 1). Asking
/// again for a song still requested is harmless: the server answers that it
/// is already asked for. A song the server shows in the library, or this
/// phone's library holds, is no longer marked (#211).
public struct RequestMarks: Sendable, Equatable {
  /// In the request outbox, not yet confirmed by the server.
  public private(set) var waiting: Set<String> = []
  /// Confirmed by the server since the request list last changed.
  public private(set) var sent: Set<String> = []
  /// Added again while the person had paused it: still paused (#264).
  public private(set) var paused: Set<String> = []
  public init(waiting: Set<String> = []) { self.waiting = waiting }
  public func contains(_ id: String) -> Bool {
    waiting.contains(id) || sent.contains(id) || paused.contains(id)
  }
  /// Each paused song's name, to match the request that resumes it: the
  /// server's request list names songs, not this iPhone's ids.
  private var pausedSongs: [String: String] = [:]
  /// The server left these songs paused, as the person had them (#264).
  public mutating func pausedOnServer(_ tracks: some Sequence<Track>) {
    for track in tracks where waiting.remove(track.id) != nil {
      paused.insert(track.id)
      pausedSongs[track.id] = Self.song(title: track.title, artist: track.artist, album: track.album)
    }
  }
  /// The person resumed this song's request (Codex round 1 on batch D): only
  /// its paused mark goes; other songs' marks stay.
  public mutating func resumed(title: String, artist: String, album: String?) {
    let name = Self.song(title: title, artist: artist, album: album)
    for (id, song) in pausedSongs where song == name {
      paused.remove(id)
      pausedSongs[id] = nil
    }
  }
  private static func song(title: String, artist: String, album: String?) -> String {
    [title, artist, album ?? ""]
      .map { $0.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() }
      .joined(separator: "\u{1F}")
  }
  public mutating func queued(_ id: String) { waiting.insert(id) }
  public mutating func sent(_ ids: some Sequence<String>) {
    for id in ids where waiting.remove(id) != nil { sent.insert(id) }
  }
  public mutating func refused(_ ids: some Sequence<String>) {
    waiting.subtract(ids)
    sent.subtract(ids)
    paused.subtract(ids)
  }
  /// A request was cancelled or cleared: forget every sent and paused mark.
  public mutating func requestsChanged() {
    sent = []
    paused = []
    pausedSongs = [:]
  }
  /// Every marked song.
  public var all: Set<String> { waiting.union(sent).union(paused) }
  /// These songs are in the person's library now (#211): a request that
  /// finished must not leave them Requested.
  public mutating func settled(_ ids: some Sequence<String>) {
    waiting.subtract(ids)
    sent.subtract(ids)
    paused.subtract(ids)
  }
  /// A fresh server answer about these songs (#211): one the server says is
  /// in the library, or whose request failed, is no longer marked, under
  /// its own id or its library id.
  public mutating func observed(_ tracks: some Sequence<Track>) {
    for track in tracks where ["in_library", "failed"].contains(track.serverState) {
      settled([track.id] + (track.libraryTrackId.map { [$0] } ?? []))
    }
  }
}
