import Foundation
import PigDomain
import PigPlayback
import PigStore
import PigSync
import Testing

@Test func queueKeepsRepeatedOccurrencesAndPreviousRestarts() {
  let song = Track(id: "same", title: "Song", artist: "Artist")
  var queue = PlaybackQueue()
  queue.replace([song, song])
  #expect(queue.items[0].id != queue.items[1].id)
  let advanced = queue.next()
  #expect(advanced)
  let restarted = queue.previous(elapsed: 4)
  #expect(restarted)
  #expect(queue.index == 1)
  let previous = queue.previous(elapsed: 1)
  #expect(!previous)
  #expect(queue.index == 0)
}

@Test func transitionRendererPreservesBoundaryAndBoundsOverlap() {
  let first: [Float] = [0.1, 0.2, 0.3]
  let second: [Float] = [0.4, 0.5, 0.6]
  #expect(TransitionPlanner.render(first, second, overlapFrames: 0) == first + second)
  let unity = TransitionPlanner.render([1, 1, 1], [1, 1, 1], overlapFrames: 2)
  #expect(unity == [1, 1, 1, 1])
  #expect(TransitionPlanner.mode(crossfade: true, albumOrder: true) == .gapless)
  #expect(TransitionPlanner.mode(crossfade: true, albumOrder: false) == .crossfade(seconds: 3))
}

@Test func namespaceSeparatesPersonAndServerPort() {
  let first = URL(string: "https://music.example:10000")!
  #expect(
    LibraryNamespace.key(server: first, personId: "a")
      != LibraryNamespace.key(server: first, personId: "b"))
  #expect(
    LibraryNamespace.key(server: first, personId: "a")
      != LibraryNamespace.key(server: URL(string: "https://music.example")!, personId: "a"))
}

@Test func cloudPlaylistEntryPreservesEditionContextAndOccurrenceIdentity() throws {
  let entry = try LibraryCodec.decoder().decode(
    PlaylistEntry.self,
    from: Data(
      #"{"entry_id":"occurrence","position":2,"title":"Live Song","artist":"Artist","album":"Live Edition","album_artist":"Artist","duration":180,"disc_number":2,"track_number":3,"musicbrainz_release_id":"release","musicbrainz_recording_id":"recording","saved_track":null}"#
        .utf8))
  #expect(entry.id == "occurrence")
  #expect(entry.album == "Live Edition")
  #expect(entry.musicbrainzReleaseId == "release")
  #expect(entry.musicbrainzRecordingId == "recording")
  #expect(entry.discNumber == 2)
  #expect(entry.trackNumber == 3)
  #expect(entry.savedTrack == nil)
}

@Test func storeReplacesAndAppliesDeletesAtomicallyAndSearchesUnicode() async throws {
  let store = try LibraryStore(path: ":memory:")
  try await store.replace(
    .init(
      revision: "one",
      tracks: [
        Track(id: "a", title: "Beyoncé", artist: "Artist"),
        Track(id: "b", title: "Other", artist: "Artist"),
      ]))
  #expect(try await store.tracks(query: "Beyonce").map(\.id) == ["a"])
  let delta = try LibraryCodec.decoder().decode(
    LibraryDelta.self,
    from: Data(
      #"{"revision":"two","upserts":{"tracks":[{"id":"c","title":"New","artist":"Artist"}],"albums":[],"artists":[],"playlists":[]},"deletes":{"tracks":["a"],"albums":[],"artists":[],"playlists":[]}}"#
        .utf8))
  try await store.apply(delta)
  #expect(try await store.revision() == "two")
  #expect(try await store.count(.tracks) == 2)
  #expect(try await store.tracks(query: "Beyonce").isEmpty)
  #expect(try await store.tracks(query: "\" OR *").isEmpty)
}

@Test func storeSeparatesAlbumCategoriesAndHidesCompilationOnlyArtists() async throws {
  let store = try LibraryStore(path: ":memory:")
  let snapshot = try LibraryCodec.decoder().decode(
    LibrarySnapshot.self,
    from: Data(
      #"{"revision":"one","tracks":[],"playlists":[],"albums":[{"id":"a","title":"Own","artist":"Band","category":"album"},{"id":"m","title":"Show","artist":"Cast","category":"musical"},{"id":"c","title":"Hits","artist":"Various","category":"compilation"},{"id":"u","title":"Legacy","artist":"Band"}],"artists":[{"id":"band","name":"Band"},{"id":"guest","name":"Guest","compilation_only":true}]}"#
        .utf8))
  try await store.replace(snapshot)
  #expect(try await store.albums().map(\.id) == ["u", "a"])
  #expect(try await store.albums(category: "musical").map(\.id) == ["m"])
  #expect(try await store.albumCategoryCounts() == ["album": 2, "musical": 1, "compilation": 1])
  #expect(try await store.artists().map(\.id) == ["band"])
  #expect(try await store.ownArtistCount() == 1)
}

private actor FailedSource: LibrarySource {
  func bootstrap() async throws -> LibrarySnapshot { throw NativeFailure.unavailable("offline") }
  func changes(since: String) async throws -> LibraryDelta { throw NativeFailure.resync }
}

