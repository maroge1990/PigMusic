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
  /// A local clock, to order an Add's answer against a Resume (Codex round
  /// 1 on batch K), and when each request's last Resume here went through.
  private var clock = 0
  private var resumedAt: [String: Int] = [:]
  /// Every request the server named for a song an Add left paused here,
  /// kept after a Resume (Codex round 2 on batch K): when the request list
  /// reports one of them paused again, the song is marked again.
  private var named: [String: Set<String>] = [:]
  /// Taken before an Add is sent: its answer is placed by when it started.
  public mutating func asking() -> Int {
    clock += 1
    return clock
  }
  /// The server left these songs paused, as the person had them (#264), each
  /// under the server requests named (track ID to request IDs), answering an
  /// Add taken at `asked`. A request whose Resume went through after that
  /// Add started is not marked again: the server can only have answered
  /// "paused" before the Resume (a pause after it is the request list's to
  /// show, `reconcile`), so the song is being added (Codex round 1 on batch
  /// K). A Resume that went through before the Add started can't be undone by
  /// its answer: that pause is a new one.
  public mutating func pausedOnServer(_ requests: [String: [String]], asked: Int) {
    pausedOnServer(requests, askedAt: requests.mapValues { _ in asked })
  }
  /// `pausedOnServer`, each song's Add taken when its own request was sent
  /// (Codex round 2 on batch K: one tick for a whole drain made a fresh
  /// pause after a Resume look stale). A song with no tick counts as asked
  /// before any Resume: not marked, which the request list heals.
  public mutating func pausedOnServer(_ requests: [String: [String]], askedAt: [String: Int]) {
    for (id, requestIds) in requests where waiting.remove(id) != nil {
      named[id, default: []].formUnion(requestIds)
      let asked = askedAt[id] ?? 0
      let current = requestIds.filter { resumedAt[$0].map { $0 < asked } ?? true }
      guard current.isEmpty, !requestIds.isEmpty else {
        paused.insert(id)
        pausedSongs[id, default: []].formUnion(current)
        continue
      }
      sent.insert(id)
    }
  }
  /// The person resumed this server request (Codex round 1 on batch D,
  /// #276): only the songs left paused under it lose their paused mark;
  /// other songs' marks stay, whatever their names. A mark the server didn't
  /// name a request for goes on any Resume rather than stay stuck. An Add
  /// answer already on its way can't mark it again.
  public mutating func resumed(request id: String) {
    clock += 1
    resumedAt[id] = clock
    for (song, requestIds) in pausedSongs where requestIds.isEmpty || requestIds.contains(id) {
      paused.remove(song)
      pausedSongs[song] = nil
    }
  }
  /// The server's request list (Codex round 1 on batch K): `statuses` holds
  /// the status of each request it reports, by request ID. A paused mark is
  /// cleared only when the list reports every one of its requests, none of
  /// them paused (resumed on another device, a Resume whose answer was lost,
  /// finished, cancelled). A request the list leaves out says nothing: the
  /// server folds a song's jobs into one entry, so a paused job can be
  /// missing from even a one-page list (Codex round 2): the mark stays. An
  /// older server's mark with no request is left as it is.
  /// A song whose named request the list reports paused is marked paused
  /// again (Codex round 2 on batch K): a pause an Add's answer was too old
  /// to show, or one made on another device after a Resume, heals here.
  public mutating func reconcile(statuses: [String: String]) {
    for (song, requestIds) in named {
      let pausedNow = requestIds.filter { statuses[$0] == "paused" }
      guard !pausedNow.isEmpty, !waiting.contains(song) else { continue }
      sent.remove(song)
      paused.insert(song)
      pausedSongs[song, default: []].formUnion(pausedNow)
    }
    for (song, requestIds) in pausedSongs where !requestIds.isEmpty {
      let reported = requestIds.compactMap { statuses[$0] }
      if reported.count == requestIds.count, !reported.contains("paused") {
        paused.remove(song)
        pausedSongs[song] = nil
      }
    }
  }
  public mutating func queued(_ id: String) { waiting.insert(id) }
  public mutating func sent(_ ids: some Sequence<String>) {
    for id in ids where waiting.remove(id) != nil { sent.insert(id) }
  }
  public mutating func refused(_ ids: some Sequence<String>) {
    let ids = Set(ids)
    waiting.subtract(ids)
    sent.subtract(ids)
    paused.subtract(ids)
    pausedSongs = pausedSongs.filter { paused.contains($0.key) }
    named = named.filter { !ids.contains($0.key) }
  }
  /// A request was cancelled or cleared: forget every sent and paused mark.
  public mutating func requestsChanged() {
    sent = []
    paused = []
    pausedSongs = [:]
    named = [:]
  }
  /// Every marked song.
  public var all: Set<String> { waiting.union(sent).union(paused) }
  /// These songs are in the person's library now (#211): a request that
  /// finished must not leave them Requested.
  public mutating func settled(_ ids: some Sequence<String>) {
    let ids = Set(ids)
    waiting.subtract(ids)
    sent.subtract(ids)
    paused.subtract(ids)
    pausedSongs = pausedSongs.filter { paused.contains($0.key) }
    named = named.filter { !ids.contains($0.key) }
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
