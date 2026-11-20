import Foundation
import PigDomain
import Testing

/// Codex round 1 on batch D: a playlist's page and both collection-menu entry
/// points leave out a song removed here, waiting for its Undo or already
/// applied, so Play, Download or Add to playlist never bring it back.
struct PlaylistShownTests {
  private func playlist(_ titles: [String], revision: Int = 4) throws -> Playlist {
    let entries = titles.enumerated().map { index, title in
      #"{"entry_id":"e\#(index)","title":"\#(title)","artist":"Artist","position":\#(index)}"#
    }.joined(separator: ",")
    return try LibraryCodec.decoder().decode(
      Playlist.self,
      from: Data(
        #"{"id":"p","name":"Road trip","revision":\#(revision),"entries":[\#(entries)]}"#.utf8))
  }

  @Test func aSongWaitingForItsUndoIsLeftOutUntilUndone() throws {
    let list = try playlist(["a", "b", "c"])
    var waiting: Set<String> = [PlaylistRemoval.undoKey(playlist: "p", revision: 4, position: 1)]
    let shown = PlaylistRemoval.shown(list, ledger: PlaylistRemovalLedger()) {
      waiting.contains($0)
    }
    #expect(shown.map(\.entry.title) == ["a", "c"])
    #expect(shown.map(\.position) == [0, 2])
    waiting = []
    let undone = PlaylistRemoval.shown(list, ledger: PlaylistRemovalLedger()) {
      waiting.contains($0)
    }
    #expect(undone.map(\.entry.title) == ["a", "b", "c"])
  }

  /// A cached copy of the same version still lists a song the server removed.
  @Test func aSongAlreadyRemovedFromThatVersionIsLeftOut() throws {
    var ledger = PlaylistRemovalLedger()
    ledger.record(playlist: "p", position: 0, revision: 4, applied: true)
    let cached = try playlist(["a", "b"])
    #expect(PlaylistRemoval.shown(cached, ledger: ledger) { _ in false }.map(\.entry.title) == ["b"])
    // The server's newer version is shown as it is.
    let newer = try playlist(["b"], revision: 5)
    #expect(PlaylistRemoval.shown(newer, ledger: ledger) { _ in false }.map(\.entry.title) == ["b"])
  }

  private func removal(
    _ position: Int, revision: Int = 4, playlist: String = "p", state: String = "queued"
  ) -> PendingMutation {
    var item = PendingMutation(
      mutation: OfflineMutation(
        action: "remove", playlistId: playlist, revision: revision, position: position))
    item.state = state
    return item
  }

  /// #277: offline, a removal waits in the edit outbox after its Undo ran
  /// out; Play, Download and Add to playlist must not bring the song back.
  @Test func aRemovalWaitingOfflineIsLeftOut() throws {
    let list = try playlist(["a", "b", "c"])
    let shown = PlaylistRemoval.shown(
      list, ledger: PlaylistRemovalLedger(), pending: [removal(1)]
    ) { _ in false }
    #expect(shown.map(\.entry.title) == ["a", "c"])
    #expect(shown.map(\.position) == [0, 2])
  }

  /// Only a removal of this copy's song: another playlist's, another
  /// version's, and one the server refused for review leave the song shown.
  @Test func onlyAQueuedRemovalOfThisCopysSongIsLeftOut() throws {
    let list = try playlist(["a", "b", "c"])
    let others = [
      removal(1, playlist: "other"), removal(1, revision: 3), removal(1, state: "conflict"),
      removal(1, state: "rejected"),
    ]
    let shown = PlaylistRemoval.shown(list, ledger: PlaylistRemovalLedger(), pending: others) {
      _ in false
    }
    #expect(shown.map(\.entry.title) == ["a", "b", "c"])
  }

  /// A removal queued after one the server applied from the same version
  /// was sent shifted (position 2 of version 4 as position 1 of version 5):
  /// the page, still on version 4, leaves out "c", not "b".
  @Test func aQueuedRemovalShiftedByAnAppliedOneLeavesOutItsOwnSong() throws {
    var ledger = PlaylistRemovalLedger()
    ledger.record(playlist: "p", position: 0, revision: 4, applied: true)
    let list = try playlist(["a", "b", "c"])
    let shown = PlaylistRemoval.shown(list, ledger: ledger, pending: [removal(1, revision: 5)]) {
      _ in false
    }
    #expect(shown.map(\.entry.title) == ["b"])
    // The server's version 5 (without "a"), read again: the same song goes.
    let newer = try playlist(["b", "c"], revision: 5)
    #expect(
      PlaylistRemoval.shown(newer, ledger: ledger, pending: [removal(1, revision: 5)]) { _ in
        false
      }.map(\.entry.title) == ["b"])
  }
}
