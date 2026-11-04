import Foundation
import PigDomain
import PigSync
import Testing

// #109 stage B (D-66): Apple Music playlists from the phone's MusicKit search.

private func decode<T: Decodable>(_ json: String) -> T {
  try! LibraryCodec.decoder().decode(T.self, from: Data(json.utf8))
}

private let serverJSON = #"""
  {"top":[{"kind":"song","id":"yellow"}],
   "songs":[{"id":"yellow","title":"Yellow","artist":"Coldplay","server_state":"household_available"}],
   "albums":[],"artists":[],
   "playlists":[{"id":"p1","name":"Yellow days","revision":1}],
   "mixes":[{"id":"e1","kind":"daily","day":"2026-11-05","title":"Yellow blend",
             "reason":"Songs you play most","tracks":[]}]}
  """#

private actor Calls {
  var queries: [String] = []
  var lookups: [[String]] = []
  func add(_ query: String) { queries.append(query) }
  func lookup(_ titles: [String]) { lookups.append(titles) }
}

private struct ServerOnly: FoundSearchSource {
  func foundResults(query: String) async throws -> FoundResults { decode(serverJSON) }
  func onlineResults(query: String) async throws -> OnlineResults { OnlineResults() }
  func catalogueArtists(query: String, offset: Int) async throws -> CatalogueArtistPage {
    decode(#"{"artists":[],"total":0,"offset":0,"next_offset":null}"#)
  }
}

private let chill = ApplePlaylist(id: "pl.1", name: "Yellow Hits", curator: "Apple Music")

@MainActor @Test func deniedAppleMusicShowsNoApplePlaylistsAndSearchStillWorks() async {
  let calls = Calls()
  let denied = ApplePlaylistSearch(
    authorize: { false },
    search: { query in
      await calls.add(query)
      return [chill]
    })
  let search = FoundSearch(source: ServerOnly(), applePlaylists: denied)
  await search.search("Yellow")
  #expect(search.screen.top.first?.id == "song:yellow")
  #expect(search.screen.playlists.map(\.id) == ["playlist:p1", "mix:e1"])
  #expect(search.error == nil && !search.loading)
  // Without permission MusicKit is never asked.
  #expect(await calls.queries.isEmpty)

  // MusicKit failing (no developer-portal service yet, a simulator) is the same.
  struct NoService: Error {}
  let failing = ApplePlaylistSearch(authorize: { true }, search: { _ in throw NoService() })
  let other = FoundSearch(source: ServerOnly(), applePlaylists: failing)
  await other.search("Yellow")
  #expect(other.screen.playlists.map(\.id) == ["playlist:p1", "mix:e1"])
  #expect(other.error == nil)
}

@MainActor @Test func applePlaylistsFollowTheServersOwnUnderPlaylists() async {
  let calls = Calls()
  let allowed = ApplePlaylistSearch(
    authorize: { true },
    search: { query in
      await calls.add(query)
      return [chill]
    })
  let search = FoundSearch(source: ServerOnly(), applePlaylists: allowed)
  await search.search("Yellow")
  #expect(
    search.screen.playlists.map(\.id) == ["playlist:p1", "mix:e1", "apple-playlist:pl.1"])
  // The best one also fills Top results after everything else.
  #expect(search.screen.top.map(\.id) == ["song:yellow", "apple-playlist:pl.1"])
  // A single letter asks nothing of Apple; the same words reuse the answer.
  await search.search("Y")
  #expect(search.screen.playlists.map(\.id) == ["playlist:p1", "mix:e1"])
  await search.search("Yellow")
  #expect(search.screen.playlists.last?.id == "apple-playlist:pl.1")
  #expect(await calls.queries == ["Yellow"])
}

private struct StatusSource: ApplePlaylistStatusSource {
  let calls: Calls
  func lookupSongs(_ tracks: [Track]) async throws -> [Track?] {
    await calls.lookup(tracks.map(\.title))
    return tracks.map { track in
      guard track.title == "Clocks" else { return nil }
      var mine = Track(id: "t-clocks", title: "Clocks", artist: "Coldplay")
      mine.serverState = "in_library"
      return mine
    }
  }
  func foundResults(query: String) async throws -> FoundResults {
    await calls.add(query)
    return decode(
      #"{"top":[],"albums":[],"artists":[],"playlists":[],"songs":["#
        + #"{"id":"s-yellow","title":"Yellow","artist":"Coldplay","server_state":"household_available"},"#
        + #"{"id":"s-fix","title":"Fix You (Live)","artist":"Coldplay","server_state":"household_available"},"#
        + #"{"id":"s-viva","title":"Viva la Vida","artist":"Coldplay","server_state":"in_library"}]}"#)
  }
}

@Test func aPlaylistsSongsAreMarkedLibraryServerOrNotOnTheServer() async throws {
  let songs = [
    ApplePlaylistSong(position: 0, appleId: 1, title: "Clocks", artist: "Coldplay"),
    ApplePlaylistSong(
      position: 1, appleId: 2, title: "Yellow (Remastered 2020)", artist: "Coldplay & Friends"),
    // The studio song: the server has only the live one.
    ApplePlaylistSong(position: 2, appleId: 3, title: "Fix You", artist: "Coldplay"),
    // Found by the ranked search though the library matches missed it.
    ApplePlaylistSong(position: 3, appleId: 4, title: "Viva La Vida", artist: "Coldplay"),
    ApplePlaylistSong(position: 4, appleId: 5, title: "Paradise", artist: "Coldplay"),
  ]
  let calls = Calls()
  let states = try await ApplePlaylistStatuses.states(
    for: songs, source: StatusSource(calls: calls), concurrency: 2)
  #expect(states.map(\.track?.id) == ["t-clocks", "s-yellow", nil, "s-viva", nil])
  guard case .library = states[0], case .server = states[1], case .elsewhere = states[2],
    case .library = states[3], case .elsewhere = states[4]
  else {
    Issue.record("wrong states: \(states)")
    return
  }
  // One library call for the whole playlist; the server search only for the rest.
  #expect(await calls.lookups.count == 1)
  #expect(await calls.queries.count == 4)
  #expect(!(await calls.queries).contains { $0.hasPrefix("Clocks") })
}

@Test func titlesMatchAcrossEditionsButNotAcrossRecordings() {
  #expect(ApplePlaylistMatching.titleKey("Song (feat. Someone)") == "song")
  #expect(ApplePlaylistMatching.titleKey("Song - Remastered 2009") == "song")
  #expect(ApplePlaylistMatching.titleKey("Without You (With Strings)") == "without you")
  #expect(ApplePlaylistMatching.titleKey("Without Me") == "without me")
  #expect(ApplePlaylistMatching.titleKey("Song (Live)") != ApplePlaylistMatching.titleKey("Song"))
  #expect(ApplePlaylistMatching.titleKey("Song (Remix)") != "song")
  #expect(ApplePlaylistMatching.artistKeys("Simon & Garfunkel").contains("simon garfunkel"))
}
