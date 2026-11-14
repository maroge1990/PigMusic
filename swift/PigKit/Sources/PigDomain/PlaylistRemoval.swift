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
  /// `earlier`: the positions, in that same revision, of removals already
  /// sent from it, in the order they were sent.
  public static func rebased(position: Int, revision: Int, after earlier: [Int]) -> Sent {
    Sent(position - earlier.filter { $0 < position }.count, revision + earlier.count)
  }
}
