import Foundation

/// What one accepted song file changed on the Watch (#219), judged by the
/// archive inside the same call that stores it, from its own library just
/// before the write. The Watch's refresh window reads only this: the view
/// model's receipt can still be empty on a cold background launch, or stale
/// while another delivery runs (Codex round 2 on batch G).
public struct CompanionAcceptance: Equatable, Sendable {
  /// This file made a copy ready that wasn't (false for a copy the Watch
  /// already had).
  public var newlyReady: Bool
  /// Songs the library listed that were not ready just before the write.
  public var waitingBefore: Int
  public init(newlyReady: Bool, waitingBefore: Int) {
    self.newlyReady = newlyReady
    self.waitingBefore = waitingBefore
  }

  /// The rule. `listed`: the library's song ids; `removed`: those on their
  /// way out (not waited for); `ready`: id to checksum of the copies ready
  /// before the write; `songId`/`checksum`: the file being stored.
  public static func judge(
    listed: some Sequence<String>, removed: Set<String>, ready: [String: String],
    songId: String, checksum: String
  ) -> CompanionAcceptance {
    let waiting = Set(listed).filter { !removed.contains($0) && ready[$0] == nil }.count
    return CompanionAcceptance(newlyReady: ready[songId] != checksum, waitingBefore: waiting)
  }
}
