import Foundation
import HTTPTypes
import OpenAPIRuntime
import PigDomain

/// Retries a request that never got an answer because the connection dropped,
/// such as a reused connection the server or tunnel had already closed. Only
/// reads, and requests that are safe to repeat, are retried.
public struct TransientRetryMiddleware: ClientMiddleware {
  /// POSTs the server answers the same way when they arrive twice: minting a
  /// playback address, setting a favourite to a value, listens and edits
  /// carrying their own IDs (#188).
  static let repeatable: Set<String> = [
    "playback_url_api_v1_playback__track_id__url_post",
    "favourite_api_v1_favourites_post",
    "play_events_api_v1_play_events_post",
    "native_mutation_api_v1_mutations_post",
  ]
  let delays: [Duration]
  /// About 15 s in all: the phone lost every request for 10–20 s after
  /// opening the app while the server answered them (#188).
  public init(
    delays: [Duration] = [.milliseconds(250), .seconds(1), .seconds(2), .seconds(4), .seconds(8)]
  ) { self.delays = delays }
  public func intercept(
    _ request: HTTPRequest, body: HTTPBody?, baseURL: URL, operationID: String,
    next: (HTTPRequest, HTTPBody?, URL) async throws -> (HTTPResponse, HTTPBody?)
  ) async throws -> (HTTPResponse, HTTPBody?) {
    // A body can be sent again only if it can be read again.
    let resendable = body.map { $0.iterationBehavior == .multiple } ?? true
    let safe =
      resendable
      && (request.method == .get || request.method == .head
        || Self.repeatable.contains(operationID))
    var attempt = 0
    while true {
      do {
        return try await next(request, body, baseURL)
      } catch {
        guard safe, attempt < delays.count, error.isTransientNetworkFailure else { throw error }
        try await Task.sleep(for: delays[attempt])
        attempt += 1
      }
    }
  }
}

extension Error {
  /// The connection problem behind a client error, if any.
  public var networkFailure: URLError? {
    if let url = self as? URLError { return url }
    if let client = self as? ClientError { return client.underlyingError.networkFailure }
    return nil
  }
  var isTransientNetworkFailure: Bool {
    networkFailure?.code.isTransientConnectionFailure ?? false
  }
  /// A short message for people; connection problems never show system detail.
  public var userMessage: String {
    guard let failure = networkFailure else { return localizedDescription }
    return failure.code == .notConnectedToInternet || failure.code == .dataNotAllowed
      ? "You're offline. Check your connection and try again."
      : "Couldn't reach your server. Check your connection and try again."
  }
}
