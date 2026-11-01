import Foundation
import HTTPTypes
import OpenAPIRuntime
import PigAPI
import Testing

@testable import PigSync

private actor Tokens: DeviceTokenStore {
  var token: String?
  init(_ token: String?) { self.token = token }
  func read() -> String? { token }
  func save(_ value: String) { token = value }
  func clear() { token = nil }
}
private struct Unreachable: Error {}
/// No server in reach, or one that answers every request with `status`.
private actor Server: ClientTransport {
  var reachable: Bool
  var tokens: [String] = []
  init(reachable: Bool) { self.reachable = reachable }
  func connect() { reachable = true }
  func send(_ request: HTTPRequest, body: HTTPBody?, baseURL: URL, operationID: String) async throws
    -> (HTTPResponse, HTTPBody?)
  {
    guard reachable else { throw Unreachable() }
    tokens.append(request.headerFields[.authorization] ?? "")
    var response = HTTPResponse(status: .ok)
    response.headerFields[.contentType] = "application/json"
    return (response, HTTPBody(#"{"ok":true}"#))
  }
}

@Test func signingOutOfflineSignsOutNowAndEndsTheSessionLater() async throws {
  // D-55, #97: offline, the iPhone stayed signed in and kept its token.
  let origin = URL(string: "https://fixture.invalid")!
  let server = Server(reachable: false)
  let vault = Tokens("session-one")
  let parked = Tokens(nil)
  let service = NativeService(
    server: origin, client: Client(serverURL: origin, transport: server), vault: vault,
    parked: parked)
  #expect(try await service.signOut() == false)
  #expect(await vault.token == nil)
  #expect(await parked.token == "session-one")
  // Still offline: the session stays parked.
  await NativeService.endParkedSessions(
    server: origin.absoluteString, parked: parked, transport: server)
  #expect(await parked.token == "session-one")
  // Back in reach: that session, and only it, is ended by its own token.
  await server.connect()
  await NativeService.endParkedSessions(
    server: origin.absoluteString, parked: parked, transport: server)
  #expect(await server.tokens == ["Bearer session-one"])
  #expect(await parked.token == nil)
}
