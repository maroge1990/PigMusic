import Foundation
import HTTPTypes
import OpenAPIRuntime
import PigAPI
import PigDomain
import Testing

@testable import PigSync

private actor HomeTokens: DeviceTokenStore {
  func read() -> String? { "fixture" }
  func save(_ value: String) {}
  func clear() {}
}
private actor HomeTransport: ClientTransport {
  let status: Int
  let json: String
  var paths: [String] = []
  var bodies: [Data] = []
  init(status: Int = 200, json: String) {
    self.status = status
    self.json = json
  }
  func send(_ request: HTTPRequest, body: HTTPBody?, baseURL: URL, operationID: String) async throws
    -> (HTTPResponse, HTTPBody?)
  {
    paths.append(request.path ?? "")
    if let body { bodies.append(try await Data(collecting: body, upTo: 1_000_000)) }
    var response = HTTPResponse(status: .init(code: status))
    response.headerFields[.contentType] = "application/json"
    return (response, HTTPBody(json))
  }
}

struct HomeTests {
  private func service(_ transport: HomeTransport) -> NativeService {
    let origin = URL(string: "https://fixture.invalid")!
    return NativeService(
      server: origin, client: Client(serverURL: origin, transport: transport), vault: HomeTokens())
  }
  @Test func discoveryKeepsRecordingAndReleaseIdentitySeparateFromSavedFiles() async throws {
    let transport = HomeTransport(
      json:
        #"{"artists":[],"albums":[],"songs":[{"id":"recording-one","name":"First Light","artist":"The Orchard","artist_id":"artist-one","kind":"songs","release_id":"release-one","album":"Sunrise","reason":"Similar to Riverlight"}],"source":"Similar to artists in your library","unavailable":false,"state":"ready"}"#
    )
    let result = try await service(transport).home(.recommended)
    let item = try #require(result.feed?.songs.first)
    let track = try #require(item.playbackTrack)
    #expect(track.id == "catalogue:recording-one")
    #expect(track.serverState == "not_in_library")
    #expect(track.musicbrainzRecordingId == "recording-one")
    #expect(track.musicbrainzReleaseId == "release-one")
    #expect(item.reason == "Similar to Riverlight")
    #expect(await transport.paths == ["/api/v1/home/recommended"])
  }
  @Test func recentUsesPersonalAlbumIDsAndCovers() async throws {
    let transport = HomeTransport(
      json:
        #"{"albums":[{"id":"saved-album","title":"Sunrise","artist":"The Orchard","artist_id":"saved-artist","category":"albums","track_count":2,"cover_sha":"abc","date":"","year":"","musicbrainz_release_id":null,"edition":null,"codecs":[],"compilation":false,"server_state":"in_library"}],"revision":"v1"}"#
    )
    let content = try await service(transport).home(.recent)
    #expect(content.albums.first?.id == "saved-album")
    #expect(content.albums.first?.coverSha == "abc")
    #expect(content.feed == nil)
    #expect(await transport.paths == ["/api/v1/home/recent"])
  }
  @Test func discoveryProviderStatesRemainDistinct() async throws {
    for state in ["preparing", "no_seeds", "unavailable", "ready"] {
      let transport = HomeTransport(
        json:
          "{\"artists\":[],\"albums\":[],\"songs\":[],\"source\":\"Chart\",\"unavailable\":\(state == "unavailable"),\"state\":\"\(state)\"}"
      )
      let result = try await service(transport).home(.popular)
      #expect(result.feed?.state?.rawValue == state)
      #expect(await transport.paths == ["/api/v1/home/popular"])
    }
  }
  @Test func expiredSessionIsNotReportedAsProviderOutage() async throws {
    for section in HomeSection.allCases {
      let transport = HomeTransport(
        status: 401, json: #"{"code":"unauthorized","detail":"Sign in"}"#)
      await #expect(throws: NativeFailure.authentication) {
        try await service(transport).home(section)
      }
    }
  }
  /// A sync that changed the library or its artwork makes Recently added
  /// stale with the other library sections (#168); the chart keeps its answer.
  @Test func recentlyAddedIsAskedForAgainWhenTheLibraryChanges() {
    #expect(HomeSection.followsLibrary == [.recent, .recommended, .mixes])
    #expect(!HomeSection.followsLibrary.contains(.popular))
  }
  /// A Recently added album the server has no stored cover for carries its
  /// own artwork address, which the card asks for.
  @Test func recentlyAddedCarriesAnAlbumsOwnArtworkAddress() async throws {
    let id = "0123456789abcdef01234567"
    let transport = HomeTransport(
      json:
        #"{"albums":[{"id":"\#(id)","title":"Sunrise","artist":"The Orchard","artist_id":"saved-artist","category":"albums","track_count":2,"cover_sha":null,"cover_url":"/api/v1/library-artwork/albums/\#(id)","date":"","year":"","musicbrainz_release_id":null,"edition":null,"codecs":[],"compilation":false,"server_state":"in_library"}],"revision":"v1"}"#
    )
    let album = try #require(try await service(transport).home(.recent).albums.first)
    #expect(album.coverSha == nil)
    #expect(album.libraryArtwork == LibraryArtwork(kind: .albums, id: id))
  }
  @Test func preparedMixesKeepPrivatePlaybackIdentityAndNeverUseRemoteStreaming() async throws {
    let transport = HomeTransport(
      json:
        #"{"mixes":[{"id":"edition","kind":"daily","title":"Daily blend","reason":"For you","day":"2026-10-16","tracks":[{"id":"mix:edition:0","title":"Discovery","artist":"Artist","server_state":"not_in_library","musicbrainz_recording_id":"recording","musicbrainz_release_id":"release"}]}],"preparing":true}"#
    )
    let content = try await service(transport).home(.mixes)
    let track = try #require(content.mixes?.first?.tracks.first)
    #expect(track.id == "mix:edition:0")
    #expect(track.musicbrainzReleaseId == "release")
    #expect(content.mixesPreparing == true)
    #expect(await transport.paths == ["/api/v1/home/mixes"])
    let playback = HomeTransport(
      json: #"{"url":"/api/v1/playback/files/mix:edition:0?sig=fixture","expires_in":21600}"#)
    _ = try await service(playback).resolve(track)
    #expect(await playback.paths.count == 1)
    #expect(await playback.paths.first?.contains("/api/v1/playback/") == true)
    await service(playback).prepareRemote([track])
    #expect(await playback.paths.count == 1)
  }
  @Test func mixActionsUseTheOwnedFileWithoutChangingPlaybackIdentity() throws {
    let track = try LibraryCodec.decoder().decode(
      Track.self,
      from: Data(
        #"{"id":"mix:edition:0","title":"Owned song","artist":"Artist","server_state":"in_library","library_track_id":"owned-file"}"#
          .utf8))
    #expect(track.actionTrack.id == "owned-file")
    #expect(track.id == "mix:edition:0")
    #expect(track.actionTrack.libraryTrackId == nil)
  }
  @Test func radioCardAndPagesDecodeAsOrdinaryMixEntries() async throws {
    let home = HomeTransport(
      json: #"{"mixes":[],"preparing":false,"radio":{"title":"Mark's radio","ready":20}}"#)
    let content = try await service(home).home(.mixes)
    #expect(content.radio?.title == "Mark's radio")
    #expect(content.radio?.ready == 20)
    let page = HomeTransport(
      json:
        #"{"id":"radio-1","title":"Mark's radio","preparing":true,"tracks":[{"id":"mix:radio-1:10","title":"Discovery","artist":"Artist","server_state":"not_in_library"}]}"#
    )
    let batch = try await service(page).radio(after: "mix:radio-1:9")
    #expect(batch.tracks.map { $0.id } == ["mix:radio-1:10"])
    #expect(batch.preparing)
    let path = try #require(await page.paths.first)
    #expect(path.hasPrefix("/api/v1/radio?"))
    #expect(path.contains("after=mix:radio-1:9") || path.contains("after=mix%3Aradio-1%3A9"))
    let feedback = HomeTransport(json: #"{"recorded":true}"#)
    try await service(feedback).radioFeedback(trackId: "mix:radio-1:10", action: "skip")
    #expect(await feedback.paths == ["/api/v1/radio/feedback"])
  }
  @Test func savedMixPlaybackKeepsItsQualifiedListenIdentity() async throws {
    let transport = HomeTransport(
      json:
        #"{"url":"/api/v1/playback/files/mix:restored:0?sig=fixture","history_track_id":"mix:restored:0"}"#
    )
    let api = service(transport)
    let track = try LibraryCodec.decoder().decode(
      Track.self,
      from: Data(
        #"{"id":"playlist-song","title":"Discovery","artist":"Artist","album":"Album","server_state":"not_in_library"}"#
          .utf8))
    _ = try await api.resolve(track)
    #expect(await api.qualifiedHistoryTrackID(track.id) == "mix:restored:0")
    #expect(await api.qualifiedHistoryTrackID("unrelated-song") == nil)
    #expect(await transport.paths == ["/api/v1/remote-playback"])
  }
  @Test func hidingAnEditionUsesItsScopedEndpoint() async throws {
    let transport = HomeTransport(json: #"{"hidden":true}"#)
    try await service(transport).hideMix("edition")
    #expect(await transport.paths == ["/api/v1/mixes/edition/hide"])
  }
}

struct CatalogueContractTests {
  private func service(_ json: String) -> NativeService {
    let origin = URL(string: "https://fixture.invalid")!
    return NativeService(
      server: origin, client: Client(serverURL: origin, transport: HomeTransport(json: json)),
      vault: HomeTokens())
  }
  @Test func releaseUsesPersonalFileIDButPreservesCataloguePositionsAndAvailability() async throws {
    let json =
      #"{"id":"release","title":"Album","artist":"Artist","tracks":[{"id":"catalogue-track","title":"Song","artist":"Artist","track_number":2,"disc_number":1,"server_state":"in_library","saved_track":{"id":"file-id","title":"Song","artist":"Artist","source_revision":"v7","server_state":"in_library"}},{"id":"requested","title":"Another","artist":"Artist","server_state":"requested","saved_track":null}]}"#
    let release = try await service(json).catalogueRelease(releaseId: "release")
    #expect(release.songs[0].id == "catalogue-track")
    #expect(release.songs[0].track.id == "file-id")
    #expect(release.songs[0].track.sourceRevision == "v7")
    #expect(release.songs[0].track.trackNumber == 2)
    #expect(release.songs[1].availability == "requested")
    #expect(release.songs[1].track.serverState == "not_in_library")
  }
  @Test func aLiveReleaseTypeTravelsFromTheCatalogueToTheRequest() async throws {
    let release = try await service(
      #"{"id":"release","title":"Concert Night","artist":"Artist","tracks":[{"id":"t1","title":"Song","artist":"Artist","duration":200,"track_number":1,"disc_number":1,"musicbrainz_release_id":"release","server_state":"not_in_library","recording_kind":["live"],"saved_track":null}]}"#
    ).catalogueRelease(releaseId: "release")
    let track = release.songs[0].track
    #expect(track.recordingKind == ["live"])
    let transport = HomeTransport(
      json: #"{"created":1,"existing":0,"skipped":0,"downloads_enabled":true}"#)
    let origin = URL(string: "https://fixture.invalid")!
    _ = try await NativeService(
      server: origin, client: Client(serverURL: origin, transport: transport), vault: HomeTokens()
    ).requestMusic([track])
    let sent = try #require(await transport.bodies.first)
    let body = try JSONSerialization.jsonObject(with: sent) as? [String: Any]
    let tracks = try #require(body?["tracks"] as? [[String: Any]])
    #expect(tracks[0]["recording_kind"] as? [String] == ["live"])
  }
  @Test func groupsAndEditionsPreservePagingAndEditionMetadata() async throws {
    let groups = try await service(
      #"{"offset":50,"total":51,"releases":[{"id":"group","title":"Album","date":"2020","type":"Album"}]}"#
    ).catalogueGroups(artistId: "artist", offset: 50)
    #expect(groups.offset == 50)
    #expect(groups.total == 51)
    let editions = try await service(
      #"{"offset":0,"total":1,"editions":[{"id":"edition","title":"Album","date":"2020-01-01","country":"AU","description":"Deluxe","status":"Official","formats":["CD"],"track_count":12}]}"#
    ).catalogueEditions(groupId: groups.releases[0].id)
    #expect(editions.editions[0].trackCount == 12)
    #expect(editions.editions[0].description == "Deluxe")
  }
}
