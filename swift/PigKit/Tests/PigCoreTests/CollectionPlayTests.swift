import PigDomain
import Testing

/// Codex round 2 on batch D: Play and Shuffle from a collection's menu never
/// stop what plays for nothing; a read that failed says so.
struct CollectionPlayTests {
  @Test func playingNothingLeavesTheQueueAlone() {
    let song = Track(id: "s", title: "Song", artist: "Artist")
    #expect(CollectionPlay.decide([song], name: "Road trip") == .play([song]))
    #expect(CollectionPlay.decide([], name: "Road trip") == .nothing)
    #expect(
      CollectionPlay.decide(nil, name: "Road trip") == .notice("Couldn't load Road trip. Try again."))
  }
}
