import Foundation
import GRDB
import PigDomain
import PigStore
import PigSync
import Testing

@Test func indexedSearchPagesAllEntitiesAndTracksMutations() async throws {
  let store = try LibraryStore(path: ":memory:")
  let albums = try LibraryCodec.decoder().decode(
    [Album].self, from: Data(#"[{"id":"album","title":"Beyoncé album","artist":"Singer"}]"#.utf8))
  let artists = try LibraryCodec.decoder().decode(
    [Artist].self, from: Data(#"[{"id":"artist","name":"Beyoncé","album_ids":["album"]}]"#.utf8))
  let tracks = (0..<205).map {
    Track(id: String(format: "%03d", $0), title: "Beyoncé song", artist: "Singer")
  }
  try await store.replace(.init(revision: "one", tracks: tracks, albums: albums, artists: artists))
  var ids: [String] = []
  for offset in [0, 100, 200] {
    let page = try await store.searchPage(query: "Beyonce", offset: offset)
    #expect(page.revision == "one")
    #expect(page.nextOffset == (offset == 200 ? nil : offset + 100))
    ids += page.tracks.map(\.id)
    if offset == 0 {
      #expect(page.albums.map(\.id) == ["album"])
      #expect(page.artists.map(\.id) == ["artist"])
    }
  }
  #expect(ids.count == 205 && Set(ids).count == 205)
  #expect(try await store.albums(artistId: "artist").map(\.id) == ["album"])
  #expect(try await store.searchPage(query: "\" OR *").tracks.isEmpty)
  let delta = try LibraryCodec.decoder().decode(
    LibraryDelta.self,
    from: Data(
      #"{"revision":"two","upserts":{"tracks":[],"albums":[],"artists":[],"playlists":[]},"deletes":{"tracks":["000"],"albums":["album"],"artists":["artist"],"playlists":[]}}"#
        .utf8))
  try await store.apply(delta)
  let page = try await store.searchPage(query: "Beyonce")
  #expect(page.albums.isEmpty && page.artists.isEmpty)
  #expect(!page.tracks.contains { $0.id == "000" })
}

@Test func upgradedCacheBuildsEntityIndexesAndRenamedRowsReplaceOldTerms() async throws {
  let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
  try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
  defer { try? FileManager.default.removeItem(at: directory) }
  let path = directory.appendingPathComponent("library.sqlite").path
  let previous = try LibraryStore(path: path)
  let snapshot = try LibraryCodec.decoder().decode(
    LibrarySnapshot.self,
    from: Data(
      #"{"revision":"one","tracks":[],"albums":[{"id":"album","title":"Oldname","artist":"Artist"}],"artists":[{"id":"artist","name":"Oldname","album_ids":["album"]}],"playlists":[]}"#
        .utf8))
  try await previous.replace(snapshot)
  // Recreate a v2 on-disk cache so migration must index existing albums/artists.
  let queue = try DatabaseQueue(path: path)
  try await queue.write { db in
    try db.execute(sql: "DROP TABLE album_search")
    try db.execute(sql: "DROP TABLE artist_search")
    try db.execute(sql: "DELETE FROM grdb_migrations WHERE identifier='entity-search-v3'")
  }
  try queue.close()
  let upgraded = try LibraryStore(path: path)
  let existing = try await upgraded.searchPage(query: "Oldname")
  #expect(existing.albums.count == 1 && existing.artists.count == 1)
  let delta = try LibraryCodec.decoder().decode(
    LibraryDelta.self,
    from: Data(
      #"{"revision":"two","upserts":{"tracks":[],"albums":[{"id":"album","title":"Newname","artist":"Artist"}],"artists":[{"id":"artist","name":"Newname","album_ids":["album"]}],"playlists":[]},"deletes":{"tracks":[],"albums":[],"artists":[],"playlists":[]}}"#
        .utf8))
  try await upgraded.apply(delta)
  let old = try await upgraded.searchPage(query: "Oldname")
  #expect(old.albums.isEmpty && old.artists.isEmpty)
  let renamed = try await upgraded.searchPage(query: "Newname")
  #expect(renamed.albums.count == 1 && renamed.artists.count == 1)
}

@Test func cacheSyncedBeforeArtistPortraitsResyncsOnce() async throws {
  let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
  try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
  defer { try? FileManager.default.removeItem(at: directory) }
  let path = directory.appendingPathComponent("library.sqlite").path
  // An older app stored the artist without `cover_sha` and kept syncing.
  let previous = try LibraryStore(path: path)
  try await previous.replace(
    LibraryCodec.decoder().decode(
      LibrarySnapshot.self,
      from: Data(
        #"{"revision":"old","tracks":[],"albums":[],"artists":[{"id":"ar","name":"Artist","cover_album_id":"al"}],"playlists":[]}"#
          .utf8)))
  let queue = try DatabaseQueue(path: path)
  try await queue.write { db in
    try db.execute(sql: "DELETE FROM grdb_migrations WHERE identifier='artist-portraits-v9'")
  }
  try queue.close()
  let upgraded = try LibraryStore(path: path)
  // No revision: the next sync downloads the library again, and the cached
  // artists stay until that replacement arrives.
  #expect(try await upgraded.revision() == nil)
  #expect(try await upgraded.artist(id: "ar")?.name == "Artist")
  try await upgraded.replace(
    LibraryCodec.decoder().decode(
      LibrarySnapshot.self,
      from: Data(
        #"{"revision":"new","tracks":[],"albums":[],"artists":[{"id":"ar","name":"Artist","cover_album_id":"al","cover_sha":"ppp"}],"playlists":[]}"#
          .utf8)))
  #expect(try await upgraded.artist(id: "ar")?.coverSha == "ppp")
  // Only once.
  #expect(try await LibraryStore(path: path).revision() == "new")
}
