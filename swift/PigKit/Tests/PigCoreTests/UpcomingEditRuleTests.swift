import Foundation
import PigDomain
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

  @Test func everyIdAnEntryHasGoneByNamesIt() {
    var identity = OccurrenceIdentity()
    let first = UUID()
    let second = UUID()
    let third = UUID()
    identity.record(second, as: first)
    // A third listening, recorded against the second's id.
    identity.record(third, as: second)
    #expect(identity.root(third) == first && identity.root(second) == first)
    #expect(identity.root(first) == first)
    let track = Track(id: "a", title: "a", artist: "Fixture")
    let watch = PlaybackOccurrence(track: track, id: first)
    let phone = PlaybackOccurrence(track: track, id: third)
    #expect(identity.key(watch) == identity.key(phone))
    // A fresh copy of the song is the same entry but another song to fetch.
    var fresh = track
    fresh.duration = 2
    #expect(identity.key(PlaybackOccurrence(track: fresh, id: first)) != identity.key(watch))
    var other = OccurrenceIdentity()
    other.merge(identity)
    #expect(other.root(third) == first)
    // Each side knows one step: a stream resumed on the second listening
    // makes a fourth from it; the session knows the second's first id.
    var stream = OccurrenceIdentity()
    let fourth = UUID()
    stream.record(fourth, as: second)
    stream.merge(identity)
    #expect(stream.root(fourth) == first)
  }

  private func late(
    _ edited: String, anchor: Int = 0, heard: Int?, played: Set<Character> = [],
    repeatAll: Bool = false
  ) -> [Int]? {
    LateEditRestart.point(
      edited: Array(edited), anchor: anchor, heard: heard, played: played, repeatAll: repeatAll
    ).map { [$0.index, $0.keepsPosition ? 1 : 0] }
  }
  @Test func aLateEditRestartsWhereNothingQueuedIsSkipped() {
    // The song shown is still heard, or the next one: it carries on.
    #expect(late("abc", heard: 0) == [0, 1])
    #expect(late("abc", heard: 1) == [1, 1])
    // Play next put "x" before the heard "b": "x" first, from its start.
    #expect(late("axbc", heard: 2) == [1, 0])
    // "b" (heard) removed: the next song queued from its start, even one
    // added after it.
    #expect(late("ac", heard: nil) == [1, 0])
    #expect(late("ad", heard: nil) == [1, 0])
    // Nothing left: the queue ends; under Repeat All it wraps.
    #expect(late("a", heard: nil) == nil)
    #expect(late("a", heard: nil, repeatAll: true) == [0, 0])
    // Songs heard on the way to the heard one are not played again.
    #expect(late("abcd", heard: 2, played: ["b"]) == [2, 1])
    #expect(late("abd", heard: nil, played: ["b"]) == [2, 0])
  }
}
