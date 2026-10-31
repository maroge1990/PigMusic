import Foundation

/// Favourite state by song id, shared by the player's star and Song actions.
/// A server read that started before a person's change never overwrites it
/// (Codex round 1, #100): each change bumps the song's revision, and a read's
/// answer is kept only if the revision it started at is still current.
public struct FavouriteStates: Sendable {
  public private(set) var values: [String: Bool] = [:]
  private var revisions: [String: Int] = [:]
  /// Rises on every clear (sign-out), so a read started before it never
  /// counts after it, even for a song with no revision yet (#101).
  private var epoch = 0
  public init() {}
  public subscript(id: String) -> Bool? { values[id] }
  /// Taken when a read starts; hand it back with the answer.
  public func revision(_ id: String) -> Int { epoch << 32 | revisions[id, default: 0] }
  /// A read's answer: false (and ignored) when a change came after it started.
  @discardableResult public mutating func answer(_ id: String, _ value: Bool, since revision: Int)
    -> Bool
  {
    guard self.revision(id) == revision else { return false }
    values[id] = value
    return true
  }
  /// A change is being saved: reads already started no longer count.
  public mutating func invalidate(_ id: String) { revisions[id, default: 0] += 1 }
  /// A person's change, saved.
  public mutating func change(_ id: String, _ value: Bool) {
    invalidate(id)
    values[id] = value
  }
  public mutating func clear() {
    values = [:]
    revisions = [:]
    epoch += 1
  }
}
