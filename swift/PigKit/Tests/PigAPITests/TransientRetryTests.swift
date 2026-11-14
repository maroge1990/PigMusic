import Foundation
import HTTPTypes
import OpenAPIRuntime
import Testing

@testable import PigAPI

private actor Attempts {
  var count = 0
  func next() -> Int {
    count += 1
    return count
  }
}

private func send(
  _ method: HTTPRequest.Method, operation: String, failures: Int, code: URLError.Code,
  body: HTTPBody? = nil
) async -> (Int, Error?) {
  let attempts = Attempts()
  let middleware = TransientRetryMiddleware(delays: [.zero, .zero])
  do {
    _ = try await middleware.intercept(
      HTTPRequest(method: method, scheme: "https", authority: "pig.test", path: "/"), body: body,
      baseURL: URL(string: "https://pig.test")!, operationID: operation
    ) { _, _, _ in
      if await attempts.next() <= failures { throw URLError(code) }
      return (HTTPResponse(status: .ok), nil)
    }
    return (await attempts.count, nil)
  } catch { return (await attempts.count, error) }
}

@Test func droppedConnectionsAreRetriedForReadsAndPlaybackLinks() async {
  let read = await send(.get, operation: "me", failures: 2, code: .networkConnectionLost)
  #expect(read.0 == 3 && read.1 == nil)
  let link = await send(
    .post, operation: "playback_url_api_v1_playback__track_id__url_post", failures: 1,
    code: .networkConnectionLost)
  #expect(link.0 == 2 && link.1 == nil)
}

@Test func changesAndOtherFailuresAreNotRepeated() async {
  let change = await send(.post, operation: "request_tracks", failures: 1, code: .timedOut)
  #expect(change.0 == 1 && change.1 != nil)
  let offline = await send(.get, operation: "me", failures: 1, code: .notConnectedToInternet)
  #expect(offline.0 == 1 && offline.1?.userMessage.hasPrefix("You're offline") == true)
  let lost = await send(.get, operation: "me", failures: 3, code: .networkConnectionLost)
  #expect(lost.0 == 3 && lost.1?.userMessage.hasPrefix("Couldn't reach your server") == true)
}

@Test func aTenToTwentySecondOutageIsRiddenOut() {
  // #188: requests failed for 10–20 s after opening the app; two retries
  // within 1.25 s gave up long before the connection came back.
  let delays = TransientRetryMiddleware().delays
  #expect(delays.count == 5)
  #expect(delays.reduce(Duration.zero, +) >= .seconds(15))
  #expect(delays.first! <= .milliseconds(250))
}

@Test func favouritesAndListensAreRepeatedBecauseTheServerIgnoresARepeat() async {
  // #188: a favourite sets a value and a listen carries its event ID, so a
  // repeat changes nothing; the body is sent again whole.
  for operation in [
    "favourite_api_v1_favourites_post", "play_events_api_v1_play_events_post",
    "native_mutation_api_v1_mutations_post",
  ] {
    let sent = await send(
      .post, operation: operation, failures: 2, code: .cannotConnectToHost,
      body: HTTPBody(#"{"favourite":true}"#))
    #expect(sent.0 == 3 && sent.1 == nil, "\(operation)")
  }
  // A body that can be read only once is never sent twice.
  let once = HTTPBody(
    AsyncStream<ArraySlice<UInt8>> {
      $0.yield(ArraySlice("x".utf8))
      $0.finish()
    }, length: .known(1), iterationBehavior: .single)
  let single = await send(
    .post, operation: "favourite_api_v1_favourites_post", failures: 1,
    code: .cannotConnectToHost, body: once)
  #expect(single.0 == 1 && single.1 != nil)
}
