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
}
