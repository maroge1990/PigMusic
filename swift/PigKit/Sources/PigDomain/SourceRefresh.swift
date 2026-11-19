import Foundation

/// What a refresh does about songs the server changed or removed since their
/// audio was cached or downloaded (#76): which the player is told of, and
/// which downloads stay. Nothing a playing queue depends on is deleted before
/// the player has been told (Codex confirmation on batch E): when it can't be
/// told, the changed songs' downloads stay as they are, so the next refresh
/// finds them changed again.
public enum SourceRefresh {
  public struct Download: Sendable, Equatable {
    public let id: String
    public let ready: Bool
    public let revision: String?
    public init(id: String, ready: Bool, revision: String?) {
      self.id = id
      self.ready = ready
      self.revision = revision
    }
  }
  public struct Plan: Sendable, Equatable {
    /// Songs whose cached or downloaded audio is stale.
    public var changed: Set<String>
    /// The revision each download may keep; a download not named goes.
    public var allowed: [String: String?]
  }
  /// `cached`: revisions of the player's cached audio; `known`: the library's
  /// revisions, absent for a song no longer in it.
  public static func plan(
    cached: [String: String?], downloads: [Download], known: [String: String?]
  ) -> Plan {
    var changed = Set(cached.filter { id, revision in known[id].map { $0 != revision } ?? true }.keys)
    var allowed: [String: String?] = [:]
    for download in downloads {
      if let current = known[download.id] {
        allowed.updateValue(current, forKey: download.id)
        if download.ready && download.revision != current { changed.insert(download.id) }
      } else if download.ready {
        changed.insert(download.id)
      }
    }
    return Plan(changed: changed, allowed: allowed)
  }
  /// The player wasn't told of `plan.changed`: their downloads keep their own
  /// revision, so they stay until a later refresh tells it.
  public static func deferring(_ plan: Plan, downloads: [Download]) -> [String: String?] {
    var allowed = plan.allowed
    for download in downloads where plan.changed.contains(download.id) {
      allowed.updateValue(download.revision, forKey: download.id)
    }
    return allowed
  }
}
