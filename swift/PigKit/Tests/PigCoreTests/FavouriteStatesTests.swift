import PigDomain
import Testing

@Test func aReadStartedBeforeAToggleNeverOverwritesIt() {
  var states = FavouriteStates()
  let first = states.answer("song", false, since: states.revision("song"))
  #expect(first)
  // Another read starts, then the person stars the song while it waits.
  let late = states.revision("song")
  states.invalidate("song")
  states.change("song", true)
  let stale = states.answer("song", false, since: late)
  #expect(!stale)
  #expect(states["song"] == true)
  // A read started after the change is believed.
  let fresh = states.answer("song", true, since: states.revision("song"))
  #expect(fresh)
  #expect(states["other"] == nil)
  states.clear()
  #expect(states["song"] == nil)
}

@Test func aReadStartedBeforeSignOutNeverCountsAfterIt() {
  var states = FavouriteStates()
  // A read starts for a song this account never touched, then sign-out.
  let before = states.revision("song")
  states.clear()
  let stale = states.answer("song", true, since: before)
  #expect(!stale)
  #expect(states["song"] == nil)
}
