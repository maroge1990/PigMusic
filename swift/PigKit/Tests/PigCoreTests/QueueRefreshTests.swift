import Foundation
import PigDomain
import PigPlayback
import Testing

private func song(_ id: Int, revision: String = "a") -> Track {
  var track = Track(id: String(id), title: "Song \(id)", artist: "A")
  track.sourceRevision = revision
  return track
}

/// Codex round 2 on batch E: Songs queues the whole library, so a changed
/// download far down it must not stop the song playing. It takes its fresh
/// copy where it waits; the song playing and its place are untouched.
@Test func aChangedSongLaterInTheQueueLeavesTheCurrentSongPlaying() {
  var queue = PlaybackQueue()
  queue.replace((1...200).map { song($0) })
  let playing = queue.current
  let waiting = queue.items[149].id
  let result = queue.refreshSongs(["150"], fresh: ["150": song(150, revision: "b")])
  #expect(result == .init(current: .same, others: true, shifted: false))
  #expect(queue.current == playing)
  #expect(queue.index == 0)
  // When it is reached, it plays its fresh copy, as the same occurrence.
  #expect(queue.items[149].id == waiting)
  #expect(queue.items[149].track.sourceRevision == "b")
  #expect(queue.items.count == 200)
}

/// A removed song leaves the queue; one before the current song moves its
/// place, which the player is told.
@Test func aRemovedSongLeavesTheQueueAndTheCurrentSongKeepsPlaying() {
  var queue = PlaybackQueue()
  queue.replace((1...5).map { song($0) }, startingAt: 2)
  let playing = queue.current
  let result = queue.refreshSongs(["1", "5"], fresh: [:])
  #expect(result == .init(current: .same, others: true, shifted: true))
  #expect(queue.current == playing)
  #expect(queue.items.map(\.track.id) == ["2", "3", "4"])
  // Nothing in the queue changed: nothing happens.
  #expect(queue.refreshSongs(["99"], fresh: [:]) == .init())
}

/// Holds the lookup until the test lets it answer.
private actor Gate {
  private var waiting: CheckedContinuation<Void, Never>?
  private var open = false
  func wait() async {
    if open { return }
    await withCheckedContinuation { waiting = $0 }
  }
  func release() {
    open = true
    waiting?.resume()
    waiting = nil
  }
}

/// Codex confirmation on batch E: the queue is replaced while the fresh
/// copies are read. The new queue's current song C changed too; it was
/// looked up with the rest, so it takes its fresh copy instead of being
/// taken for removed (which stopped playback).
@Test func aQueueReplacedDuringTheLookupKeepsItsChangedCurrentSong() async throws {
  let queue = QueueBox()
  await queue.replace([song(1), song(2)])
  let gate = Gate()
  let library = [song(2, revision: "b"), song(3, revision: "b")]
  async let lookup = PlaybackQueue.freshCopies(of: ["2", "3"]) { ids in
    await gate.wait()
    return library.filter { ids.contains($0.id) }
  }
  // Something else plays while the lookup waits: C (song 3) first.
  await queue.replace([song(3), song(4)])
  await gate.release()
  let fresh = try await lookup
  let result = await queue.refresh(["2", "3"], fresh: fresh)
  #expect(result.current == .fresh)
  #expect(await queue.revision == "b")
}

/// A failed lookup changes nothing: the error reaches the caller.
@Test func aFailedLookupChangesNothing() async {
  struct Unreadable: Error {}
  await #expect(throws: Unreadable.self) {
    _ = try await PlaybackQueue.freshCopies(of: ["1"]) { _ in throw Unreadable() }
  }
}

@MainActor private final class QueueBox {
  var queue = PlaybackQueue()
  func replace(_ tracks: [Track]) { queue.replace(tracks) }
  func refresh(_ changed: Set<String>, fresh: [String: Track]) -> PlaybackQueue.SongRefresh {
    queue.refreshSongs(changed, fresh: fresh)
  }
  var revision: String? { queue.current?.track.sourceRevision }
}

/// Only the song playing changing itself is reported, for the player to
/// restart it where it was.
@Test func theCurrentSongChangingItselfIsReported() {
  var queue = PlaybackQueue()
  queue.replace((1...3).map { song($0) })
  let id = queue.current?.id
  #expect(queue.refreshSongs(["1"], fresh: ["1": song(1, revision: "b")]).current == .fresh)
  #expect(queue.current?.id == id)
  #expect(queue.current?.track.sourceRevision == "b")
  #expect(queue.refreshSongs(["1"], fresh: [:]).current == .removed)
  #expect(queue.current?.id == id)
}
