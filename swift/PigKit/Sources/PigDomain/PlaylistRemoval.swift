import Foundation

/// A playlist song removed with Undo keeps the position and revision it was
/// removed from: entry IDs are positions, so finding the song again later
/// could remove a different one (Codex, batch 9). Only this phone's own
/// earlier removals from the same revision shift it; any other change makes
/// the server answer 409, and the edit waits for review.
public enum PlaylistRemoval {
  public struct Sent: Equatable, Sendable {
    public let position: Int
    public let revision: Int
    public init(_ position: Int, _ revision: Int) {
      self.position = position
      self.revision = revision
    }
  }
  /// `earlier`: the positions, in that same revision, of removals from it
  /// that the server has applied, in order. A removal only queued must not be
  /// counted: it may still be refused and discarded.
  public static func rebased(position: Int, revision: Int, after earlier: [Int]) -> Sent {
    Sent(position - earlier.filter { $0 < position }.count, revision + earlier.count)
  }
}

/// This iPhone's removals the server applied, per playlist version, which
/// shift the removals still to be sent from that version.
public struct PlaylistRemovalLedger: Sendable {
  private var applied: [String: [Int]] = [:]
  public init() {}
  private static func key(_ playlist: String, _ revision: Int) -> String {
    playlist + "@" + String(revision)
  }
  /// What to send for the song at `position` of `playlist` at `revision`;
  /// nil when that song is already removed (a stale copy of the playlist
  /// shows it again): sending it would remove its neighbour (Codex).
  public func plan(playlist: String, position: Int, revision: Int) -> PlaylistRemoval.Sent? {
    let earlier = applied[Self.key(playlist, revision)] ?? []
    guard !earlier.contains(position) else { return nil }
    return PlaylistRemoval.rebased(position: position, revision: revision, after: earlier)
  }
  /// Whether this iPhone already removed the song at `position` of that version.
  public func removed(playlist: String, position: Int, revision: Int) -> Bool {
    applied[Self.key(playlist, revision)]?.contains(position) ?? false
  }
  /// A removal was sent; it counts only once the server has applied it.
  public mutating func record(playlist: String, position: Int, revision: Int, applied: Bool) {
    guard applied else { return }
    self.applied[Self.key(playlist, revision), default: []].append(position)
  }
}

extension PlaylistRemoval {
  /// The Undo id of removing the song at `position` from that version of a
  /// playlist (entry IDs are positions).
  public static func undoKey(playlist: String, revision: Int, position: Int) -> String {
    "playlist-entry:\(playlist)@\(revision)#\(position)"
  }

  /// A playlist's songs as its page and both collection-menu entry points
  /// use them (Codex round 1 on batch D): less those whose removal waits for
  /// its Undo (`hides`) or that this iPhone already removed from that
  /// version (`ledger`), with their positions. Undo brings a song back.
  /// A removal still waiting in the edit outbox (`pending`, offline) is left
  /// out too (#277): one whose playlist, revision and position are what this
  /// copy's song would be sent as. It stays apart from the ledger, which
  /// counts only removals the server applied.
  public static func shown(
    _ playlist: Playlist, ledger: PlaylistRemovalLedger, pending: [PendingMutation] = [],
    hides: (String) -> Bool
  ) -> [(position: Int, entry: PlaylistEntry)] {
    let queued: [Sent] = pending.compactMap { item in
      let edit = item.mutation
      guard item.state == "queued", edit.action == "remove", edit.playlistId == playlist.id,
        let revision = edit.revision, let position = edit.position
      else { return nil }
      return Sent(position, revision)
    }
    return (playlist.entries ?? []).enumerated().filter { position, _ in
      guard
        let sent = ledger.plan(playlist: playlist.id, position: position, revision: playlist.revision)
      else { return false }
      return !queued.contains(sent)
        && !hides(undoKey(playlist: playlist.id, revision: playlist.revision, position: position))
    }.map { (position: $0.offset, entry: $0.element) }
  }
}
