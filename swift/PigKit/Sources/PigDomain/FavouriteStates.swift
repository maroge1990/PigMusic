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
  /// Whether the star can be tapped: only an unknown state stops it. A
  /// library sync or another save is no reason to grey it (#212).
  public func canTap(_ id: String) -> Bool { values[id] != nil }
  /// A person's tap, shown at once (#212): flips the state before anything
  /// is saved and returns the change to save, or nil while it is unknown.
  public mutating func tap(_ id: String) -> FavouriteTap? {
    guard let current = values[id] else { return nil }
    change(id, !current)
    return FavouriteTap(id: id, value: !current, revision: revision(id))
  }
  /// The save of `tap` was refused: its old state comes back, unless a newer
  /// tap, change or clear has come since.
  public mutating func refused(_ tap: FavouriteTap) {
    guard revision(tap.id) == tap.revision else { return }
    change(tap.id, !tap.value)
  }
  public mutating func clear() {
    values = [:]
    revisions = [:]
    epoch += 1
  }
}

/// One tap on the favourite star, for putting it back if its save is refused.
public struct FavouriteTap: Sendable, Equatable {
  public let id: String
  public let value: Bool
  fileprivate let revision: Int
}
