import Foundation
@testable import PigDomain
import PigStore
import Testing

struct LibrarySortingTests {
  @Test func sortingPagesAcrossTheWholeCacheAndKeepsAlbumSequence() async throws {
    let store = try LibraryStore(path: ":memory:")
    let tracks = (0..<250).map { index in
      var track = Track(
        id: String(index), title: String(format: "%03d", index), artist: index % 2 == 0 ? "A" : "B")
      track.albumId = "album"
      track.trackNumber = index + 1
      return track
    }
    try await store.replace(LibrarySnapshot(revision: "one", tracks: tracks))
    let first = try await store.tracks(limit: 100, sort: .artist)
    let next = try await store.tracks(offset: 100, limit: 100, sort: .artist)
    #expect(first.allSatisfy { $0.artist == "A" })
    #expect(next.prefix(25).allSatisfy { $0.artist == "A" })
    #expect(next.dropFirst(25).allSatisfy { $0.artist == "B" })
    #expect(Set((first + next).map(\.id)).count == 200)
    #expect(try await store.tracks(limit: 1, sort: .titleDescending).first?.id == "249")
    #expect(
      try await store.tracks(albumId: "album", sort: .titleDescending).map(\.id) == tracks.map(\.id)
    )
  }
  @Test func aLeadingTheIsIgnoredWhenSorting() async throws {
    #expect("The Beatles".sortingName == "Beatles")
    #expect("The".sortingName == "The")
    #expect("Theatre".sortingName == "Theatre")
    let store = try LibraryStore(path: ":memory:")
    let names = ["The Cure", "ABBA", "the Beatles", "Coldplay", "Theory", "2Pac", "10cc"]
    let tracks = names.enumerated().map { index, name in
      Track(id: String(index), title: name, artist: name)
    }
    try await store.replace(LibrarySnapshot(revision: "one", tracks: tracks))
    let expected = ["2Pac", "10cc", "ABBA", "the Beatles", "Coldplay", "The Cure", "Theory"]
    #expect(try await store.tracks(limit: 10, sort: .title).map(\.title) == expected)
    #expect(try await store.tracks(limit: 10, sort: .artist).map(\.artist) == expected)
    #expect(names.sorted { $0.sortingCompare($1) == .orderedAscending } == expected)
  }
}

struct SourceRevisionLookupTests {
  @Test func oneQueryMatchesPerTrackLookups() async throws {
    let store = try LibraryStore(path: ":memory:")
    let tracks = (0..<600).map { index in
      var track = Track(id: "t\(index)", title: "Song \(index)", artist: "A")
      track.sourceRevision = index % 3 == 0 ? nil : "rev\(index)"
      return track
    }
    try await store.replace(LibrarySnapshot(revision: "one", tracks: tracks))
    let ids = tracks.map(\.id) + ["missing", "also-missing"]
    let batched = try await store.sourceRevisions(ids: ids)
    var single: [String: String?] = [:]
    for id in ids {
      if let track = try await store.track(id: id) {
        single.updateValue(track.sourceRevision, forKey: id)
      }
    }
    #expect(batched == single)
    #expect(batched.count == 600)
    #expect(batched["t3"] == .some(nil))
    #expect(batched["t4"] == .some("rev4"))
    #expect(batched["missing"] == nil)
    #expect(try await store.sourceRevisions(ids: [String]()).isEmpty)
  }
}

