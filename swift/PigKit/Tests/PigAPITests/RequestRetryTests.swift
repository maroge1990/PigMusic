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
/// A server that answers every request with `status` and `body` (a refusal's
/// JSON when nil; no body at all when empty).
private struct StatusTransport: ClientTransport {
  let status: Int
  var body: String?
  func send(_ request: HTTPRequest, body: HTTPBody?, baseURL: URL, operationID: String) async throws
    -> (HTTPResponse, HTTPBody?)
  {
    var response = HTTPResponse(status: .init(code: status))
    let json =
      self.body
      ?? (status == 200
        ? #"{"created":1,"existing":0,"skipped":0,"downloads_enabled":true}"#
        : #"{"detail":"No.","code":"refused"}"#)
    guard !json.isEmpty else { return (response, nil) }
    response.headerFields[.contentType] = "application/json"
    return (response, HTTPBody(json))
  }
}

/// The server's answers for a busy database and an unexpected error (#239).
private enum Answer {
  static let busy = #"{"detail":"The server is busy. Try again shortly.","code":"server_busy"}"#
  static let unconfirmed =
    #"{"detail":"The server was busy. Check whether this was saved before trying again.","code":"busy_unconfirmed"}"#
  static let serverError =
    #"{"detail":"Something went wrong on the server.","code":"server_error","request_id":"0123456789abcdef"}"#
  static let otherBusy = #"{"detail":"Restarting.","code":"restarting"}"#
  static let proxyPage = "<html><body>500 Internal Server Error</body></html>"
  static let otherError = #"{"detail":"Oops.","code":"something_else"}"#
}

/// A server that answers the first request with `first`, and every later
/// one as accepted.
private final class RecoveringTransport: ClientTransport, @unchecked Sendable {
  let first: StatusTransport
  private let lock = NSLock()
  private var sent = 0
  init(_ first: StatusTransport) { self.first = first }
  func send(_ request: HTTPRequest, body: HTTPBody?, baseURL: URL, operationID: String) async throws
    -> (HTTPResponse, HTTPBody?)
  {
    let count = lock.withLock {
      sent += 1
      return sent
    }
    let answer = count == 1 ? first : StatusTransport(status: 200)
    return try await answer.send(request, body: body, baseURL: baseURL, operationID: operationID)
  }
}

/// Codex round 1 on #206: a server restart (5xx) or a rate limit must keep a
/// queued request for the next try; only a real refusal (4xx) drops it.
/// #239: a server error (500) is not busy and drops it with its reference.
@MainActor struct RequestRetryTests {
  private let song = Track(id: "song", title: "Song", artist: "Artist")

  private func service(_ transport: StatusTransport) -> NativeService {
    let origin = URL(string: "https://fixture.invalid")!
    return NativeService(
      server: origin, client: Client(serverURL: origin, transport: transport), vault: Tokens())
  }

