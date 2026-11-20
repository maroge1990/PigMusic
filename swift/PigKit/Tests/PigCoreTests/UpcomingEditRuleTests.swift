import Testing

@testable import PigPlayback

/// The pure rule behind a queue edit while a song plays (#279): no audio,
/// so it also runs where AVFoundation does not.
@Suite struct UpcomingEditRuleTests {
  private func decide(
    version: Int = 1, last: Int? = nil, old: [String], new: [String], current: Int = 0,
    oldMode: PlaybackRepeat = .off, newMode: PlaybackRepeat = .off, prepared: Int? = nil,
    fetching: Int? = 1, intact: Bool = true, between: Bool = false
  ) -> UpcomingEditRule.Decision {
    UpcomingEditRule.decide(
      version: version, lastVersion: last, old: old, new: new, current: current,
      oldMode: oldMode, newMode: newMode, prepared: prepared, fetching: fetching,
      fetchIntact: intact, betweenSongs: between)
  }

  @Test func followingMatchesTheStreamsOrder() {
    #expect(UpcomingEditRule.following(0, count: 3, mode: .off) == 1)
    #expect(UpcomingEditRule.following(2, count: 3, mode: .off) == nil)
    #expect(UpcomingEditRule.following(2, count: 3, mode: .all) == 0)
    #expect(UpcomingEditRule.following(1, count: 3, mode: .one) == 1)
    #expect(UpcomingEditRule.following(0, count: 1, mode: .all) == 0)
  }

  @Test func removingTheNextSongFetchesAgain() {
    #expect(decide(old: ["a", "b", "c"], new: ["a", "c"]) == .refetch)
  }

  @Test func anEditFurtherDownKeepsTheFetch() {
    #expect(decide(old: ["a", "b", "c"], new: ["a", "b", "d", "c"]) == .keep)
    #expect(decide(old: ["a", "b", "c"], new: ["a", "b"]) == .keep)
  }

  @Test func playNextAndMovingASongUpFetchAgain() {
    #expect(decide(old: ["a", "b", "c"], new: ["a", "x", "b", "c"]) == .refetch)
    #expect(decide(old: ["a", "b", "c", "d"], new: ["a", "d", "b", "c"]) == .refetch)
  }

  @Test func songsAddedAfterTheLastOneStartAFetch() {
    #expect(decide(old: ["a"], new: ["a", "s1", "s2"], fetching: nil) == .refetch)
    #expect(decide(old: ["a"], new: ["a"], fetching: nil) == .keep)
  }

  @Test func removingTheOnlyFollowingSongDropsTheFetch() {
    #expect(decide(old: ["a", "b"], new: ["a"]) == .refetch)
  }

  @Test func aChangedSongUpToTheOneDecodingIsTooLate() {
    // The stream decodes "b" (ahead of the song heard): "b" is already audio.
    #expect(decide(old: ["a", "b", "c"], new: ["a", "c"], current: 1, fetching: 2) == .tooLate)
    #expect(decide(old: ["a", "b", "c"], new: ["z", "b", "c"], current: 1, fetching: 2) == .tooLate)
    #expect(decide(old: ["a", "b"], new: [], current: 0) == .tooLate)
    #expect(decide(old: ["a", "b"], new: ["a"], current: 1, fetching: nil) == .tooLate)
  }

  @Test func aPassedOverOrFailedFetchIsChosenAgain() {
    #expect(decide(old: ["a", "b", "c"], new: ["a", "b", "c", "d"], intact: false) == .refetch)
  }

  @Test func repeatOneChangesTheNextSongAndItsTransition() {
    #expect(decide(old: ["a", "b"], new: ["a", "b"], newMode: .one) == .refetch)
    #expect(
      decide(old: ["a", "b"], new: ["a", "b"], oldMode: .one, newMode: .off, fetching: 0)
        == .refetch)
    // The same song follows, but Repeat One plays it gapless: fetched again.
    #expect(decide(old: ["a"], new: ["a"], oldMode: .all, newMode: .one, fetching: 0) == .refetch)
    #expect(decide(old: ["a", "b"], new: ["a", "b"], oldMode: .off, newMode: .all) == .keep)
  }

  @Test func aPreparedNextSongIsKeptOnlyWhereItStands() {
    #expect(
      decide(old: ["a", "b", "c"], new: ["a", "b", "c", "d"], prepared: 1, fetching: 1) == .keep)
    #expect(decide(old: ["a", "b", "c"], new: ["a", "c"], prepared: 1, fetching: 1) == .tooLate)
    #expect(
      decide(old: ["a", "b", "c"], new: ["a", "x", "b"], prepared: 1, fetching: 1) == .tooLate)
    #expect(
      decide(old: ["a", "b"], new: ["a", "b"], newMode: .one, prepared: 1, fetching: 1) == .tooLate)
    // Wrapping round to the first song, which is still first.
    #expect(
      decide(
        old: ["a", "b"], new: ["a", "b"], current: 1, oldMode: .all, newMode: .all,
        prepared: 0, fetching: 0) == .keep)
    #expect(
      decide(
        old: ["a", "b"], new: ["a", "b", "c"], current: 1, oldMode: .all, newMode: .all,
        prepared: 0, fetching: 0) == .tooLate)
  }

  @Test func betweenSongsTheNewListIsTakenAsItIs() {
    #expect(
      decide(old: ["a", "b", "c"], new: ["a", "b", "x"], current: 1, fetching: 1, between: true)
        == .keep)
    #expect(
      decide(old: ["a", "b", "c"], new: ["a", "x", "c"], current: 1, fetching: 1, between: true)
        == .tooLate)
  }

  @Test func versionsApplyInOrderAndALateOlderEditIsIgnored() {
    #expect(decide(version: 3, last: 5, old: ["a", "b"], new: ["a"]) == .stale)
    #expect(decide(version: 5, last: 5, old: ["a", "b"], new: ["a"]) == .stale)
    #expect(decide(version: 6, last: 5, old: ["a", "b"], new: ["a"]) == .refetch)
    // A stale edit is ignored even when it would be too late.
    #expect(decide(version: 1, last: 2, old: ["a", "b"], new: ["z"]) == .stale)
  }
}
