import Foundation
import HTTPTypes
import OpenAPIRuntime
import OpenAPIURLSession
import PigAPI
import PigDomain
import PigStore
import XCTest

@testable import PigSync

private actor FixtureTokens: DeviceTokenStore {
  var token: String?
  func read() -> String? { token }
  func save(_ value: String) { token = value }
  func clear() { token = nil }
}
private struct FixtureSession: ClientMiddleware {
  let tokens: FixtureTokens
  func intercept(
    _ request: HTTPRequest, body: HTTPBody?, baseURL: URL, operationID: String,
    next: (HTTPRequest, HTTPBody?, URL) async throws -> (HTTPResponse, HTTPBody?)
  ) async throws -> (HTTPResponse, HTTPBody?) {
    var request = request
    request.headerFields[HTTPField.Name("X-Forwarded-Proto")!] = "https"
    request.headerFields[HTTPField.Name("X-Pig-Request")!] = "1"
    if let token = await tokens.read() { request.headerFields[.authorization] = "Bearer " + token }
    return try await next(request, body, baseURL)
  }
}

final class NativeSyncContractTests: XCTestCase {
  func testPersonalBootstrapSQLiteDeltaAndMediaAgainstServer() async throws {
    guard let address = ProcessInfo.processInfo.environment["PIG_CONTRACT_SERVER"],
      let url = URL(string: address)
    else {
      throw XCTSkip("Set PIG_CONTRACT_SERVER for the isolated native sync journey")
    }
    let tokens = FixtureTokens()
    let client = Client(
      serverURL: url, transport: URLSessionTransport(),
      middlewares: [FixtureSession(tokens: tokens)])
    let service = NativeService(server: url, client: client, vault: tokens)
    let person = try await service.signIn(
      username: "member", password: "browser fixture password", deviceName: "Native sync fixture")
    XCTAssertEqual(person.username, "member")
    let store = try LibraryStore(path: ":memory:")
    let coordinator = SyncCoordinator(source: service, store: store)
    try await coordinator.refresh()
    let tracks = try await store.tracks()
    XCTAssertEqual(tracks.count, 2)
    let albums = try await store.albums()
    XCTAssertEqual(albums.count, 1)
    let recent = try await service.home(.recent)
    XCTAssertEqual(recent.albums.map(\.id), albums.map(\.id))
    XCTAssertEqual(recent.albums.first?.trackCount, 2)
    let albumSongs = try await store.tracks(albumId: albums[0].id)
    XCTAssertEqual(albumSongs.count, 2)
    let revision = try await store.revision()
    XCTAssertNotNil(revision)
    try await coordinator.refresh()
    let following = try await store.revision()
    XCTAssertEqual(revision, following)
    let search = try await store.tracks(query: "Song")
    XCTAssertEqual(search.count, 2)
    let media = try await service.playbackURL(trackId: tracks[0].id)
    XCTAssertEqual(media.host, url.host)
    XCTAssertEqual(media.port, url.port)
    XCTAssertTrue(media.path.contains("playback/files"))
    // Verify the signed server/file handoff, including byte-range delivery.
    var mediaRequest = URLRequest(url: media)
    mediaRequest.setValue("bytes=0-63", forHTTPHeaderField: "Range")
    mediaRequest.setValue("https", forHTTPHeaderField: "X-Forwarded-Proto")
    let (mediaBytes, mediaResponse) = try await URLSession.shared.data(for: mediaRequest)
    XCTAssertEqual((mediaResponse as? HTTPURLResponse)?.statusCode, 206)
    XCTAssertEqual(mediaBytes.count, 64)
    XCTAssertEqual(String(data: mediaBytes.prefix(4), encoding: .ascii), "RIFF")
    XCTAssertTrue(
      (mediaResponse as? HTTPURLResponse)?.value(forHTTPHeaderField: "Content-Range")?.hasPrefix(
        "bytes 0-63/") == true)
    let cachedTrack = try await store.track(id: tracks[0].id)
    XCTAssertEqual(cachedTrack?.id, tracks[0].id)
    // The fixture is deliberately HTTP-only. Read its real binary response directly here;
    // production HTTPS/bearer/error handling is covered separately by ArtworkTransportTests.
    let sha = try XCTUnwrap(tracks[0].coverSha)
    var artworkRequest = URLRequest(url: url.appendingPathComponent("api/v1/artwork/" + sha))
    artworkRequest.setValue(
      "Bearer " + (await tokens.read() ?? ""), forHTTPHeaderField: "Authorization")
    artworkRequest.setValue("https", forHTTPHeaderField: "X-Forwarded-Proto")
    let (imageData, imageResponse) = try await URLSession.shared.data(for: artworkRequest)
    XCTAssertEqual((imageResponse as? HTTPURLResponse)?.statusCode, 200)
    XCTAssertEqual(imageResponse.mimeType, "image/webp")
    let image = try ArtworkCache.thumbnail(imageData, width: 256)
    XCTAssertEqual(image.width, 256)
    let results = try await service.foundResults(query: "Song")
    let household = results.songs.filter { $0.serverState == "household_available" }
    XCTAssertEqual(results.songs.count - household.count, 2)
    XCTAssertEqual(household.count, 1)
    let shared = try XCTUnwrap(household.first)
    XCTAssertEqual(shared.serverState, "household_available")
    // Household songs stream without being added; playing never adds them.
    let householdURL = try await service.resolve(shared)
    XCTAssertTrue(householdURL.path.hasPrefix("/api/v1/playback/files/"))
    try await coordinator.refresh()
    let beforeAdd = try await store.count(.tracks)
    XCTAssertEqual(beforeAdd, 2)
    let marked = try await service.favourite(shared, value: true)
    XCTAssertTrue(marked)
    let favourite = try await service.favourite(shared)
    XCTAssertTrue(favourite)
    let unmarked = try await service.favourite(shared, value: false)
    XCTAssertFalse(unmarked)
    try await coordinator.refresh()
    let afterFavourite = try await store.count(.tracks)
    XCTAssertEqual(afterFavourite, 2)
    let added = try await service.addMembership(trackIds: [shared.id])
    XCTAssertEqual(added, 1)
    var savedShared = shared
    savedShared.serverState = "in_library"
    let sharedURL = try await service.resolve(savedShared)
    XCTAssertTrue(sharedURL.path.contains("playback/files"))
    try await coordinator.refresh()
    let afterAdd = try await store.count(.tracks)
    XCTAssertEqual(afterAdd, 3)
    let refreshed = try await service.foundResults(query: "Song")
    XCTAssertFalse(refreshed.songs.contains { $0.serverState == "household_available" })
    let requested = try await service.requestMusic([
      Track(
        id: "missing", title: "Missing fixture song", artist: "Fixture Artist", album: "Other album"
      )
    ])
    XCTAssertEqual(requested.created, 1)
    let requestPage = try await service.requests()
    XCTAssertEqual(requestPage.items.count, 1)
    XCTAssertEqual(requestPage.items[0].title, "Missing fixture song")
    XCTAssertEqual(requestPage.items[0].status, "waiting_for_sources")
    let cleared = try await service.clearFinishedRequests()
    XCTAssertEqual(cleared, 0)  // Active requests are retained.
    // Every playlist edit goes through the outbox's mutation ledger (#176).
    let edited = try await service.applyMutation(
      OfflineMutation(action: "create", name: "Native edits"))
    let editedId = try XCTUnwrap(edited.playlistId)
    _ = try await service.applyMutation(
      OfflineMutation(action: "append", playlistId: editedId, track: tracks[0]))
    var receipt = try await service.applyMutation(
      OfflineMutation(action: "append", playlistId: editedId, track: tracks[0]))
    var personalPlaylist = try await service.playlist(id: editedId)
    XCTAssertEqual((personalPlaylist.entries ?? []).count, 2)
    let oldRevision = try XCTUnwrap(receipt.revision)
    receipt = try await service.applyMutation(
      OfflineMutation(action: "rename", playlistId: editedId, name: "Renamed", revision: oldRevision))
    receipt = try await service.applyMutation(
      OfflineMutation(
        action: "reorder", playlistId: editedId, revision: try XCTUnwrap(receipt.revision),
        order: [1, 0]))
    personalPlaylist = try await service.playlist(id: editedId)
    XCTAssertEqual(personalPlaylist.name, "Renamed")
    do {
      _ = try await service.applyMutation(
        OfflineMutation(action: "rename", playlistId: editedId, name: "Stale", revision: oldRevision))
      XCTFail("Stale playlist writes must fail")
    } catch NativeFailure.changed {}
    receipt = try await service.applyMutation(
      OfflineMutation(
        action: "remove", playlistId: editedId, revision: try XCTUnwrap(receipt.revision),
        position: 0))
    personalPlaylist = try await service.playlist(id: editedId)
    XCTAssertEqual((personalPlaylist.entries ?? []).count, 1)
    _ = try await service.applyMutation(
      OfflineMutation(action: "delete", playlistId: editedId, revision: try XCTUnwrap(receipt.revision)))
    let offlineCreate = OfflineMutation(action: "create", name: "Native offline ledger")
    let createdReceipt = try await service.applyMutation(offlineCreate)
    let replayedReceipt = try await service.applyMutation(offlineCreate)
    XCTAssertEqual(createdReceipt.playlistId, replayedReceipt.playlistId)
    let createdId = try XCTUnwrap(createdReceipt.playlistId)
    let appendEdit = OfflineMutation(action: "append", playlistId: createdId, track: tracks[0])
    _ = try await service.applyMutation(appendEdit)
    _ = try await service.applyMutation(appendEdit)
    let replayedPlaylist = try await service.playlist(id: createdId)
    XCTAssertEqual(replayedPlaylist.entries?.count, 1)
    let matches = try await service.lookupSongs([
      tracks[0], Track(id: "unknown", title: "Unmatched title", artist: "Unmatched artist"),
    ])
    XCTAssertEqual(matches[0]?.id, tracks[0].id)
    XCTAssertNil(matches[1])
    let monitoredArtist = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    let artistSettings = try await service.configureArtist(
      id: monitoredArtist, kinds: ["album", "ep"], monitor: false, request: false)
    XCTAssertEqual(artistSettings.kinds, ["album", "ep"])
    let cancelledArtist = try await service.cancelArtist(id: monitoredArtist)
    XCTAssertEqual(cancelledArtist.status, "cancelled")
    let qualified = ListeningEvent(
      eventId: UUID().uuidString, trackId: tracks[0].id,
      playedAt: Int(Date().timeIntervalSince1970), seconds: 30)
    try await service.recordListens([qualified])
    try await service.recordListens([qualified])  // Replay is idempotent on the server.
    let export: [String: Any] = [
      "Tracks": [
        "1": [
          "Track ID": 1, "Name": tracks[0].title, "Artist": tracks[0].artist,
          "Album": tracks[0].album ?? "",
        ]
      ],
      "Playlists": [
        [
          "Name": "Fixture import", "Playlist Persistent ID": "NATIVE-FIXTURE",
          "Playlist Items": [["Track ID": 1]],
        ]
      ],
    ]
    let xml = try PropertyListSerialization.data(fromPropertyList: export, format: .xml, options: 0)
    let preview = try await service.previewImport(xml)
    XCTAssertEqual(preview.tracks, 1)
    XCTAssertEqual(preview.saved, 1)
    let imported = try await service.importApple(xml)
    XCTAssertEqual(imported.playlists, 1)
    _ = try await service.applyMutation(
      OfflineMutation(
        action: "preferences",
        preferences: AccountPreferences(appearance: "dark", defaultTab: "albums")))
    let settings = try await service.preferences()
    XCTAssertEqual(settings.appearance, "dark")
    let devices = try await service.sessions()
    XCTAssertFalse(devices.isEmpty)
    let quality = try await service.quality()
    XCTAssertFalse(quality.options.isEmpty)
    let manifest = try await service.deviceCopy(
      trackId: tracks[0].id, quality: "original", create: true)
    XCTAssertEqual(manifest.state, "ready")
    XCTAssertGreaterThan(manifest.bytes, 0)
    XCTAssertEqual(manifest.sha256?.count, 64)
    _ = try await service.signOut()
    let token = await tokens.read()
    XCTAssertNil(token)
  }
}
