import Foundation
import PigDomain
import PigStore

/// Shuffle your library (D-120): up to 200 songs from the whole library in
/// a random order, less any whose removal waits for its Undo or is being
/// sent, judged after the read so one removed meanwhile is left out too
/// (Codex round 1 on batch E). nil when the account ended meanwhile.
@MainActor public enum LibraryShuffle {
  public static func songs(
    store: LibraryStore, isCurrent: () -> Bool, removing: (Track) -> Bool
  ) async throws -> [Track]? {
    guard isCurrent() else { return nil }
    let sample = try await store.shuffledTracks(limit: 200)
    guard isCurrent() else { return nil }
    return sample.filter { !removing($0) }
  }
}
