import Foundation
import PigDomain

extension DownloadRecord {
  /// A Watch copy the Watch confirmed holding (#218): the record stays, so the
  /// Watch's library keeps listing the song and its storage still counts, but
  /// the iPhone's own file of it is gone. Saved with the records as a ready
  /// record without a file name; only the Watch's copies ever become one.
  public var delivered: Bool { state == "ready" && fileName == nil && checksum != nil }
}

/// What the Watch's confirmation means for the iPhone's copies of the songs
/// sent to it (#218). The iPhone kept a hidden AAC copy of every one; once the
/// Watch confirms holding a song, that copy is deleted, and a song the Watch
/// later loses is fetched from the server again.
///
/// Only a confirmation of an applied library version counts: until then the
/// Watch's list describes the version before. A song counts as on the Watch
/// when the confirmation lists it with the checksum the iPhone's record has;
/// one is lost only when the confirmed version names it with that checksum
/// and the Watch still doesn't list it, never from a version that didn't name
/// it.
public enum WatchHandOver {
  public struct Plan: Equatable, Sendable {
    /// Song id to checksum: copies the Watch holds, whose file goes.
    public var deliver: [String: String] = [:]
    /// Song id to checksum: songs the Watch lost, fetched again.
    public var refetch: [String: String] = [:]
    public var isEmpty: Bool { deliver.isEmpty && refetch.isEmpty }
    public init(deliver: [String: String] = [:], refetch: [String: String] = [:]) {
      self.deliver = deliver
      self.refetch = refetch
    }
  }

  /// `listed`: the confirmed version's songs (id to checksum); `ready`: the
  /// songs the Watch says it holds (id to checksum); `applied`: whether the
  /// Watch confirmed applying that version.
  public static func plan(
    records: [DownloadRecord], listed: [String: String], ready: [String: String], applied: Bool
  ) -> Plan {
    var plan = Plan()
    guard applied else { return plan }
    for record in records where record.state == "ready" {
      guard let checksum = record.checksum, listed[record.id] == checksum else { continue }
      let held = ready[record.id] == checksum
      if record.delivered {
        if !held { plan.refetch[record.id] = checksum }
      } else if held {
        plan.deliver[record.id] = checksum
      }
    }
    return plan
  }
}

/// One consistent reading of a `DownloadArchive` (see `listing(reusing:)`).
public struct DownloadListing: Sendable {
  public var revision: Int
  public var records: [DownloadRecord]
  /// Song id to the iPhone's file, for the ready songs that have one; nil
  /// when the caller's copy from the same revision still holds.
  public var files: [String: URL]?
  public init(revision: Int, records: [DownloadRecord], files: [String: URL]?) {
    self.revision = revision
    self.records = records
    self.files = files
  }
}
