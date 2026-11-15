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

// #212: a tap shows the new state before the server answers, and only a
// refused save puts the old one back.
@Test func aTapShowsTheNewStateAtOnceAndARefusalRevertsIt() {
  var states = FavouriteStates()
  let unknown = states.tap("song")
  #expect(unknown == nil)  // Unknown: nothing to flip yet.
  states.answer("song", false, since: states.revision("song"))
  #expect(states.canTap("song"))
  let read = states.revision("song")
  let tap = states.tap("song")
  #expect(tap?.value == true)
  #expect(states["song"] == true)  // Before any save or server answer.
  // A read that started before the tap doesn't undo it.
  let stale = states.answer("song", false, since: read)
  #expect(!stale)
  #expect(states["song"] == true)
  states.refused(tap!)
  #expect(states["song"] == false)
}

@Test func aRefusalAfterANewerTapKeepsTheNewerState() {
  var states = FavouriteStates()
  states.answer("song", false, since: states.revision("song"))
  let first = states.tap("song")!
  let second = states.tap("song")!
  #expect(second.value == false)
  states.refused(first)
  #expect(states["song"] == false)  // The second tap's state, not reverted.
  states.refused(second)
  #expect(states["song"] == true)  // Back to what the first tap showed.
}

/// The star depends only on whether its state is known: a library sync,
/// which made the star grey before (#212), is no input.
@Test func theStarStaysTappableWhateverElseIsRunning() {
  var states = FavouriteStates()
  #expect(!states.canTap("song"))
  states.answer("song", true, since: states.revision("song"))
  #expect(states.canTap("song"))
  states.invalidate("song")  // A save under way.
  #expect(states.canTap("song"))
}

/// Codex on #212: a song whose state is unknown (refused and its server
/// read failed, or never cached and opened offline) stays listed for a retry
/// until a read answers, so a later successful sync makes the star known.
@Test func anUnknownStateIsRetriedUntilAReadAnswers() {
  var states = FavouriteStates()
  let song = Track(id: "song", title: "Song", artist: "A")
  states.answer(song.id, false, since: states.revision(song.id))
  states.refusal(RefusedFavourite(trackId: song.id, value: nil, track: song))
  #expect(states[song.id] == nil)
  // The recovery read fails: nothing answers, and the song waits for a retry.
  #expect(states.unresolved == [song])
  // A successful refresh retries it; the server's answer is shown.
  let retry = states.revision(song.id)
  states.answer(song.id, false, since: retry)
  #expect(states[song.id] == false)
  #expect(states.unresolved.isEmpty)
  // A song first opened offline, never cached, waits the same way.
  let other = Track(id: "other", title: "Other", artist: "A")
  states.wanted(other)
  #expect(states.unresolved == [other])
  states.wanted(song)  // Known already: nothing to retry.
  #expect(states.unresolved == [other])
  states.clear()
  #expect(states.unresolved.isEmpty)
}
