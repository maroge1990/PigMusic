import Foundation
import PigDomain
import PigSync
import Testing

private let serverJSON = #"""
  {"top":[{"kind":"song","id":"yellow"},{"kind":"artist","id":"coldplay"}],
   "songs":[{"id":"yellow","title":"Yellow","artist":"Coldplay","server_state":"household_available"}],
   "albums":[{"id":"parachutes","title":"Parachutes","artist":"Coldplay","in_library":true}],
   "artists":[{"id":"coldplay","name":"Coldplay","in_library":false}],
   "playlists":[]}
  """#
private let onlineJSON = #"""
  {"songs":[{"apple_id":11,"title":"Yellow (Live)","artist":"Coldplay","album":"Live 2003"}],
   "albums":[{"apple_id":21,"title":"Yellow - Single","artist":"Coldplay"}],
   "state":"ready"}
  """#

private func decode<T: Decodable>(_ json: String) -> T {
  try! LibraryCodec.decoder().decode(T.self, from: Data(json.utf8))
}

@Test func theServersRankingLeadsAndAppleFillsTopResultsAfterIt() {
  let screen = FoundScreen(
    decode(serverJSON), online: decode(onlineJSON),
    catalogue: decode(
      #"[{"id":"mb-coldplay","name":"Coldplay","description":""},"#
        + #"{"id":"mb-other","name":"Coldplay Tribute","description":""}]"#))
  #expect(
    screen.top.map(\.id) == [
      "song:yellow", "artist:coldplay", "apple-song:11", "apple-album:21",
      "catalogue-artist:mb-other",
    ])
  // A MusicBrainz artist the server already lists by name isn't repeated.
  #expect(screen.artists.map(\.id) == ["artist:coldplay", "catalogue-artist:mb-other"])
  #expect(screen.songs.map(\.id) == ["song:yellow", "apple-song:11"])
  guard case .album(_, let mine) = screen.albums[0] else {
    Issue.record("not an album")
    return
  }
  #expect(mine)
  #expect(screen.kinds == [.top, .artists, .albums, .songs])
  // Nothing on the server: Apple's results lead.
  let only = FoundScreen(nil, online: decode(onlineJSON))
  #expect(only.top.first?.id == "apple-song:11")
}

@Test func homeMixesShowUnderPlaylistsAndOldServersStillDecode() {
  // #109: the person's Home mixes named like the search.
  let found: FoundResults = decode(
    #"{"top":[],"songs":[],"albums":[],"artists":[],"playlists":[],"#
      + #""mixes":[{"id":"e1","kind":"daily","day":"2026-11-01","#
      + #""title":"Daily blend","reason":"Songs you play most","tracks":[]}]}"#)
  let screen = FoundScreen(found)
  #expect(screen.playlists.map(\.id) == ["mix:e1"])
  #expect(screen.kinds == [.top, .playlists])
  // A server before #109 sends no mixes.
  #expect(FoundScreen(decode(serverJSON)).playlists.isEmpty)
}

private actor Gate {
  var open = false
  func wait() async {
    while !open { await Task.yield() }
  }
  func release() { open = true }
}

private struct FakeSource: FoundSearchSource {
  let gate: Gate
  let unsupported: Bool
  func foundResults(query: String) async throws -> FoundResults {
    if unsupported { throw SearchUnsupported() }
    if query == "old" { await gate.wait() }
    let value: FoundResults = decode(serverJSON)
    return query == "old" ? FoundResults() : value
  }
  func onlineResults(query: String) async throws -> OnlineResults { decode(onlineJSON) }
  func catalogueArtists(query: String, offset: Int) async throws -> CatalogueArtistPage {
    decode(#"{"artists":[],"total":0,"offset":0,"next_offset":null}"#)
  }
}

@MainActor @Test func aNewerSearchDropsAnOlderOnesLateAnswer() async {
  let gate = Gate()
  let search = FoundSearch(source: FakeSource(gate: gate, unsupported: false))
  let old = Task { await search.search("old") }
  await Task.yield()
  await search.search("Yellow")
  await gate.release()
  await old.value
  #expect(search.query == "Yellow")
  #expect(search.screen.top.first?.id == "song:yellow")
  #expect(!search.fromThisPhone)
}

/// This phone's library: `old` waits until released.
private actor PhoneLibrary: LibrarySearchSource {
  var waiting: CheckedContinuation<LibrarySearchPage, any Error>?
  var asked = 0
  func searchPage(query: String, offset: Int, limit: Int) async throws -> LibrarySearchPage {
    asked += 1
    if query == "old" { return try await withCheckedThrowingContinuation { waiting = $0 } }
    return LibrarySearchPage(
      tracks: [Track(id: "phone-" + query, title: query, artist: "Artist")],
      albums: [decode(#"{"id":"phone-album","title":"Phone album","artist":"Artist"}"#)])
  }
  func ready() -> Bool { waiting != nil }
  func release() {
    waiting?.resume(returning: LibrarySearchPage(tracks: [Track(id: "old", title: "old", artist: "A")]))
    waiting = nil
  }
}

@MainActor @Test func anOlderServerSearchesThisPhonesLibrary() async {
  // #177: SearchSession's cached lane now lives in FoundSearch.
  let library = PhoneLibrary()
  let search = FoundSearch(source: FakeSource(gate: Gate(), unsupported: true), library: library)
  await search.search("Yellow")
  #expect(search.fromThisPhone && !search.loading && search.error == nil)
  #expect(search.screen.songs.map(\.id) == ["song:phone-Yellow"])
  guard case .album(_, let mine) = search.screen.albums.first else {
    Issue.record("no album")
    return
  }
  #expect(mine)
  #expect(search.screen.top.map(\.id) == ["album:phone-album", "song:phone-Yellow"])
}

@MainActor @Test func offlineSearchUsesThisPhonesLibraryAndAsksTheServerAgainNextTime() async {
  let library = PhoneLibrary()
  let search = FoundSearch(source: FailingSource(hold: Hold()), library: library)
  await search.search("Yellow")
  #expect(search.fromThisPhone && search.error == nil && !search.loading)
  #expect(search.screen.songs.map(\.id) == ["song:phone-Yellow"])
  // Nothing from this phone is remembered: the server is asked again.
  await search.search("Yellow")
  #expect(await library.asked == 2)
}

@MainActor @Test func theDemoSearchesOnlyThisPhoneAndDropsALateAnswer() async {
  let library = PhoneLibrary()
  let search = FoundSearch(source: nil, library: library)
  let old = Task { await search.search("old") }
  while !(await library.ready()) { await Task.yield() }
  await search.search("new")
  await library.release()
  await old.value
  #expect(search.query == "new" && search.fromThisPhone)
  #expect(search.screen.songs.map(\.id) == ["song:phone-new"])
  let pending = Task { await search.search("old") }
  while !(await library.ready()) { await Task.yield() }
  search.clear()
  await library.release()
  await pending.value
  #expect(search.query.isEmpty && search.results == nil && !search.loading)
}

private struct ExpiredSource: FoundSearchSource {
  func foundResults(query: String) async throws -> FoundResults {
    throw NativeFailure.authentication
  }
  func onlineResults(query: String) async throws -> OnlineResults {
    throw NativeFailure.unavailable("down")
  }
  func catalogueArtists(query: String, offset: Int) async throws -> CatalogueArtistPage {
    throw NativeFailure.unavailable("down")
  }
}

@MainActor @Test func anExpiredSessionClearsTheSearchWithoutTheLibraryAndSignsOutOnce() async {
  var signedOut = 0
  let library = PhoneLibrary()
  let search = FoundSearch(
    source: ExpiredSource(), library: library, onAuthentication: { signedOut += 1 })
  await search.search("Yellow")
  #expect(signedOut == 1)
  #expect(search.query.isEmpty && search.results == nil && !search.fromThisPhone)
  #expect(await library.asked == 0)
}

private actor CountingSource: FoundSearchSource {
  var counts = [0, 0, 0]
  func foundResults(query: String) async throws -> FoundResults {
    counts[0] += 1
    return FoundResults()
  }
  func onlineResults(query: String) async throws -> OnlineResults {
    counts[1] += 1
    return OnlineResults()
  }
  func catalogueArtists(query: String, offset: Int) async throws -> CatalogueArtistPage {
    counts[2] += 1
    return decode(
      #"{"artists":[{"id":"\#(query)","name":"\#(query)","description":""}],"total":1,"offset":0,"next_offset":null}"#
    )
  }
}

@MainActor @Test func typeAheadReusesAnswersAndSkipsShortWideLanes() async {
  // Moved from SearchSession's test (#177).
  #expect(SearchText.debounce >= .milliseconds(300))
  let source = CountingSource()
  let search = FoundSearch(source: source)
  let start = Date()
  await search.search("R")
  #expect(await source.counts == [1, 0, 0])
  await search.search("Ra")
  await search.search("Rad")
  // Backspacing to an earlier query, in any case, reuses its answers.
  await search.search("ra", now: start.addingTimeInterval(10))
  #expect(await source.counts == [3, 2, 2])
  #expect(search.catalogue.map(\.id) == ["Ra"])
  // Refresh, and answers older than five minutes, ask again.
  await search.search("Ra", force: true)
  await search.search("Rad", now: start.addingTimeInterval(400))
  #expect(await source.counts == [5, 4, 4])
}

private actor Hold {
  /// Waits until cancelled: a remote lane that never answers.
  func forever() async {
    while !Task.isCancelled { await Task.yield() }
  }
}

private struct FailingSource: FoundSearchSource {
  let hold: Hold
  func foundResults(query: String) async throws -> FoundResults {
    throw NativeFailure.unavailable("down")
  }
  func onlineResults(query: String) async throws -> OnlineResults {
    await hold.forever()
    throw CancellationError()
  }
  func catalogueArtists(query: String, offset: Int) async throws -> CatalogueArtistPage {
    await hold.forever()
    throw CancellationError()
  }
}

@MainActor @Test func aFailedServerSearchDoesNotWaitForSlowerLanes() async {
  // Codex: without the ranked results, slower lanes are not waited for.
  let search = FoundSearch(source: FailingSource(hold: Hold()))
  await search.search("Yellow")
  #expect(search.error != nil && !search.loading)
}