struct ArtworkRevisionTests {
  private func delta(_ revision: String, upserts: String, deletedAlbums: String = "")
    throws -> LibraryDelta
  {
    try LibraryCodec.decoder().decode(
      LibraryDelta.self,
      from: Data(
        #"{"revision":"\#(revision)","upserts":{\#(upserts)},"deletes":{"tracks":[],"albums":[\#(deletedAlbums)],"artists":[],"playlists":[]}}"#
          .utf8))
  }
  @Test func largeMetadataDeltasCompareCoversInOnePass() async throws {
    let store = try LibraryStore(path: ":memory:")
    func tracks(_ title: String, last: String) -> String {
      (0..<500).map { index in
        #"{"id":"t\#(index)","title":"\#(title)","artist":"A","cover_sha":"\#(index == 499 ? last : "same")"}"#
      }.joined(separator: ",")
    }
    func apply(_ revision: String, _ list: String) async throws {
      try await store.apply(
        delta(revision, upserts: #""tracks":[\#(list)],"albums":[],"artists":[],"playlists":[]"#))
    }
    try await apply("one", tracks("Song", last: "same"))
    let first = try #require(try await store.artworkRevision())
    // 500 renamed songs, covers untouched: no redraw.
    try await apply("two", tracks("Renamed", last: "same"))
    #expect(try await store.artworkRevision() == first)
    // Only the last of them changes its cover: found all the same.
    try await apply("three", tracks("Renamed", last: "other"))
    #expect(try await store.artworkRevision() != first)
  }
  @Test func onlyCoverChangesMoveTheArtworkRevision() async throws {
    let store = try LibraryStore(path: ":memory:")
    #expect(try await store.artworkRevision() == nil)
    let album = #"{"id":"al","title":"Album","artist":"A","cover_sha":"aaa"}"#
    try await store.apply(
      delta("one", upserts: #""tracks":[],"albums":[\#(album)],"artists":[],"playlists":[]"#))
    let first = try #require(try await store.artworkRevision())
    // A title change and a new song without a cover leave covers alone.
    try await store.apply(
      delta(
        "two",
        upserts:
          #""tracks":[{"id":"t","title":"Song","artist":"A"}],"albums":[{"id":"al","title":"Renamed","artist":"A","cover_sha":"aaa"}],"artists":[],"playlists":[]"#
      ))
    #expect(try await store.revision() == "two")
    #expect(try await store.artworkRevision() == first)
    // A changed cover moves it.
    try await store.apply(
      delta(
        "three",
        upserts:
          #""tracks":[],"albums":[{"id":"al","title":"Renamed","artist":"A","cover_sha":"bbb"}],"artists":[],"playlists":[]"#
      ))
    let second = try #require(try await store.artworkRevision())
    #expect(second != first)
    // An artist's chosen cover album moves it; repeating it does not.
    let artist =
      #""tracks":[],"albums":[],"artists":[{"id":"ar","name":"A","cover_album_id":"al"}],"playlists":[]"#
    try await store.apply(delta("four", upserts: artist))
    let third = try #require(try await store.artworkRevision())
    #expect(third != second)
    try await store.apply(delta("five", upserts: artist))
    #expect(try await store.artworkRevision() == third)
    // So does a portrait arriving for the same artist.
    let portrait =
      #""tracks":[],"albums":[],"artists":[{"id":"ar","name":"A","cover_album_id":"al","cover_sha":"ppp"}],"playlists":[]"#
    try await store.apply(delta("five-b", upserts: portrait))
    #expect(try await store.artworkRevision() != third)
    #expect(try await store.artist(id: "ar")?.coverSha == "ppp")
    let withPortrait = try #require(try await store.artworkRevision())
    // A removed album can change an artist's cover.
    try await store.apply(
      delta(
        "six", upserts: #""tracks":[],"albums":[],"artists":[],"playlists":[]"#,
        deletedAlbums: #""al""#))
    let fourth = try #require(try await store.artworkRevision())
    #expect(fourth != withPortrait)
    // A full resync moves it when covers differ (the artist is gone), but
    // not when it repeats the same covers.
    var song = Track(id: "t", title: "Song", artist: "A")
    try await store.replace(LibrarySnapshot(revision: "seven", tracks: [song]))
    let fifth = try #require(try await store.artworkRevision())
    #expect(fifth != fourth)
    try await store.replace(LibrarySnapshot(revision: "eight", tracks: [song]))
    #expect(try await store.artworkRevision() == fifth)
    song.coverSha = "ccc"
    try await store.replace(LibrarySnapshot(revision: "nine", tracks: [song]))
    #expect(try await store.artworkRevision() != fifth)
  }
}

struct LibraryReloadGateTests {
  /// Choosing Soundtracks during a sync that brings no change still loads
  /// Soundtracks once the sync ends (it used to stay empty).
  @Test func aCategoryChosenDuringAnUnchangedSyncLoadsAfterIt() async throws {
    let store = try LibraryStore(path: ":memory:")
    var album = Album(id: "score", title: "Score", artist: "Composer")
    album.category = "soundtrack"
    try await store.replace(LibrarySnapshot(revision: "one", albums: [album]))
    var gate = LibraryReloadGate()
    var shown: [Album] = []
    var category = "album"
    func reloadIfNeeded(libraryChanged: Bool) async throws {
      if gate.afterSync(libraryChanged: libraryChanged) {
        shown = try await store.albums(category: category)
      }
    }
    // The sync starts; the person picks Soundtracks while it runs.
    category = "soundtrack"
    shown = []
    gate.viewChangedDuringSync()
    try await reloadIfNeeded(libraryChanged: false)
    #expect(shown.map(\.id) == ["score"])
    // Later unchanged syncs do not reload again.
    shown = []
    try await reloadIfNeeded(libraryChanged: false)
    #expect(shown.isEmpty)
    let leftOver = gate.takePending()
    #expect(!leftOver)
  }
  @Test func aFailedSyncStillLeavesTheChoiceToLoad() {
    var gate = LibraryReloadGate()
    gate.viewChangedDuringSync()
    // The sync threw before its reload: the pending choice is still there.
    let pending = gate.takePending()
    let again = gate.takePending()
    let changed = gate.afterSync(libraryChanged: true)
    #expect(pending && !again && changed)
  }
  /// D-120: Albums sort by year, newest first and undated last; Songs
  /// say how long the whole library plays.
  @Test func albumsSortByYearAndTheLibraryKnowsItsLength() async throws {
    let store = try LibraryStore(path: ":memory:")
    let albums: [Album] = try LibraryCodec.decoder().decode(
      [Album].self,
      from: Data(
        (#"[{"id":"old","title":"A","artist":"X","year":1971},{"id":"none","title":"B","artist":"X"},"#
          + #"{"id":"new","title":"C","artist":"X","year":"2024"},{"id":"mid","title":"D","artist":"X","year":1999}]"#)
          .utf8))
    try await store.replace(
      LibrarySnapshot(
        revision: "one",
        tracks: [
          Track(id: "1", title: "One", artist: "X", duration: 100),
          Track(id: "2", title: "Two", artist: "X", duration: 50.5),
          Track(id: "3", title: "Three", artist: "X"),
        ], albums: albums))
    #expect(try await store.albums(sort: .year).map(\.id) == ["new", "mid", "old", "none"])
    #expect(try await store.totalDuration() == 150.5)
  }
  /// #281 (D-120 screen 30): Albums sort by when they were added, newest
  /// first; albums the server gave no time follow in today's title order.
  @Test func albumsSortByRecentlyAddedThenTitle() async throws {
    let store = try LibraryStore(path: ":memory:")
    let albums: [Album] = try LibraryCodec.decoder().decode(
      [Album].self,
      from: Data(
        (#"[{"id":"old","title":"A","artist":"X","added_at":"2026-01-02T00:00:00Z"},{"id":"z","title":"Z","artist":"X"},"#
          + #"{"id":"new","title":"C","artist":"X","added_at":"2026-11-20T09:30:00Z"},{"id":"b","title":"B","artist":"X","added_at":null}]"#)
          .utf8))
    #expect(albums.first?.addedAt == "2026-01-02T00:00:00Z")
    try await store.replace(LibrarySnapshot(revision: "one", albums: albums))
    #expect(try await store.albums(sort: .recentlyAdded).map(\.id) == ["new", "old", "b", "z"])
    // Saved and read back with its time.
    #expect(try await store.album(id: "new")?.addedAt == "2026-11-20T09:30:00Z")
  }
  /// Codex round 1 on batch E: Songs' Play plays the whole library, not the
  /// first page; a row's tap starts at its song and plays on past the page.
  @Test func songsPlayTheWholeLibraryInItsSort() async throws {
    let store = try LibraryStore(path: ":memory:")
    let tracks = (0..<250).map {
      Track(id: String($0), title: String(format: "%03d", $0), artist: "A")
    }
    try await store.replace(LibrarySnapshot(revision: "one", tracks: tracks))
    #expect(try await store.tracks(limit: 100, sort: .titleDescending).count == 100)
    let all = try await store.allTracks(sort: .titleDescending)
    #expect(all.map(\.id) == tracks.reversed().map(\.id))
    let queue = LibraryQueue.make(all, removing: { $0.id == "5" }, from: "150")
    #expect(queue.songs.count == 249)
    #expect(queue.songs[queue.index].id == "150")
    #expect(queue.songs.last?.id == "0")
    #expect(LibraryQueue.make(all, removing: { _ in false }, shuffled: true).songs.count == 250)
  }
}
