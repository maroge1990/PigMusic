/// Which songs the iPhone shows as Requested (#206). A song is marked while
/// its request waits in the outbox, and after it is sent until the server's
/// request list changes in a way the phone can't match to songs (a request
/// cancelled, finished requests cleared): then the sent marks go, so a song
/// never stays Requested after its request is gone (Codex round 1). Asking
/// again for a song still requested is harmless: the server answers that it
/// is already asked for.
public struct RequestMarks: Sendable, Equatable {
  /// In the request outbox, not yet confirmed by the server.
  public private(set) var waiting: Set<String> = []
  /// Confirmed by the server since the request list last changed.
  public private(set) var sent: Set<String> = []
  public init(waiting: Set<String> = []) { self.waiting = waiting }
  public func contains(_ id: String) -> Bool { waiting.contains(id) || sent.contains(id) }
  public mutating func queued(_ id: String) { waiting.insert(id) }
  public mutating func sent(_ ids: some Sequence<String>) {
    for id in ids where waiting.remove(id) != nil { sent.insert(id) }
  }
  public mutating func refused(_ ids: some Sequence<String>) {
    waiting.subtract(ids)
    sent.subtract(ids)
  }
  /// A request was cancelled or cleared: forget every sent mark.
  public mutating func requestsChanged() { sent = [] }
}
