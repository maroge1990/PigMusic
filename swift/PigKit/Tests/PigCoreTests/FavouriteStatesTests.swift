import Foundation
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

private actor ReadLatch {
  var open = false
  var waiting = false
  func wait() async {
    waiting = true
    while !open { await Task.yield() }
  }
  func release() { open = true }
}

/// Codex on #212: a recovery read waiting on one song must not read, or
/// overwrite, another song resolved and tapped meanwhile; and only one
/// recovery runs at a time.
@MainActor @Test func recoverySkipsASongResolvedAndTappedMeanwhile() async {
  var states = FavouriteStates()
  let a = Track(id: "a", title: "A", artist: "X")
  let b = Track(id: "b", title: "B", artist: "X")
  states.wanted(a)
  states.wanted(b)
  var queued: Set<String> = []
  var reads: [String] = []
  let latch = ReadLatch()
  let recovery = FavouriteRecovery()
  let run = Task { @MainActor in
    await recovery.run(
      states: { states },
      answer: { states.answer($0, $1, since: $2) },
      editWaiting: { queued.contains($0) },
      read: { track in
        reads.append(track.id)
        if track.id == "a" { await latch.wait() }
        return false
      })
  }
  while !(await latch.waiting) { await Task.yield() }
  // Meanwhile B is read elsewhere, then tapped; its edit waits.
  states.answer("b", false, since: states.revision("b"))
  _ = states.tap("b")
  queued.insert("b")
  // A second recovery doesn't start while the first runs.
  let second = await recovery.run(
    states: { states }, answer: { states.answer($0, $1, since: $2) },
    editWaiting: { _ in false }, read: { _ in true })
  #expect(second.isEmpty)
  await latch.release()
  let answered = await run.value
  #expect(reads == ["a"])
  #expect(answered.map(\.track.id) == ["a"])
  #expect(states["a"] == false && states["b"] == true)
}

/// #227: one star design. The song sheet showed a struck-through star
/// (`star.slash`) on "Remove from favourites" while the player's star was
/// filled: a favourite is a filled star everywhere, an outline otherwise.
@Test func aFavouriteIsAFilledStarEverywhere() {
  #expect(FavouriteStates.symbol(favourite: true) == "star.fill")
  #expect(FavouriteStates.symbol(favourite: false) == "star")
  #expect(FavouriteStates.action(favourite: true) == "Remove from favourites")
  #expect(FavouriteStates.action(favourite: false) == "Add to favourites")
}

/// #284: the library this iPhone holds answers a song's favourite state, so
/// the song menu never offers to load it: listed in the Favourites playlist
/// (by the server's same-song test) or not; unknown only while the library
/// holds no Favourites playlist with its songs.
@Test func theLibrarysFavouritesPlaylistAnswersTheState() throws {
  let json = #"""
    [{"id":"mine","name":"Road trip","revision":1,"entries":[]},
     {"id":"pig-favourites:me","name":"Favourites","revision":3,"favourites":true,
      "entries":[{"entry_id":"f:0","title":"Open Road","artist":"The Orchard",
        "album":"Sunrise","position":0,"duration":181,"saved_id":"two"}]}]
    """#
  let playlists = try LibraryCodec.decoder().decode([Playlist].self, from: Data(json.utf8))
  var two = Track(id: "two", title: "Open Road", artist: "The Orchard", album: "Sunrise")
  two.duration = 183
  two.recordingKind = ["studio"]
  #expect(FavouriteStates.listed(two, in: playlists) == true)
  let one = Track(id: "one", title: "First Light", artist: "The Orchard", album: "Sunrise")
  #expect(FavouriteStates.listed(one, in: playlists) == false)
  // The same song on another album isn't the favourite the server checks.
  let other = Track(id: "two-b", title: "Open Road", artist: "The Orchard", album: "Elsewhere")
  #expect(FavouriteStates.listed(other, in: playlists) == false)
  // No Favourites playlist, or one without its songs: not known here.
  #expect(FavouriteStates.listed(two, in: [playlists[0]]) == nil)
  var bare = playlists[1]
  bare.entries = nil
  #expect(FavouriteStates.listed(two, in: [bare]) == nil)
}

/// #284: plain "Favourite" on a song whose state is unknown makes it a
/// favourite at once; a refused save makes it unknown again.
@Test func favouriteOnAnUnknownSongMakesItOneAndARefusalUndoesIt() throws {
  var states = FavouriteStates()
  #expect(states.tap("song") == nil)  // The player's star still needs a known state.
  let tapped = states.tap("song", orFavourite: true)
  let tap = try #require(tapped)
  #expect(tap.value == true && tap.previous == nil)
  #expect(states["song"] == true)
  states.refused(tap)
  #expect(states["song"] == nil)
  // A known state toggles as before, whatever `orFavourite` says.
  states.answer("song", true, since: states.revision("song"))
  #expect(states.tap("song", orFavourite: true)?.value == false)
}
