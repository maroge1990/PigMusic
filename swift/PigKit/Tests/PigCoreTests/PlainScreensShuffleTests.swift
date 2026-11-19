import Foundation
import PigDomain
import PigStore
import PigSync
import Testing

/// Codex round 1 on batch E: Shuffle your library (D-120).
@MainActor struct PlainScreensShuffleTests {
  /// Shuffle your library leaves out a song whose removal waits for its
  /// Undo, and hands nothing back for an ended account.
  @Test func shuffleLeavesOutSongsBeingRemoved() async throws {
    let store = try LibraryStore(path: ":memory:")
    let tracks = (0..<5).map { Track(id: String($0), title: "Song \($0)", artist: "A") }
    try await store.replace(LibrarySnapshot(revision: "one", tracks: tracks))
    let songs = try await LibraryShuffle.songs(store: store, isCurrent: { true }) {
      $0.id == "2"
    }
    #expect(songs?.count == 4)
    #expect(songs?.contains { $0.id == "2" } == false)
    let ended = try await LibraryShuffle.songs(store: store, isCurrent: { false }) { _ in false }
    #expect(ended == nil)
  }
}
