import Foundation
import HTTPTypes
import OpenAPIRuntime
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

/// A body whose first bytes arrive at once and the rest when the test lets
/// them, so a renewal can happen while it is still arriving.
private final class HeldBody: @unchecked Sendable {
  static let shared = HeldBody()
  static let first = Data(repeating: 1, count: 1024)
  static let rest = Data(repeating: 2, count: 4096)
  private let condition = NSCondition()
  private var started = false
  private var released = false
  func reset() {
    condition.withLock {
      started = false
      released = false
    }
  }
  var hasStarted: Bool { condition.withLock { started } }
  func markStarted() { condition.withLock { started = true } }
  func release() {
    condition.withLock {
      released = true
      condition.broadcast()
    }
  }
  /// Whether the rest may be sent (false after ten seconds).
  func waitForRelease() -> Bool {
    condition.withLock {
      let deadline = Date(timeIntervalSinceNow: 10)
      while !released { if !condition.wait(until: deadline) { break } }
      return released
    }
  }
}

private final class HeldBodyProtocol: URLProtocol, @unchecked Sendable {
  override class func canInit(with request: URLRequest) -> Bool { true }
  override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
  override func startLoading() {
    let total = HeldBody.first.count + HeldBody.rest.count
    client?.urlProtocol(
      self,
      didReceive: HTTPURLResponse(
        url: request.url!, statusCode: 200, httpVersion: "HTTP/1.1",
        headerFields: ["Content-Type": "image/png", "Content-Length": String(total)])!,
      cacheStoragePolicy: .notAllowed)
    client?.urlProtocol(self, didLoad: HeldBody.first)
    HeldBody.shared.markStarted()
    DispatchQueue.global().async { [self] in
      guard HeldBody.shared.waitForRelease() else {
        return client?.urlProtocol(self, didFailWithError: URLError(.timedOut)) ?? ()
      }
      client?.urlProtocol(self, didLoad: HeldBody.rest)
      client?.urlProtocolDidFinishLoading(self)
    }
  }
  override func stopLoading() {}
}

/// Records the sessions that became invalid.
private final class Invalidations: NSObject, URLSessionDelegate, @unchecked Sendable {
  private let lock = NSLock()
  private var ended: Set<ObjectIdentifier> = []
  func urlSession(_ session: URLSession, didBecomeInvalidWithError error: Error?) {
    _ = lock.withLock { ended.insert(ObjectIdentifier(session)) }
  }
  func contains(_ session: URLSession) -> Bool {
    lock.withLock { ended.contains(ObjectIdentifier(session)) }
  }
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

  private func waitUntilTheBodyHasStarted() async throws {
    for _ in 0..<500 where !HeldBody.shared.hasStarted {
      try await Task.sleep(for: .milliseconds(10))
    }
    #expect(HeldBody.shared.hasStarted)
  }

  /// An artwork body still arriving when the connections are renewed
  /// arrives whole: the replaced session lets its running task finish.
  @Test func anArtworkBodyArrivingDuringRenewalsArrivesWhole() async throws {
    HeldBody.shared.reset()
    let configuration = URLSessionConfiguration.ephemeral
    configuration.protocolClasses = [HeldBodyProtocol.self]
    let tokens = SlowTokens()
    await tokens.gate.open()
    let transport = ArtworkTransport(
      server: URL(string: "https://fixture.invalid:10000")!, vault: tokens,
      configuration: configuration)
    let request = try ArtworkRequest(sha: String(repeating: "a", count: 64))
    let fetching = Task { try await transport.fetch(request) }
    try await waitUntilTheBodyHasStarted()
    for _ in 0..<3 { transport.renew() }
    try await Task.sleep(for: .milliseconds(300))
    HeldBody.shared.release()
    #expect(try await fetching.value == HeldBody.first + HeldBody.rest)
  }

  /// An API response streams its body after `send` returns, so after the
  /// borrow has ended. Renewals meanwhile retire that session without
  /// cutting the body short; it is invalidated once the body has arrived.
  @Test func anAPIBodyStreamingAfterTheBorrowSurvivesRenewals() async throws {
    HeldBody.shared.reset()
    let invalidations = Invalidations()
    let sessions = RenewableSession {
      let configuration = URLSessionConfiguration.ephemeral
      configuration.protocolClasses = [HeldBodyProtocol.self]
      return URLSession(configuration: configuration, delegate: invalidations, delegateQueue: nil)
    }
    let lock = NSLock()
    nonisolated(unsafe) var used: URLSession?
    let transport = RenewableTransport { send in
      try await sessions.withSession { session in
        lock.withLock { used = session }
        return try await send(session)
      }
    }
    let (response, body) = try await transport.send(
      HTTPRequest(method: .get, scheme: "https", authority: "fixture.invalid", path: "/x"),
      body: nil, baseURL: URL(string: "https://fixture.invalid")!, operationID: "fixture")
    #expect(response.status == .ok)
    let session = try #require(lock.withLock { used })
    for _ in 0..<3 { sessions.renew() }
    #expect(sessions.retiredCount == 0)
    try await Task.sleep(for: .milliseconds(300))
    // Still finishing its body, so not invalidated yet.
    #expect(!invalidations.contains(session))
    HeldBody.shared.release()
    let bytes = try await [UInt8](collecting: try #require(body), upTo: 1 << 20)
    #expect(Data(bytes) == HeldBody.first + HeldBody.rest)
    for _ in 0..<100 where !invalidations.contains(session) {
      try await Task.sleep(for: .milliseconds(20))
    }
    #expect(invalidations.contains(session))
  }

}
