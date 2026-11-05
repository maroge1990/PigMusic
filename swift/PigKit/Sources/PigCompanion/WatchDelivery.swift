import Foundation

/// What the phone hands to WatchConnectivity on each publish, decided
/// without the link so it can be tested (#46).
public enum WatchDelivery {
  public struct Audio: Equatable, Sendable {
    /// Songs whose audio goes now, in manifest order.
    public var send: [CompanionSong]
    /// Some song the Watch still needs does not fit its storage budget.
    public var budgetFull: Bool
  }

  /// The identity of one song's audio, as transfers and the send log key it.
  public static func identity(_ song: CompanionSong) -> String { "\(song.id):\(song.checksum)" }

  /// Audio still to send once the Watch applied the manifest: every song it
  /// lacks, while the budget it reported allows, skipping songs already in
  /// flight, sent too recently (`due` says no) or without a local file. A
  /// song that does not fit is skipped; smaller ones after it may still go.
  public static func audio(
    songs: [CompanionSong], receipt: CompanionReceipt, inFlight: Set<String>,
    hasFile: (String) -> Bool, due: (String) -> Bool
  ) -> Audio {
    var plan = Audio(send: [], budgetFull: false)
    var remaining = receipt.budgetBytes - receipt.usedBytes
    for song in songs where receipt.ready[song.id] != song.checksum {
      guard song.bytes <= remaining else {
        plan.budgetFull = true
        continue
      }
      remaining -= song.bytes
      let identity = Self.identity(song)
      guard !inFlight.contains(identity), due("audio:" + identity), hasFile(song.id) else {
        continue
      }
      plan.send.append(song)
    }
    return plan
  }

  /// Manifest files that can go: older than three versions before `revision`
  /// and not named by a transfer the system is still finishing.
  public static func staleManifests(
    _ files: [URL], current: URL, revision: Int64, retained: Set<Int64>
  ) -> [URL] {
    files.filter { file in
      guard file.lastPathComponent.hasPrefix("watch-manifest-"), file != current else {
        return false
      }
      let number = Int64(
        file.deletingPathExtension().lastPathComponent.replacingOccurrences(
          of: "watch-manifest-", with: ""))
      guard let number else { return false }
      return !retained.contains(number) && number < revision - 3
    }
  }
}
