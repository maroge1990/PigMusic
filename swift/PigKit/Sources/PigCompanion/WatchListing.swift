import Foundation
import PigDomain

/// Which songs the Watch's manifest lists (#218). The Watch deletes the
/// audio of every song its manifest doesn't list, so only a real removal
/// (the person removed or released the song, or reconcile dropped it: its
/// record is gone) may drop a song. A hand-over, a refetch or a missing
/// iPhone file never does: such a song stays listed with the checksum it was
/// listed with, and the Watch keeps its audio, or waits for it.
public enum WatchListing {
  public struct Result: Equatable, Sendable {
    /// The songs to list, by id.
    public var songs: [CompanionSong]
    /// Songs listed only because an earlier version listed them: the iPhone
    /// has no ready copy now (one fetched again, or a file gone).
    public var kept: Set<String>
    /// The kept songs the Watch's last applied confirmation said it holds:
    /// without this rule, the next manifest would have deleted them there.
    public var held: Set<String>
    public init(songs: [CompanionSong], kept: Set<String> = [], held: Set<String> = []) {
      self.songs = songs
      self.kept = kept
      self.held = held
    }
  }

  /// The songs earlier versions listed for `account`, in the order given
  /// (the published manifest, the last one written, the one the Watch last
  /// applied). A manifest of another account counts for nothing; one that is
  /// missing (a failed publication cleared it) doesn't hide the others.
  public static func earlier(account: String?, _ manifests: CompanionManifest?...)
    -> [CompanionSong]
  {
    guard let account else { return [] }
    return manifests.compactMap { $0 }.filter { $0.context.account == account }.flatMap(\.songs)
  }

  /// `records`: the Watch archive's records; `hasFile`: whether the iPhone
  /// has a song's file; `earlier`: songs of earlier versions, the published
  /// one first, then the one the Watch last confirmed applying; `held`: that
  /// confirmation's songs (id to checksum).
  public static func songs(
    records: [DownloadRecord], hasFile: (String) -> Bool, earlier: [CompanionSong],
    held: [String: String] = [:]
  ) -> Result {
    let before = Dictionary(earlier.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
    var result = Result(songs: [])
    var seen: Set<String> = []
    func keep(_ record: DownloadRecord, checksum: String, bytes: Int64) {
      result.songs.append(CompanionSong(track: record.track, checksum: checksum, bytes: bytes))
      result.kept.insert(record.id)
      if held[record.id] == checksum { result.held.insert(record.id) }
    }
    for record in records {
      guard seen.insert(record.id).inserted else { continue }
      // A ready song: the iPhone has its file, or the Watch confirmed it.
      if record.state == "ready", let checksum = record.checksum,
        hasFile(record.id) || record.delivered
      {
        result.songs.append(
          CompanionSong(track: record.track, checksum: checksum, bytes: record.total))
      } else if let checksum = record.watchChecksum, let bytes = record.watchBytes, bytes > 0 {
        // Fetched again: the record itself keeps what the Watch was listed
        // with, saved with the records, so no earlier manifest is needed.
        keep(record, checksum: checksum, bytes: bytes)
      } else if let song = before[record.id] {
        keep(record, checksum: song.checksum, bytes: song.bytes)
      }
    }
    result.songs.sort { $0.id < $1.id }
    return result
  }
}
