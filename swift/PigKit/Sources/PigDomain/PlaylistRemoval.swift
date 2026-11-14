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
  /// What to send for the song at `position` of `playlist` at `revision`.
  public func plan(playlist: String, position: Int, revision: Int) -> PlaylistRemoval.Sent {
    PlaylistRemoval.rebased(
      position: position, revision: revision, after: applied[Self.key(playlist, revision)] ?? [])
  }
  /// A removal was sent; it counts only once the server has applied it.
  public mutating func record(playlist: String, position: Int, revision: Int, applied: Bool) {
    guard applied else { return }
    self.applied[Self.key(playlist, revision), default: []].append(position)
  }
}
