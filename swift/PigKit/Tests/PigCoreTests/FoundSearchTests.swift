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
  #expect(!search.unsupported)
}

@MainActor @Test func anOlderServerFallsBackToTheEarlierSearch() async {
  let search = FoundSearch(source: FakeSource(gate: Gate(), unsupported: true))
  await search.search("Yellow")
  #expect(search.unsupported)
  #expect(!search.loading)
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
  // Codex: the earlier (cached) search must start as soon as ranked results fail.
  let search = FoundSearch(source: FailingSource(hold: Hold()))
  await search.search("Yellow")
  #expect(search.error != nil && !search.loading)
}
