import PigDomain
import Testing

@MainActor private final class Account {
  var current = true
  var notices: [String] = []
}

/// Codex confirmation on batch D: a collection menu's action whose read
/// finishes after the account changed does nothing at all, so one account's
/// songs never reach the next account's queue, and no notice shows.
@MainActor struct CollectionAccountTests {
  private let song = Track(id: "a-1", title: "One", artist: "Artist")

  @Test func signingOutDuringTheReadLeavesTheNextAccountAlone() async {
    let account = Account()
    let outcome = await CollectionPlay.read(name: "Artist", isCurrent: { account.current }) {
      // Signed out while the albums were being listed.
      account.current = false
      return [song]
    }
    #expect(outcome == .nothing)
    // A failed read for the old account says nothing either.
    account.current = true
    let failed = await CollectionPlay.read(name: "Artist", isCurrent: { account.current }) {
      account.current = false
      return nil
    }
    #expect(failed == .nothing)
  }

  @Test func theSameAccountPlaysWhatWasRead() async {
    let account = Account()
    let outcome = await CollectionPlay.read(name: "Artist", isCurrent: { account.current }) {
      [song]
    }
    #expect(outcome == .play([song]))
    let ended = Account()
    ended.current = false
    let never = await CollectionPlay.read(name: "Artist", isCurrent: { ended.current }) {
      Issue.record("read for an account that already ended")
      return [song]
    }
    #expect(never == .nothing)
  }
}
