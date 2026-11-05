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
  var asked: [[Track]] = []
  func add(_ query: String) { queries.append(query) }
  func ask(_ tracks: [Track]) { asked.append(tracks) }
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

/// The server's answer (`/server/matches`), in the order asked: the person's
/// copy of "Clocks", a household copy of "Yellow", nothing for the rest. The
/// server, not the phone, decides that a live copy is no match.
private struct ServerMatches: ApplePlaylistStatusSource {
  let calls: Calls
  var short = false
  func serverMatches(_ tracks: [Track]) async throws -> [Track?] {
    await calls.ask(tracks)
    let answer = tracks.map { track -> Track? in
      var copy = Track(id: "s-" + track.title, title: track.title, artist: track.artist)
      switch track.title {
      case "Clocks": copy.serverState = "in_library"
      case "Yellow": copy.serverState = "household_available"
      default: return nil
      }
      return copy
    }
    return short ? Array(answer.dropLast()) : answer
  }
}

private func song(_ position: Int, _ title: String, artist: String = "Coldplay") -> ApplePlaylistSong
{
  ApplePlaylistSong(
    // Apple's id belongs to the song, wherever it sits in the playlist.
    position: position, appleId: title.utf8.reduce(7) { abs($0 &* 31 &+ Int($1)) % 1_000_000 },
    title: title, artist: artist,
    album: title + " album", duration: 240)
}

@Test func aPlaylistsSongsTakeTheServersAnswerInOrder() async throws {
  let songs = [song(0, "Clocks"), song(1, "Yellow"), song(2, "Fix You"), song(3, "Lost", artist: " ")]
  let calls = Calls()
  let found = try await ApplePlaylistStatuses.states(
    for: songs, source: ServerMatches(calls: calls))
  let states = try #require(found.states(for: songs))
  #expect(states.map(\.track?.id) == ["s-Clocks", "s-Yellow", nil, nil])
  guard case .library = states[0], case .server = states[1], case .elsewhere = states[2],
    case .elsewhere = states[3]
  else {
    Issue.record("wrong states: \(states)")
    return
  }
  // One call, with each song's title, artist, album and length; a song the
  // server would refuse (no artist) isn't sent.
  let asked = await calls.asked
  #expect(asked.count == 1)
  #expect(asked.first?.map(\.title) == ["Clocks", "Yellow", "Fix You"])
  #expect(asked.first?.first?.album == "Clocks album")
  #expect(asked.first?.first?.duration == 240)

  // Over 200 songs: one call per 200, answers still in place.
  let long = (0..<250).map { song($0, $0 == 210 ? "Yellow" : "Song \($0)") }
  let many = Calls()
  let longStates = try #require(
    try await ApplePlaylistStatuses.states(for: long, source: ServerMatches(calls: many))
      .states(for: long))
  #expect(await many.asked.map(\.count) == [200, 50])
  #expect(longStates.indices.filter { longStates[$0].track != nil } == [210])

  // An answer that doesn't line up is never used.
  await #expect(throws: (any Error).self) {
    _ = try await ApplePlaylistStatuses.states(
      for: songs, source: ServerMatches(calls: Calls(), short: true))
  }
}

@Test func aChangedPlaylistNeverReusesOldStatuses() async throws {
  let before = [song(0, "Clocks"), song(1, "Yellow")]
  let saved = try await ApplePlaylistStatuses.states(
    for: before, source: ServerMatches(calls: Calls()))
  #expect(saved.states(for: before) != nil)
  // Reordered, replaced, longer or shorter: the saved states are not shown.
  let reordered = [song(0, "Yellow"), song(1, "Clocks")]
  #expect(saved.states(for: reordered) == nil)
  #expect(saved.states(for: [song(0, "Clocks"), song(1, "Paradise")]) == nil)
  #expect(saved.states(for: before + [song(2, "Fix You")]) == nil)
  #expect(saved.states(for: [song(0, "Clocks")]) == nil)
  // Kept on the device and read back, it still belongs only to its songs.
  let stored = try JSONDecoder().decode(
    ApplePlaylistStates.self, from: JSONEncoder().encode(saved))
  #expect(stored.states(for: before) == saved.states)
  #expect(stored.states(for: reordered) == nil)
}
