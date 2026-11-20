import Foundation
import PigDomain
import Testing

/// #285: Add to playlist's New playlist makes the playlist and adds the
/// songs to it, also before the playlist has reached the server.
@Test func aNewPlaylistIsCreatedThenGetsItsSongsInOrder() throws {
  let songs = [
    Track(id: "one", title: "First Light", artist: "The Orchard"),
    Track(id: "two", title: "Open Road", artist: "The Orchard"),
  ]
  let edits = NewPlaylistEdits.make(name: "  Road trip ", songs: songs)
  #expect(edits.map(\.action) == ["create", "append", "append"])
  #expect(edits[0].name == "Road trip")
  #expect(edits.dropFirst().map { $0.track?.id } == ["one", "two"])
  // One playlist id, chosen here, as the server takes it (a UUID): the
  // songs go to the playlist being made, and wait with it in the outbox.
  let id = try #require(edits[0].playlistId)
  #expect(UUID(uuidString: id) != nil && id == id.lowercased())
  #expect(edits.allSatisfy { $0.playlistId == id })
  #expect(Set(edits.map(\.operationId)).count == 3)
  // Each New playlist is a playlist of its own.
  #expect(NewPlaylistEdits.make(name: "Road trip", songs: [])[0].playlistId != id)
}

/// Codex round 1 on batch G: the outbox's room is checked for the whole
/// group, not one edit at a time.
@Test func aGroupFitsTheOutboxOnlyWhole() {
  #expect(MutationOutboxRoom.fits(pending: 999, adding: 1))
  #expect(!MutationOutboxRoom.fits(pending: 999, adding: 2))
  #expect(MutationOutboxRoom.fits(pending: 0, adding: MutationOutboxRoom.limit))
  #expect(MutationOutboxRoom.fits(pending: 1000, adding: 0))
}

@Test func aNewPlaylistNeedsAName() {
  let song = Track(id: "a", title: "A", artist: "B")
  #expect(NewPlaylistEdits.make(name: "   ", songs: [song]).isEmpty)
}
