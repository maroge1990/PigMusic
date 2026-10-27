import Foundation
import HTTPTypes
import OpenAPIRuntime
import PigAPI
import PigDomain
import Testing

@testable import PigSync

private actor BootstrapTokens: DeviceTokenStore {
  func read() -> String? { "fixture" }
  func save(_ value: String) {}
  func clear() {}
}

/// Answers each request with the next scripted reply for its path (and
/// cursor), recording what was asked.
private actor ScriptedTransport: ClientTransport {
  var replies: [String: [(Int, String)]]
  var asked: [String] = []
  init(_ replies: [String: [(Int, String)]]) { self.replies = replies }
  func send(_ request: HTTPRequest, body: HTTPBody?, baseURL: URL, operationID: String) async throws
    -> (HTTPResponse, HTTPBody?)
  {
    let components = URLComponents(string: request.path ?? "")
    let cursor = components?.queryItems?.first { $0.name == "cursor" }?.value
    let since = components?.queryItems?.first { $0.name == "since" }?.value
    let key = (components?.path ?? "") + (cursor.map { "?" + $0 } ?? since.map { "@" + $0 } ?? "")
    asked.append(key)
    guard var queue = replies[key], !queue.isEmpty else {
      Issue.record("Unexpected request \(key)")
      return (HTTPResponse(status: .notFound), nil)
    }
    let (status, json) = queue.removeFirst()
    replies[key] = queue
    var response = HTTPResponse(status: .init(code: status))
    response.headerFields[.contentType] = "application/json"
    return (response, HTTPBody(json))
  }
}

private func page(_ ids: [String], revision: String, next: String? = nil) -> String {
  let items = ids.map { #"{"id":"\#($0)","title":"Song \#($0)","artist":"Artist"}"# }
  let cursor = next.map { #""\#($0)""# } ?? "null"
  return
    #"{"items":[\#(items.joined(separator: ","))],"total":\#(ids.count),"revision":"\#(revision)","next_cursor":\#(cursor)}"#
}
private let empty = #"{"items":[],"total":0,"revision":"r1","next_cursor":null}"#
private let noPlaylists = #"{"playlists":[]}"#
private func delta(_ revision: String, deletes: [String] = []) -> String {
  let deleted = deletes.map { #""\#($0)""# }.joined(separator: ",")
  let none = #""artists":[],"albums":[],"playlists":[]"#
  return
    #"{"revision":"\#(revision)","upserts":{\#(none),"tracks":[]},"deletes":{\#(none),"tracks":[\#(deleted)]}}"#
}

struct LibraryBootstrapTests {
  private func service(_ transport: ScriptedTransport) -> NativeService {
    let origin = URL(string: "https://fixture.invalid")!
    return NativeService(
      server: origin, client: Client(serverURL: origin, transport: transport),
      vault: BootstrapTokens(), pageRetryPause: .milliseconds(1))
  }

  @Test func aFailedPageIsAskedForAgainFromItsCursor() async throws {
    let transport = ScriptedTransport([
      "/api/v1/library/tracks": [(200, page(["1"], revision: "r1", next: "c1"))],
      "/api/v1/library/tracks?c1": [
        (503, #"{"detail":"busy","code":"busy"}"#), (503, #"{"detail":"busy","code":"busy"}"#),
        (200, page(["2"], revision: "r1")),
      ],
      "/api/v1/library/albums": [(200, empty)],
      "/api/v1/library/artists": [(200, empty)],
      "/api/v1/playlists": [(200, noPlaylists)],
      "/api/v1/library/changes@r1": [(200, delta("r1"))],
    ])
    let snapshot = try await service(transport).bootstrap()
    #expect(Set(snapshot.tracks.map(\.id)) == ["1", "2"])
    #expect(snapshot.revision == "r1")
    // The first page is never fetched again.
    #expect(await transport.asked.filter { $0 == "/api/v1/library/tracks" }.count == 1)
  }

  @Test func aPageFailingThreeTimesFailsTheRefresh() async throws {
    let busy = (503, #"{"detail":"busy","code":"busy"}"#)
    let transport = ScriptedTransport([
      "/api/v1/library/tracks": [(200, page(["1"], revision: "r1", next: "c1"))],
      "/api/v1/library/tracks?c1": [busy, busy, busy],
    ])
    await #expect(throws: NativeFailure.unavailable("Unable to sync songs.")) {
      try await service(transport).bootstrap()
    }
  }

  @Test func laterCollectionsMayBeNewerAndTheDeltaCoversThem() async throws {
    let transport = ScriptedTransport([
      "/api/v1/library/tracks": [(200, page(["1", "2"], revision: "r1"))],
      // A download finished meanwhile: albums are a revision later.
      "/api/v1/library/albums": [
        (200, #"{"items":[],"total":0,"revision":"r2","next_cursor":null}"#)
      ],
      "/api/v1/library/artists": [
        (200, #"{"items":[],"total":0,"revision":"r2","next_cursor":null}"#)
      ],
      "/api/v1/playlists": [(200, noPlaylists)],
      "/api/v1/library/changes@r1": [(200, delta("r2", deletes: ["2"]))],
    ])
    let snapshot = try await service(transport).bootstrap()
    #expect(snapshot.tracks.map(\.id) == ["1"])
    #expect(snapshot.revision == "r2")
  }

  @Test func anExpiredCursorRestartsOnlyItsCollection() async throws {
    let transport = ScriptedTransport([
      "/api/v1/library/tracks": [(200, page(["1"], revision: "r1"))],
      "/api/v1/library/albums": [
        (200, #"{"items":[],"total":2,"revision":"r1","next_cursor":"a1"}"#),
        (200, #"{"items":[],"total":0,"revision":"r2","next_cursor":null}"#),
      ],
      "/api/v1/library/albums?a1": [(409, #"{"detail":"Pagination changed.","code":"conflict"}"#)],
      "/api/v1/library/artists": [(200, empty)],
      "/api/v1/playlists": [(200, noPlaylists)],
      "/api/v1/library/changes@r1": [(200, delta("r2"))],
    ])
    let snapshot = try await service(transport).bootstrap()
    #expect(snapshot.tracks.map(\.id) == ["1"])
    #expect(await transport.asked.filter { $0 == "/api/v1/library/tracks" }.count == 1)
    #expect(await transport.asked.filter { $0 == "/api/v1/library/albums" }.count == 2)
  }

  @Test func aSignInFailureIsNeverRetried() async throws {
    let transport = ScriptedTransport([
      "/api/v1/library/tracks": [
        (401, #"{"detail":"authentication_required","code":"authentication_required"}"#)
      ]
    ])
    await #expect(throws: NativeFailure.authentication) {
      try await service(transport).bootstrap()
    }
    #expect(await transport.asked == ["/api/v1/library/tracks"])
  }
}
