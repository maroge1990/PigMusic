import Foundation
import PigDomain
import PigPlayback
import Testing

/// Codex round 1 on batch E: the queue keeps your songs before its own.
struct PlainScreensQueueOrderTests {
  /// Add to queue goes before the similar songs the queue added itself, and
  /// stays yours even when it is one of the same songs.
  @Test func addToQueueGoesBeforeSimilarSongs() {
    let a = Track(id: "a", title: "A", artist: "X")
    let b = Track(id: "b", title: "B", artist: "X")
    let s = Track(id: "s", title: "S", artist: "Y")
    var queue = PlaybackQueue()
    queue.replace([a, b])
    queue.append([s], recommended: true)
    queue.append([s])
    #expect(queue.items.map(\.track.id) == ["a", "b", "s", "s"])
    #expect(!queue.isRecommended(queue.items[2].id))
    #expect(queue.isRecommended(queue.items[3].id))
    let upcoming = Array(queue.items.dropFirst(queue.index + 1))
    let layout = UpNextLayout(upcoming, keepPlaying: true) { queue.isRecommended($0.id) }
    #expect(layout.chosen.map(\.track.id) == ["b", "s"])
    #expect(layout.similar.count == 1)
    // A new queue forgets them.
    queue.replace([a])
    #expect(queue.recommended.isEmpty)
  }
}
