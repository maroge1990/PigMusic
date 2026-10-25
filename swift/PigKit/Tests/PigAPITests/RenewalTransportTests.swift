import Foundation
import HTTPTypes
import PigAPI
import Testing

@testable import PigDomain
@testable import PigSync

/// Holds callers until opened.
private actor Gate {
  private(set) var entered = 0
  private var opened = false
  private var waiting: [CheckedContinuation<Void, Never>] = []
  func pass() async {
    entered += 1
    guard !opened else { return }
    await withCheckedContinuation { waiting.append($0) }
  }
  func open() {
    opened = true
    for caller in waiting { caller.resume() }
    waiting = []
  }
}

/// A device token that takes as long as the test wants to read.
private actor SlowTokens: DeviceTokenStore {
  let gate = Gate()
  func read() async -> String? {
    await gate.pass()
    return "disposable-device-token"
  }
  func save(_ token: String) {}
  func clear() {}
}

private final class AnswerProtocol: URLProtocol, @unchecked Sendable {
  override class func canInit(with request: URLRequest) -> Bool { true }
  override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
  override func startLoading() {
    client?.urlProtocol(
      self,
      didReceive: HTTPURLResponse(
        url: request.url!, statusCode: 200, httpVersion: "HTTP/1.1",
        headerFields: ["Content-Type": "image/png"])!, cacheStoragePolicy: .notAllowed)
    client?.urlProtocol(self, didLoad: Data([9, 9]))
    client?.urlProtocolDidFinishLoading(self)
  }
  override func stopLoading() {}
}

/// API and artwork requests held up between taking a session and creating
/// their task, across several renewals, still start (a getter result kept
/// over two renewals once raised "Task created in a session that has been
/// invalidated" and ended the app; re-review of a442739). Each test waits
/// after renewing long enough for an invalidation to complete, so a session
/// invalidated too early would end the run rather than pass by timing.
@Suite(.serialized) struct RenewalTransportTests {
  private func waitUntilEntered(_ gate: Gate) async {
    while await gate.entered == 0 { await Task.yield() }
  }

  @Test func artworkWaitingForItsTokenSurvivesRenewals() async throws {
    let configuration = URLSessionConfiguration.ephemeral
    configuration.protocolClasses = [AnswerProtocol.self]
    let tokens = SlowTokens()
    let transport = ArtworkTransport(
      server: URL(string: "https://fixture.invalid:10000")!, vault: tokens,
      configuration: configuration)
    let request = try ArtworkRequest(sha: String(repeating: "a", count: 64))
    let fetching = Task { try await transport.fetch(request) }
    await waitUntilEntered(tokens.gate)
    for _ in 0..<3 { transport.renew() }
    try await Task.sleep(for: .milliseconds(300))
    await tokens.gate.open()
    #expect(try await fetching.value == Data([9, 9]))
    // The next one starts on the fresh session.
    #expect(try await transport.fetch(request) == Data([9, 9]))
  }

  @Test func anAPIRequestHeldBeforeSendingSurvivesRenewals() async throws {
    let sessions = RenewableSession {
      let configuration = URLSessionConfiguration.ephemeral
      configuration.protocolClasses = [AnswerProtocol.self]
      return URLSession(configuration: configuration)
    }
    let gate = Gate()
    nonisolated(unsafe) var used: [URLSession] = []
    let lock = NSLock()
    let transport = RenewableTransport { send in
      try await sessions.withSession { session in
        lock.withLock { used.append(session) }
        await gate.pass()
        return try await send(session)
      }
    }
    func send() async throws -> HTTPResponse {
      try await transport.send(
        HTTPRequest(method: .get, scheme: "https", authority: "fixture.invalid", path: "/x"),
        body: nil, baseURL: URL(string: "https://fixture.invalid")!, operationID: "fixture"
      ).0
    }
    let sending = Task { try await send() }
    await waitUntilEntered(gate)
    for _ in 0..<3 { sessions.renew() }
    #expect(sessions.retiredCount == 1)
    try await Task.sleep(for: .milliseconds(300))
    await gate.open()
    #expect(try await sending.value.status == .ok)
    #expect(sessions.retiredCount == 0)
    // A request after the renewals goes out on the newest session.
    #expect(try await send().status == .ok)
    let (held, next) = lock.withLock { (used[0], used[1]) }
    #expect(held !== next)
    #expect(await sessions.withSession { $0 } === next)
  }
}
