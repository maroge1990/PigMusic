import Foundation
import HTTPTypes
import OpenAPIRuntime
import PigDomain
import Testing

@testable import PigAPI
@testable import PigSync

private final class Traces: @unchecked Sendable {
  private let lock = NSLock()
  private var items: [RequestTrace] = []
  func add(_ trace: RequestTrace) { lock.withLock { items.append(trace) } }
  var all: [RequestTrace] { lock.withLock { items } }
}

private let address = "playback_url_api_v1_playback__track_id__url_post"

@Test func eachRequestCarriesAnIDAndReportsTheServersTiming() async throws {
  let traces = Traces()
  let middleware = RequestTraceMiddleware(newID: { "0123456789abcdef" }, report: traces.add)
  let request = HTTPRequest(method: .post, scheme: "https", authority: "pig.test", path: "/")
  _ = try await middleware.intercept(
    request, body: nil, baseURL: URL(string: "https://pig.test")!, operationID: address
  ) { sent, _, _ in
    #expect(sent.headerFields[HTTPField.Name("X-Request-ID")!] == "0123456789abcdef")
    var response = HTTPResponse(status: .ok)
    response.headerFields[HTTPField.Name("Server-Timing")!] =
      "app;dur=17100.4, auth-ready;dur=2.1, handler;dur=40.0, mix-lease;dur=16950.2"
    return (response, nil)
  }
  await #expect(throws: URLError.self) {
    _ = try await middleware.intercept(
      request, body: nil, baseURL: URL(string: "https://pig.test")!, operationID: "me"
    ) { _, _, _ in throw URLError(.timedOut) }
  }
  let (answered, failed) = (traces.all[0], traces.all[1])
  #expect(answered.status == 200 && answered.requestID == "0123456789abcdef")
  #expect(failed.status == nil && failed.failure == "URLError -1001")

  let line = try #require(NativeService.diagnosticsLine(answered))
  #expect(line.hasPrefix("playback_url 200 in "))
  #expect(line.hasSuffix("server 17100 ms: mix-lease 16950, handler 40 (id 0123456789abcdef)"))
  // Redaction keeps the line readable: no phase name or ID is masked.
  #expect(DiagnosticsLog.redact(line) == line)
  #expect(NativeService.diagnosticsLine(failed)?.contains("me URLError -1001 in ") == true)
}

@Test func onlyAddressesSlowOrFailedRequestsAreLogged() {
  func trace(_ operation: String, ms: Int, status: Int? = 200) -> RequestTrace {
    RequestTrace(
      operation: operation, requestID: "0123456789abcdef", status: status,
      duration: .milliseconds(ms), serverTiming: nil, failure: nil)
  }
  #expect(NativeService.diagnosticsLine(trace("me_api_v1_me_get", ms: 200)) == nil)
  #expect(NativeService.diagnosticsLine(trace("me_api_v1_me_get", ms: 1500)) != nil)
  #expect(NativeService.diagnosticsLine(trace("me_api_v1_me_get", ms: 20, status: 502)) != nil)
  #expect(NativeService.diagnosticsLine(trace(address, ms: 20))?.hasSuffix("0123456789abcdef)") == true)
}

@Test func serverTimingSummaryNeedsATotalAndKeepsTheSlowestPhases() {
  #expect(ServerTiming.summary(nil) == nil)
  #expect(ServerTiming.summary("mix-lease;dur=4") == nil)
  #expect(ServerTiming.summary("app;dur=12.6, auth-ready;dur=3") == "server 13 ms")
  #expect(
    ServerTiming.summary("app;dur=900, a;dur=100, b;dur=500, c;dur=300, d;dur=200, e;dur=400")
      == "server 900 ms: b 500, e 400, c 300, d 200")
  #expect(ServerTiming.summary("app;desc=x;dur=nan") == nil)
  let id = ServerTiming.newRequestID()
  #expect(id.count == 16 && id.allSatisfy(\.isHexDigit))
}


@Test(arguments: [401, 403, 429, 404])
func fastHTTPFailuresKeepRequestIDAndServerTiming(_ status: Int) throws {
  let trace = RequestTrace(
    operation: "tracks_api_v1_library_tracks_get", requestID: "0123456789abcdef", status: status,
    duration: .milliseconds(40), serverTiming: "app;dur=32.4, handler;dur=25.3", failure: nil)
  let line = try #require(NativeService.diagnosticsLine(trace))
  #expect(line == "tracks \(status) in 40 ms, server 32 ms: handler 25 (id 0123456789abcdef)")
  #expect(DiagnosticsLog.redact(line) == line)
}

@Test(arguments: [200, 206, 304])
func fastOrdinarySuccessesAndRedirectsStayUnlogged(_ status: Int) {
  let trace = RequestTrace(
    operation: "tracks_api_v1_library_tracks_get", requestID: "0123456789abcdef", status: status,
    duration: .milliseconds(40), serverTiming: "app;dur=32.4, handler;dur=25.3", failure: nil)
  #expect(NativeService.diagnosticsLine(trace) == nil)
}
