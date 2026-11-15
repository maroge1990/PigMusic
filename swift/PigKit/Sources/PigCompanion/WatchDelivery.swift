import Foundation

/// What the phone hands to WatchConnectivity on each publish, decided
/// without the link so it can be tested (#46).
public enum WatchDelivery {
  public struct Audio: Equatable, Sendable {
    /// Songs whose audio goes now, in manifest order.
    public var send: [CompanionSong]
    /// Some song the Watch still needs does not fit its storage budget.
    public var budgetFull: Bool
    /// Ready songs not in the published manifest yet, whose audio goes now
    /// for the next library version (#219).
    public var ahead: [CompanionSong] = []
    public init(send: [CompanionSong], budgetFull: Bool, ahead: [CompanionSong] = []) {
      self.send = send
      self.budgetFull = budgetFull
      self.ahead = ahead
    }
  }

  /// Songs the Watch may hold, waiting for the manifest that names them, at
  /// most. Its inbox keeps 40 transfers and 512 MB (builds up to 28 too).
  public static let holdLimit = 24
  public static let holdBytes: Int64 = 256 * 1024 * 1024

  /// The identity of one song's audio, as transfers and the send log key it.
  public static func identity(_ song: CompanionSong) -> String { "\(song.id):\(song.checksum)" }

  /// Audio still to send: every song the Watch lacks, while the budget it
  /// last reported allows, skipping songs already in flight, sent too
  /// recently (`due` says no) or without a local file. A song that does not
  /// fit is skipped; smaller ones after it may still go.
  ///
  /// Audio no longer waits for the Watch to confirm the manifest (#219): a
  /// song the Watch can file on arrival (`committed`: in the last manifest it
  /// confirmed applying) goes at once; any other song, from the published
  /// manifest or `ahead` of it, waits on the Watch for its manifest, so only
  /// `holdLimit` songs and `holdBytes` of those are on their way at a time.
  public static func audio(
    songs: [CompanionSong], ahead: [CompanionSong] = [], receipt: CompanionReceipt,
    committed: Set<String>? = nil, inFlight: Set<String>, hasFile: (String) -> Bool,
    due: (String) -> Bool
  ) -> Audio {
    var plan = Audio(send: [], budgetFull: false)
    var remaining = receipt.budgetBytes - receipt.usedBytes
    var held = 0
    var heldBytes: Int64 = 0
    let candidates = songs.map { ($0, false) } + ahead.map { ($0, true) }
    for (song, isAhead) in candidates where receipt.ready[song.id] != song.checksum {
      guard song.bytes <= remaining else {
        plan.budgetFull = true
        continue
      }
      remaining -= song.bytes
      let identity = Self.identity(song)
      // Without `committed`, every song of the published manifest files at once.
      let holds = isAhead || !(committed?.contains(identity) ?? true)
      guard !inFlight.contains(identity), due("audio:" + identity) else {
        if holds {
          held += 1
          heldBytes += song.bytes
        }
        continue
      }
      guard hasFile(song.id) else { continue }
      if holds {
        guard held < Self.holdLimit, heldBytes + song.bytes <= Self.holdBytes else { continue }
        held += 1
        heldBytes += song.bytes
      }
      if isAhead { plan.ahead.append(song) } else { plan.send.append(song) }
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