@Test func failedResyncRetainsLastValidOfflineLibrary() async throws {
  let store = try LibraryStore(path: ":memory:")
  try await store.replace(
    .init(revision: "valid", tracks: [Track(id: "a", title: "Kept", artist: "Artist")]))
  do {
    try await SyncCoordinator(source: FailedSource(), store: store).refresh()
    Issue.record("Expected sync failure")
  } catch NativeFailure.unavailable {}
  #expect(try await store.revision() == "valid")
  #expect(try await store.tracks().first?.title == "Kept")
}

@Test func homePrefetchWaitsForUnconstrainedNetworksAndNormalPower() {
  #expect(PrefetchConditions().allowsPrefetch)
  #expect(!PrefetchConditions(lowPowerMode: true).allowsPrefetch)
  #expect(!PrefetchConditions(lowDataMode: true).allowsPrefetch)
  #expect(!PrefetchConditions(cellular: true).allowsPrefetch)
  #expect(!PrefetchConditions(connected: false).allowsPrefetch)
}

@Test func catalogueReleasesCarryYourLibraryCopyWhenTheServerKnowsIt() throws {
  let page = try LibraryCodec.decoder().decode(
    CatalogueGroups.self,
    from: Data(
      #"{"total":2,"offset":0,"releases":[{"id":"g1","title":"Brat","date":"2024","type":"Album","saved_album_id":"a1","saved_songs":2},{"id":"g2","title":"Crash","date":"2022","type":"Album"}]}"#
        .utf8))
  #expect(page.releases[0].savedAlbumId == "a1")
  #expect(page.releases[0].savedSongs == 2)
  #expect(page.releases[1].savedAlbumId == nil)
}

@Test func requestGroupsReadAsOneLinePerRequest() throws {
  let page = try LibraryCodec.decoder().decode(
    RequestGroupPage.self,
    from: Data(
      #"{"total":1,"active":2,"offset":0,"next_offset":null,"downloads_enabled":true,"groups":[{"id":"g","kind":"album","title":"Meteora","artist":"Linkin Park","release_id":"r","state":"waiting","songs":13,"done":3,"active":1,"waiting":8,"failed":1,"progress":0.3,"requested_at":"2026-10-19T00:00:00Z","updated_at":"2026-10-19T00:05:00Z","items":[{"id":"a","title":"Foreword","status":"completed","request_progress":null,"source":"soulseek","quality":"FLAC","server_state":"in_library"}]}]}"#
        .utf8))
  let group = try #require(page.groups.first)
  #expect(group.summary == "3 of 13 songs in your library · 1 downloading · 8 waiting · 1 failed")
  #expect(RequestSource.name(group.items[0].source ?? "") == "Soulseek")
}

@Test func sessionsSayWhichDeviceThisIs() throws {
  let sessions = try LibraryCodec.decoder().decode(
    AccountSessions.self,
    from: Data(
      #"{"sessions":[{"id":"a","kind":"device","device_name":"iPhone","platform":"ios","created_at":1,"last_seen_at":2,"expires_at":3,"revoked_at":null,"current":true},{"id":"b","kind":"web","device_name":"Safari on Mac","platform":"web","created_at":1,"last_seen_at":2,"expires_at":3,"revoked_at":null}]}"#
        .utf8)
  ).sessions
  #expect(sessions[0].current == true && sessions[0].platformName == "iPhone or iPad app")
  #expect(sessions[1].current == nil && sessions[1].platformName == "Web")
}

@Test func onlyTheLatestPreferenceChoiceKeepsItsOutcome() async {
  // Two quick appearance choices: the first save finishes last.
  actor Model {
    var changes = LatestChange()
    var appearance = "system"
    func choose(_ value: String, delay: Duration) async -> Bool {
      let change = changes.begin()
      appearance = value
      try? await Task.sleep(for: delay)
      guard changes.isLatest(change) else { return false }
      appearance = value
      return true
    }
  }
  let model = Model()
  let first = Task { await model.choose("dark", delay: .milliseconds(200)) }
  // The second choice starts only once the first has begun.
  while await model.changes.current < 1 { await Task.yield() }
  let latest = await model.choose("light", delay: .zero)
  let stale = await first.value
  #expect(!stale && latest)
  #expect(await model.appearance == "light")
}

struct SongFailureTests {
  /// 22 Oct diagnostics: with the server out of reach, streaming passed over
  /// every song of the queue in seconds. Only a song's own failure passes over.
  @Test func onlyASongsOwnFailurePassesOverIt() {
    #expect(NativeFailure.songUnavailable("gone").isSongUnavailable)
    #expect(NativeFailure.invalidMediaURL.isSongUnavailable)
    let server = NativeFailure.unavailable("Unable to prepare this song for playback.")
    #expect(!server.isSongUnavailable)
    #expect(!NativeFailure.authentication.isSongUnavailable)
    #expect(!URLError(.cannotConnectToHost).isSongUnavailable)
    #expect(!URLError(.notConnectedToInternet).isSongUnavailable)
  }
}

struct RenewableSessionTests {
  /// After a break, the next requests go out on a new session (new
  /// connections); a request already running keeps its own.
  @Test func renewingHandsOutAFreshSession() async throws {
    let sessions = RenewableSession { URLSession(configuration: .ephemeral) }
    let first = sessions.session
    #expect(sessions.session === first)
    sessions.renew()
    let second = sessions.session
    #expect(second !== first)
    sessions.renew()
    #expect(sessions.session !== second)
  }
}