  private func outcome(after status: Int, body: String? = nil) async throws -> (
    RequestOutbox.Report, [String]
  ) {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: root) }
    let store = try LibraryStore(path: root.appendingPathComponent("requests.sqlite").path)
    let service = service(StatusTransport(status: status, body: body))
    try await RequestOutbox.add(song, to: store)
    let outcome = try await RequestOutbox.send(store: store, isCurrent: { true }) {
      try await service.requestMusic($0)
    }
    return (outcome, await RequestOutbox.pending(store).map(\.id))
  }

  private func failure(_ status: Int, _ body: String?) async -> (any Error)? {
    do {
      _ = try await service(StatusTransport(status: status, body: body)).requestMusic([song])
      return nil
    } catch { return error }
  }

  @Test(arguments: [
    (429, nil), (502, nil), (503, Answer.busy), (503, Answer.otherBusy), (504, nil),
    (503, Answer.unconfirmed), (500, ""), (500, Answer.proxyPage), (500, Answer.otherError),
  ] as [(Int, String?)])
  func aServerThatCanAnswerLaterKeepsTheRequest(status: Int, body: String?) async throws {
    let (outcome, pending) = try await outcome(after: status, body: body)
    #expect(outcome.stop == .offline && outcome.refused.isEmpty)
    #expect(pending == ["song"])
  }

  @Test(arguments: [400, 403, 404, 409, 410, 422])
  func aRefusalDropsTheRequest(status: Int) async throws {
    let (outcome, pending) = try await outcome(after: status)
    #expect(outcome.refused == ["song"] && outcome.stop == .done)
    #expect(pending.isEmpty)
  }

  /// Codex on #239: a 500 that isn't the server's own may never have
  /// reached it; the request is kept and sent again once the server answers.
  @Test(arguments: ["", Answer.proxyPage] as [String])
  func aProxysServerErrorKeepsTheRequestUntilItIsSent(body: String) async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: root) }
    let store = try LibraryStore(path: root.appendingPathComponent("requests.sqlite").path)
    let origin = URL(string: "https://fixture.invalid")!
    let service = NativeService(
      server: origin,
      client: Client(
        serverURL: origin,
        transport: RecoveringTransport(StatusTransport(status: 500, body: body))),
      vault: Tokens())
    try await RequestOutbox.add(song, to: store)
    let first = try await RequestOutbox.send(store: store, isCurrent: { true }) {
      try await service.requestMusic($0)
    }
    #expect(first.stop == .offline && first.refused.isEmpty)
    #expect(await RequestOutbox.pending(store).map(\.id) == ["song"])
    let second = try await RequestOutbox.send(store: store, isCurrent: { true }) {
      try await service.requestMusic($0)
    }
    #expect(second.requested == ["song"] && second.stop == .done)
    #expect(await RequestOutbox.pending(store).isEmpty)
  }

  @Test func aServerErrorDropsTheRequestWithItsReference() async throws {
    let (outcome, pending) = try await outcome(after: 500, body: Answer.serverError)
    #expect(outcome.refused == ["song"] && outcome.stop == .done)
    #expect(
      outcome.reason
        == "That didn't work: something went wrong on the server. Reference 01234567")
    #expect(pending.isEmpty)
  }

  @Test func anAcceptedRequestLeavesTheOutbox() async throws {
    let (outcome, pending) = try await outcome(after: 200)
    #expect(outcome.requested == ["song"] && outcome.stop == .done)
    #expect(pending.isEmpty)
  }

  /// Each answer the server or a proxy in front of it can give (#239).
  @Test func eachBusyOrFailedAnswerIsToldApart() async {
    for (status, body) in [
      (429, nil), (502, nil), (504, nil), (503, Answer.busy), (503, Answer.otherBusy),
      (500, ""), (500, Answer.proxyPage), (500, Answer.otherError),
    ] as [(Int, String?)] {
      #expect(await failure(status, body) is ServerBusy, "\(status) \(body ?? "")")
    }
    #expect(await failure(503, Answer.unconfirmed) as? NativeFailure == .busyUnconfirmed)
    #expect(
      await failure(500, Answer.serverError) as? NativeFailure
        == .serverError(requestID: "0123456789abcdef"))
  }

  /// An interactive write says what happened rather than "not confirmed".
  @Test func aLibraryChangeTellsAServerErrorFromABusyServer() async {
    func failure(_ status: Int, _ body: String?) async -> NativeFailure? {
      do {
        _ = try await service(StatusTransport(status: status, body: body))
          .addMembership(trackIds: ["song"])
        return nil
      } catch { return error as? NativeFailure }
    }
    #expect(await failure(503, Answer.unconfirmed) == .busyUnconfirmed)
    #expect(await failure(500, Answer.serverError) == .serverError(requestID: "0123456789abcdef"))
    #expect(
      await failure(502, nil)
        == .unavailable("Couldn't confirm the library change. Refresh before trying again."))
  }

  @Test func theWordingIsTheSettledOne() {
    #expect(
      NativeFailure.busyUnconfirmed.errorDescription
        == "The server was busy, so this may or may not have been saved. Check before trying again."
    )
    #expect(
      NativeFailure.serverError(requestID: nil).errorDescription
        == "That didn't work: something went wrong on the server.")
  }
}
