import Foundation
import HTTPTypes
import OpenAPIRuntime
import PigAPI
import PigDomain
import Testing

@testable import PigSync

private actor ActionTokens: DeviceTokenStore {
  func read() -> String? { "fixture" }
  func save(_ value: String) {}
  func clear() {}
}
private actor ActionTransport: ClientTransport {
  let json: String
  let status: Int
  var sent: [(String, Data)] = []
  init(_ json: String, status: Int = 200) {
    self.json = json
    self.status = status
  }
  func send(_ request: HTTPRequest, body: HTTPBody?, baseURL: URL, operationID: String) async throws
    -> (HTTPResponse, HTTPBody?)
  {
    var data = Data()
    if let body { for try await chunk in body { data.append(contentsOf: chunk) } }
    sent.append((request.path ?? "", data))
    var response = HTTPResponse(status: .init(code: status))
    response.headerFields[.contentType] = "application/json"
    return (response, HTTPBody(json))
  }
}
struct PersonalActionTests {
  private func service(_ transport: ActionTransport) -> NativeService {
    let origin = URL(string: "https://fixture.invalid")!
    return NativeService(
      server: origin, client: Client(serverURL: origin, transport: transport), vault: ActionTokens()
    )
  }
  @Test func explicitFavouriteFalseAndRequestPreserveEditionMetadata() async throws {
    var track = Track(
      id: "catalogue", title: "Song", artist: "Artist", album: "Album", duration: 180)
    track.musicbrainzReleaseId = "edition"
    track.musicbrainzRecordingId = "recording"
    track.trackNumber = 2
    track.discNumber = 1
    track.year = .number(2020)
    let transport = ActionTransport(#"{"favourited":false}"#)
    #expect(try await service(transport).favourite(track, value: false) == false)
    let sent = await transport.sent
    #expect(sent.count == 1)
    let body = try #require(JSONSerialization.jsonObject(with: sent[0].1) as? [String: Any])
    #expect(body["favourite"] as? Bool == false)
    let metadata = try #require(body["track"] as? [String: Any])
    #expect(metadata["musicbrainz_release_id"] as? String == "edition")
    #expect(metadata["album_artist"] as? String == "Artist")
    #expect(metadata["year"] as? String == "2020")
    let requests = ActionTransport(
      #"{"created":1,"existing":0,"skipped":0,"downloads_enabled":false}"#)
    let result = try await service(requests).requestMusic([track])
    #expect(result.created == 1 && !result.downloadsEnabled)
    #expect(await requests.sent.count == 1)
  }
  @Test func playbackAddressesFetchedAheadSkipTheRoundTripWhenPlaying() async throws {
    var saved = Track(id: "saved", title: "Song", artist: "Artist")
    saved.serverState = "in_library"
    var remote = Track(id: "remote", title: "Other", artist: "Artist")
    remote.serverState = "not_in_library"
    let mix = Track(id: "mix:edition:0", title: "Mixed", artist: "Artist")
    let transport = ActionTransport(
      #"{"urls":{"saved":"/api/v1/playback/files/saved?exp=1&sid=s&sig=x","foreign":"https://elsewhere.invalid/x"},"expires_in":21600}"#
    )
    let native = service(transport)
    await native.preparePlayback([saved, remote, mix, saved])
    var sent = await transport.sent
    #expect(sent.map(\.0) == ["/api/v1/playback-urls"])
    let body = try #require(JSONSerialization.jsonObject(with: sent[0].1) as? [String: Any])
    // Only server files: remote and mix songs get their own address per play.
    #expect(body["track_ids"] as? [String] == ["saved"])
    let url = try await native.resolve(saved)
    #expect(
      url.absoluteString
        == "https://fixture.invalid/api/v1/playback/files/saved?exp=1&sid=s&sig=x")
    // Playing used the address fetched ahead; asking again sends nothing.
    await native.preparePlayback([saved])
    sent = await transport.sent
    #expect(sent.count == 1)
  }
  @Test func mutationsDoNotRetryUnconfirmedResponsesOrHideAuthentication() async throws {
    let track = Track(id: "one", title: "Song", artist: "Artist")
    let transport = ActionTransport(#"{"code":"unavailable","detail":"Unavailable"}"#, status: 503)
    do {
      _ = try await service(transport).requestMusic([track])
      Issue.record("Expected the server to be busy")
    } catch is ServerBusy {}  // Kept by the request outbox for later (#206).
    #expect(await transport.sent.count == 1)
    let expired = ActionTransport(#"{"code":"unauthorized","detail":"Sign in"}"#, status: 401)
    await #expect(throws: NativeFailure.authentication) {
      try await service(expired).addMembership(trackIds: ["one"])
    }
    #expect(await expired.sent.count == 1)
  }
  @Test func householdReleaseUsesPhysicalFileWithoutClaimingMembership() async throws {
    let transport = ActionTransport(
      #"{"id":"release","title":"Album","artist":"Artist","tracks":[{"id":"catalogue","title":"Song","artist":"Artist","server_state":"household_available","household_track":{"id":"physical","title":"Song","artist":"Artist","server_state":"household_available","source_revision":"v1"}}]}"#
    )
    let release = try await service(transport).catalogueRelease(releaseId: "release")
    #expect(release.songs[0].id == "catalogue")
    #expect(release.songs[0].track.id == "physical")
    #expect(release.songs[0].track.serverState == "household_available")
  }
}

/// Answers each path with its own JSON, recording what was sent.
private actor RoutedTransport: ClientTransport {
  let answers: [String: String]
  var sent: [(String, Data)] = []
  init(_ answers: [String: String]) { self.answers = answers }
  func send(_ request: HTTPRequest, body: HTTPBody?, baseURL: URL, operationID: String) async throws
    -> (HTTPResponse, HTTPBody?)
  {
    var data = Data()
    if let body { for try await chunk in body { data.append(contentsOf: chunk) } }
    let path = request.path ?? ""
    sent.append((path, data))
    var response = HTTPResponse(status: .ok)
    response.headerFields[.contentType] = "application/json"
    return (response, HTTPBody(answers.first { path.hasSuffix($0.key) }?.value ?? "{}"))
  }
}

/// #214: Remove from library is one action: the server's file rules still
/// apply, but the person sees no preview and confirms nothing.
@Test func removeFromLibraryConfirmsTheServersPreviewWithoutShowingIt() async throws {
  let token = String(repeating: "c", count: 64)
  let transport = RoutedTransport([
    "/library/removal-preview":
      #"{"delete_count":0,"keep_count":1,"confirmation":"\#(token)","#
      + #""items":[{"track_id":"song","delete_file":false,"kept_for":["Someone"]}]}"#,
    "/library/remove": #"{"id":"op","removed":1,"deleted":0,"kept":1,"undo_until":0}"#,
  ])
  let origin = URL(string: "https://fixture.invalid")!
  let service = NativeService(
    server: origin, client: Client(serverURL: origin, transport: transport), vault: ActionTokens())
  let removed = try await service.removeFromLibrary(ids: ["song"])
  #expect(removed == 1)
  let sent = await transport.sent
  #expect(sent.map(\.0) == ["/api/v1/library/removal-preview", "/api/v1/library/remove"])
  let body = try #require(JSONSerialization.jsonObject(with: sent[1].1) as? [String: Any])
  #expect(body["confirmation"] as? String == token)
  #expect(body["track_ids"] as? [String] == ["song"])
}
