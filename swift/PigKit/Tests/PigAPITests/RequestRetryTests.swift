import Foundation
import HTTPTypes
import OpenAPIRuntime
import PigAPI
import PigDomain
import PigStore
import Testing

@testable import PigSync

private actor Tokens: DeviceTokenStore {
  func read() -> String? { "fixture" }
  func save(_ value: String) {}
  func clear() {}
}
/// A server that answers every request with `status`.
private struct StatusTransport: ClientTransport {
  let status: Int
  func send(_ request: HTTPRequest, body: HTTPBody?, baseURL: URL, operationID: String) async throws
    -> (HTTPResponse, HTTPBody?)
  {
    var response = HTTPResponse(status: .init(code: status))
    response.headerFields[.contentType] = "application/json"
    let json =
      status == 200
      ? #"{"created":1,"existing":0,"skipped":0,"downloads_enabled":true}"#
      : #"{"detail":"No.","code":"refused"}"#
    return (response, HTTPBody(json))
  }
}

/// Codex round 1 on #206: a server restart (5xx) or a rate limit must keep a
/// queued request for the next try; only a real refusal (4xx) drops it.
@MainActor struct RequestRetryTests {
  private let song = Track(id: "song", title: "Song", artist: "Artist")

  private func outcome(after status: Int) async throws -> (RequestOutbox.Report, [String]) {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: root) }
    let store = try LibraryStore(path: root.appendingPathComponent("requests.sqlite").path)
    let origin = URL(string: "https://fixture.invalid")!
    let service = NativeService(
      server: origin, client: Client(serverURL: origin, transport: StatusTransport(status: status)),
      vault: Tokens())
    try await RequestOutbox.add(song, to: store)
    let outcome = try await RequestOutbox.send(store: store, isCurrent: { true }) {
      try await service.requestMusic($0)
    }
    return (outcome, await RequestOutbox.pending(store).map(\.id))
  }

  @Test(arguments: [429, 500, 502, 503, 504])
  func aServerThatCanAnswerLaterKeepsTheRequest(status: Int) async throws {
    let (outcome, pending) = try await outcome(after: status)
    #expect(outcome.stop == .offline && outcome.refused.isEmpty)
    #expect(pending == ["song"])
  }

  @Test(arguments: [400, 403, 404, 409, 410, 422])
  func aRefusalDropsTheRequest(status: Int) async throws {
    let (outcome, pending) = try await outcome(after: status)
    #expect(outcome.refused == ["song"] && outcome.stop == .done)
    #expect(pending.isEmpty)
  }

  @Test func anAcceptedRequestLeavesTheOutbox() async throws {
    let (outcome, pending) = try await outcome(after: 200)
    #expect(outcome.requested == ["song"] && outcome.stop == .done)
    #expect(pending.isEmpty)
  }
}
