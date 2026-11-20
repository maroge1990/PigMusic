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
  /// The server requests each paused song was left paused under (#276):
  /// Resume in Downloads names a request, and the song an Add matched to it
  /// may be another edition's, or share its title, artist and album with
  /// another request, so the request ID is what identifies a paused mark.
  /// An empty set: the server didn't name them (an older server).
  private var pausedSongs: [String: Set<String>] = [:]
  /// The server left these songs paused, as the person had them (#264), each
  /// under the server requests named (track ID to request IDs).
  public mutating func pausedOnServer(_ requests: [String: [String]]) {
    for (id, requestIds) in requests where waiting.remove(id) != nil {
      paused.insert(id)
      pausedSongs[id, default: []].formUnion(requestIds)
    }
  }
  /// The person resumed this server request (Codex round 1 on batch D,
  /// #276): only the songs left paused under it lose their paused mark;
  /// other songs' marks stay, whatever their names. A mark the server didn't
  /// name a request for goes on any Resume rather than stay stuck.
  public mutating func resumed(request id: String) {
    for (song, requestIds) in pausedSongs where requestIds.isEmpty || requestIds.contains(id) {
      paused.remove(song)
      pausedSongs[song] = nil
    }
  }
  public mutating func queued(_ id: String) { waiting.insert(id) }
  public mutating func sent(_ ids: some Sequence<String>) {
    for id in ids where waiting.remove(id) != nil { sent.insert(id) }
  }
  public mutating func refused(_ ids: some Sequence<String>) {
    waiting.subtract(ids)
    sent.subtract(ids)
    paused.subtract(ids)
    pausedSongs = pausedSongs.filter { paused.contains($0.key) }
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
    pausedSongs = pausedSongs.filter { paused.contains($0.key) }
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
