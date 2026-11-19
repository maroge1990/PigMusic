import PigDomain
import Testing

private struct ReadFailed: Error {}

/// Codex confirmation on batch D: an artist's songs are read album by
/// album; one album that can't be read fails the whole read, so a menu's
/// Play never plays part of an artist as if it were all of it.
struct CollectionReadTests {
  private func songs(_ album: String) -> [Track] {
    [Track(id: album + "-1", title: "One", artist: "Artist", album: album)]
  }

  @Test func everyAlbumReadGivesEverySongInOrder() async {
    let read = await CollectionRead.gather(["a", "b"]) { songs($0) }
    #expect(read?.map(\.id) == ["a-1", "b-1"])
  }

  @Test func oneAlbumThatCantBeReadFailsTheWholeRead() async {
    let read = await CollectionRead.gather(["a", "b", "c"]) { album in
      if album == "b" { throw ReadFailed() }
      return songs(album)
    }
    #expect(read == nil)
    #expect(CollectionPlay.decide(read, name: "Artist") == .notice("Couldn't load Artist. Try again."))
  }
}
