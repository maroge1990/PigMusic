import Foundation
import PigDomain
import PigStore
import Testing

private func song(_ id: String) -> Track {
  try! LibraryCodec.decoder().decode(
    Track.self, from: Data(#"{"id":"\#(id)","title":"Song \#(id)","artist":"Artist"}"#.utf8))
}

@Test func recentPicksAreNewestFirstOnceEachAndCapped() throws {
  var recent = RecentSearches()
  recent.add(.song(song("a")))
  recent.add(.catalogueArtist(id: "mb", name: "U2"))
  recent.add(.song(song("a")))  // Picked again: moves to the top, not repeated.
  #expect(recent.picks.map(\.id) == ["song:a", "catalogue-artist:mb"])
  for index in 0..<30 { recent.add(.song(song("\(index)"))) }
  #expect(recent.picks.count == RecentSearches.limit)
  #expect(recent.picks.first?.id == "song:29")
  // Saved and read back the way the phone keeps them.
  let encoded = try JSONEncoder().encode(recent)
  #expect(try JSONDecoder().decode(RecentSearches.self, from: encoded) == recent)
  recent.clear()
  #expect(recent.picks.isEmpty)
}

@Test func aRecentSongIsReplayedAsTheLibraryHoldsItNow() async throws {
  let store = try LibraryStore(path: ":memory:")
  var saved = Track(id: "song", title: "Song", artist: "Artist")
  saved.sourceRevision = "A"
  var now = saved
  now.sourceRevision = "B"
  try await store.replace(.init(revision: "one", tracks: [now], albums: [], artists: []))
  guard case .song(let replayed) = await store.current(.song(saved)) else {
    Issue.record("not a song")
    return
  }
  #expect(replayed.sourceRevision == "B")
  // Music the library doesn't hold is replayed as it was saved.
  let household = Track(id: "household", title: "Other", artist: "Artist")
  #expect(await store.current(.song(household)) == .song(household))
}

@Test func aRecentAlbumIsKnownToBeInTheLibraryOrNot() async throws {
  let store = try LibraryStore(path: ":memory:")
  let albums = try LibraryCodec.decoder().decode(
    [Album].self, from: Data(#"[{"id":"mine","title":"Mine","artist":"Band"}]"#.utf8))
  try await store.replace(.init(revision: "one", tracks: [], albums: albums, artists: []))
  #expect(await store.holds(albumId: "mine"))
  #expect(!(await store.holds(albumId: "server-only")))
}
