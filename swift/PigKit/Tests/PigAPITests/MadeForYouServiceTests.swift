import Foundation
import HTTPTypes
import OpenAPIRuntime
import PigAPI
import PigDomain
import Testing

@testable import PigSync

private actor Tokens: DeviceTokenStore {
  func read() -> String? { "fixture" }
  func save(_ value: String) {}
  func clear() {}
}
private actor Recorder: ClientTransport {
  let json: String
  var sent: [(String, Data)] = []
  init(_ json: String) { self.json = json }
  func send(_ request: HTTPRequest, body: HTTPBody?, baseURL: URL, operationID: String) async throws
    -> (HTTPResponse, HTTPBody?)
  {
    var data = Data()
    if let body { for try await chunk in body { data.append(contentsOf: chunk) } }
    sent.append((request.path ?? "", data))
    var response = HTTPResponse(status: .ok)
    response.headerFields[.contentType] = "application/json"
    return (response, HTTPBody(json))
  }
}

/// The iPhone's calls for batch 11 (#202, #203) against the contract.
struct MadeForYouServiceTests {
  private func service(_ transport: Recorder) -> NativeService {
    let origin = URL(string: "https://fixture.invalid")!
    return NativeService(
      server: origin, client: Client(serverURL: origin, transport: transport), vault: Tokens())
  }

  @Test func aContinuationSendsTheSeedsAndTheQueue() async throws {
    let transport = Recorder(
      #"{"id":"e","title":"Similar songs","preparing":false,"tracks":[{"id":"mix:e:3","title":"Next","artist":"A"}]}"#
    )
    let batch = try await service(transport).radioContinuation(
      seeds: ["one", "two"], exclude: ["one", "two", "three"])
    #expect(batch.title == "Similar songs" && batch.tracks.map(\.id) == ["mix:e:3"])
    let sent = await transport.sent
    #expect(sent.first?.0 == "/api/v1/radio/continuation")
    let data = try #require(sent.first?.1)
    let body = try #require(JSONSerialization.jsonObject(with: data) as? [String: Any])
    #expect(body["seeds"] as? [String] == ["one", "two"])
    #expect(body["exclude"] as? [String] == ["one", "two", "three"])
  }

  @Test func rewindAsksForThePeriod() async throws {
    let transport = Recorder(
      #"{"period":"month","start":1,"end":2,"timezone":"Australia/Melbourne","plays":0,"minutes":0,"top_songs":[],"top_artists":[],"top_albums":[],"new_artists":[],"top_mixes":[]}"#
    )
    let rewind = try await service(transport).rewind(period: "month")
    #expect(rewind.period == "month" && rewind.plays == 0)
    #expect(await transport.sent.first?.0 == "/api/v1/rewind?period=month")
    await #expect(throws: NativeFailure.self) {
      try await service(transport).rewind(period: "decade")
    }
  }
}
