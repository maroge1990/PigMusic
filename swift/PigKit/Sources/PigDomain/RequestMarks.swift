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
  /// The server left these songs paused, as the person had them (#264).
  public mutating func pausedOnServer(_ ids: some Sequence<String>) {
    for id in ids where waiting.remove(id) != nil { paused.insert(id) }
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
  /// A request was cancelled or cleared (or resumed): forget every sent and
  /// paused mark.
  public mutating func requestsChanged() {
    sent = []
    paused = []
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
