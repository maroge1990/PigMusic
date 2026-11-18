import Foundation
import PigDomain
import PigPlayback
import Testing

/// D-79, D-118: removing a song from Up next takes it out of the queue at
/// once, so it can never become current while its Undo waits; Undo puts it
/// back at its old place while that is still in Up next, else at the front.
struct UpNextRemovalTests {
  private func make(_ titles: [String], at index: Int = 0) -> PlaybackQueue {
    var queue = PlaybackQueue()
    queue.replace(titles.map { Track(id: $0, title: $0, artist: "Artist") }, startingAt: index)
    return queue
  }
  private func titles(_ queue: PlaybackQueue) -> [String] { queue.items.map(\.track.title) }

  @Test func aRemovedSongLeavesAtOnceAndCanNeverBecomeCurrent() throws {
    var queue = make(["a", "b", "c"])
    let found = queue.takeUpcoming(queue.items[1].id)
    let taken = try #require(found)
    #expect(taken.position == 1)
    #expect(titles(queue) == ["a", "c"])
    queue.next()
    #expect(queue.current?.track.title == "c")
  }

  @Test func undoPutsItBackWhereItWas() throws {
    var queue = make(["a", "b", "c", "d"])
    let found = queue.takeUpcoming(queue.items[2].id)
    let taken = try #require(found)
    queue.putBack(taken)
    #expect(titles(queue) == ["a", "b", "c", "d"])
    #expect(queue.items[2].id == taken.item.id)
    // Never twice.
    queue.putBack(taken)
    #expect(titles(queue) == ["a", "b", "c", "d"])
  }

  /// Playback moved past its old place: it returns to the front of Up next.
  @Test func undoAfterPlaybackMovedOnPutsItFirstInUpNext() throws {
    var queue = make(["a", "b", "c", "d"])
    let found = queue.takeUpcoming(queue.items[1].id)
    let taken = try #require(found)
    queue.next()
    queue.next()  // now "d"; "b"'s old place (1) is behind it
    #expect(queue.current?.track.title == "d")
    queue.putBack(taken)
    #expect(titles(queue) == ["a", "c", "d", "b"])
    #expect(queue.current?.track.title == "d")
  }

  /// Codex round 1 on batch D: Undo never puts a song into another queue.
  /// Playing something else replaces the queue: Undo does nothing.
  @Test func undoAfterTheQueueWasReplacedDoesNothing() throws {
    var queue = make(["a", "b", "c"])
    let found = queue.takeUpcoming(queue.items[1].id)
    let taken = try #require(found)
    queue.replace(["x", "y"].map { Track(id: $0, title: $0, artist: "Artist") })
    #expect(!queue.holds(taken))
    let back = queue.putBack(taken)
    #expect(!back)
    #expect(titles(queue) == ["x", "y"])
  }

  /// Stop empties the queue: Undo does nothing, and nothing becomes current.
  @Test func undoAfterStopDoesNothing() throws {
    var queue = make(["a", "b", "c"])
    let found = queue.takeUpcoming(queue.items[1].id)
    let taken = try #require(found)
    queue.replace([])
    let back = queue.putBack(taken)
    #expect(!back)
    #expect(queue.items.isEmpty && queue.current == nil)
  }

  /// Moving on and reordering keep the same queue: Undo still works.
  @Test func undoSurvivesAdvancingAndReordering() throws {
    var queue = make(["a", "b", "c", "d", "e"])
    let found = queue.takeUpcoming(queue.items[3].id)
    let taken = try #require(found)
    queue.next()
    queue.moveUpcoming(queue.items[3].id, by: -1)
    queue.shuffleUpcoming()
    #expect(queue.holds(taken))
    let back = queue.putBack(taken)
    #expect(back)
    #expect(queue.items.contains { $0.id == taken.item.id })
  }

  /// A queue read back from a checkpoint is a new queue.
  @Test func aRestoredQueueIsANewQueue() throws {
    var queue = make(["a", "b"])
    let found = queue.takeUpcoming(queue.items[1].id)
    let taken = try #require(found)
    let saved = try JSONEncoder().encode(queue)
    var restored = try JSONDecoder().decode(PlaybackQueue.self, from: saved)
    let back = restored.putBack(taken)
    #expect(!back)
  }

  @Test func thePlayingSongCantBeTaken() {
    var queue = make(["a", "b"])
    let taken = queue.takeUpcoming(queue.items[0].id)
    #expect(taken == nil)
    #expect(titles(queue) == ["a", "b"])
  }

  /// UndoQueue runs `undone` on Undo, and never once the change is sent.
  @MainActor @Test func undoRunsItsPutBackOnlyWhenUndone() async {
    let undo = UndoQueue(wait: .seconds(60), linger: .zero)
    let putBack = Undone()
    undo.schedule("x", label: "Removed X", undone: { putBack.ids.append("x") }) { true }
    undo.schedule("y", label: "Removed Y", undone: { putBack.ids.append("y") }) { true }
    undo.undo("x")
    await undo.sendAllAndWait()
    undo.undo("y")
    #expect(putBack.ids == ["x"])
  }

  /// The queue it came from ended: the Undo leaves without putting it back.
  @MainActor @Test func anUndoCanBeDroppedWithoutUndoing() async {
    let undo = UndoQueue(wait: .seconds(60), linger: .zero)
    let putBack = Undone()
    undo.schedule("x", label: "Removed X", undone: { putBack.ids.append("x") }) { true }
    undo.sendNow("x")
    #expect(undo.items.isEmpty)
    undo.undo("x")
    #expect(putBack.ids.isEmpty)
  }
}

@MainActor private final class Undone {
  var ids: [String] = []
}
