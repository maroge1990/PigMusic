import Foundation
import PigDomain
import Testing

// #191: the iPhone reports how long a start took to be heard, as the web does.

@Test func aStartIsTimedFromTheTapToTheSoundAndReportedOnce() {
  var clock = StartClock()
  let tap = ContinuousClock.now
  clock.begin("skip", at: tap)
  let song = Track(id: "mix:e1:3", title: "Song", artist: "Artist")
  #expect(
    clock.heard(song, at: tap + .milliseconds(1240))
      == FirstSound(ms: 1240, source: "mix", action: "skip"))
  #expect(clock.heard(song, at: tap + .seconds(3)) == nil)
}

@Test func aNewerStartReplacesAnUnheardOneAndAPauseDropsIt() {
  var clock = StartClock()
  let tap = ContinuousClock.now
  clock.begin("play", at: tap)
  clock.begin("previous", at: tap + .seconds(1))
  #expect(clock.heard(nil, at: tap + .seconds(2))?.action == "previous")
  #expect(clock.heard(nil, at: tap + .seconds(2))?.ms == nil)
  clock.begin("play", at: tap)
  clock.cancel()  // Paused before the sound: the person's wait.
  #expect(!clock.waiting && clock.heard(nil, at: tap + .seconds(9)) == nil)
}

@Test func eachSongCountsUnderTheServersKindOfSource() {
  func song(_ id: String, _ state: String?) -> Track {
    var track = Track(id: id, title: "Song", artist: "Artist")
    track.serverState = state
    return track
  }
  #expect(FirstSound.source(of: song("a", nil)) == "library")
  #expect(FirstSound.source(of: song("a", "in_library")) == "library")
  #expect(FirstSound.source(of: song("mix:e1:1", "not_in_library")) == "mix")
  #expect(FirstSound.source(of: song("a", "not_in_library")) == "remote")
  #expect(FirstSound.source(of: song("a", "requested")) == "remote")
  #expect(FirstSound.source(of: song("a", "household_available")) == "other")
  #expect(FirstSound.source(of: nil) == "other")
}
