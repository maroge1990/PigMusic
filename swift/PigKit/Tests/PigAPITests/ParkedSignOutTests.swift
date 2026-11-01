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
/// Servers by host: unreachable until connected; a held one answers only
/// when released.
private actor Servers: ClientTransport {
  var reachable: Set<String> = []
  var held = false
  var ended: [String] = []
  func connect(_ host: String) { reachable.insert(host) }
  func hold() { held = true }
  func release() { held = false }
  func send(_ request: HTTPRequest, body: HTTPBody?, baseURL: URL, operationID: String) async throws
    -> (HTTPResponse, HTTPBody?)
  {
    while held { await Task.yield() }
    guard let host = baseURL.host(), reachable.contains(host) else { throw Unreachable() }
    ended.append(host + " " + (request.headerFields[.authorization] ?? ""))
    var response = HTTPResponse(status: .ok)
    response.headerFields[.contentType] = "application/json"
    return (response, HTTPBody(#"{"ok":true}"#))
  }
}
private func service(
  _ host: String, token: String, servers: Servers, parked: ParkedSessions
) -> (NativeService, Tokens) {
  let origin = URL(string: "https://\(host)")!
  let vault = Tokens(token)
  return (
    NativeService(
      server: origin, client: Client(serverURL: origin, transport: servers), vault: vault,
      parked: parked),
    vault
  )
}

@Test func signingOutOfflineSignsOutNowAndEndsTheSessionLater() async throws {
  // D-55, #97: offline, the iPhone stayed signed in and kept its token.
  let servers = Servers()
  let parked = ParkedSessions(store: Tokens(nil), transport: servers)
  let (native, vault) = service("a.invalid", token: "one", servers: servers, parked: parked)
  #expect(try await native.signOut() == false)
  #expect(await vault.token == nil)
  #expect(await parked.count == 1)
  await parked.end()  // Still offline: it stays parked.
  #expect(await parked.count == 1)
  await servers.connect("a.invalid")
  await parked.end()  // Back in reach: ended by its own token.
  #expect(await servers.ended == ["a.invalid Bearer one"])
  #expect(await parked.count == 0)
}

@Test func aSessionParkedWhileOthersAreEndedIsKept() async throws {
  // Codex round 1 (high): ending replaced the whole list with its stale
  // copy, dropping a session parked while its requests ran.
  let servers = Servers()
  let store = Tokens(nil)
  let parked = ParkedSessions(store: store, transport: servers)
  try await parked.park(server: "https://a.invalid", token: "one")
  await servers.connect("a.invalid")
  await servers.hold()
  let ending = Task { await parked.end() }
  for _ in 0..<50 { await Task.yield() }
  try await parked.park(server: "https://a.invalid", token: "two")
  await servers.release()
  await ending.value
  #expect(await servers.ended == ["a.invalid Bearer one"])
  #expect(await parked.count == 1)
  // What was saved is the same: a restart still ends "two".
  let restarted = ParkedSessions(store: store, transport: servers)
  await restarted.end()
  #expect(await servers.ended == ["a.invalid Bearer one", "a.invalid Bearer two"])
}

@Test func offlineSignOutsOnTwoServersAreBothEnded() async throws {
  // Codex round 1 (medium): only the last server's sessions were remembered.
  let servers = Servers()
  let parked = ParkedSessions(store: Tokens(nil), transport: servers)
  _ = try await service("a.invalid", token: "one", servers: servers, parked: parked).0.signOut()
  _ = try await service("b.invalid", token: "two", servers: servers, parked: parked).0.signOut()
  await servers.connect("a.invalid")
  await servers.connect("b.invalid")
  await parked.end()
  #expect(Set(await servers.ended) == ["a.invalid Bearer one", "b.invalid Bearer two"])
  #expect(await parked.count == 0)
}
