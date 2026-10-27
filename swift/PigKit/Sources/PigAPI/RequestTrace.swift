import Foundation
import HTTPTypes
import OpenAPIRuntime

/// What one API request took, for diagnostics. The server answers with the
/// same `X-Request-ID` and its own phases in `Server-Timing`.
public struct RequestTrace: Sendable {
  public let operation: String
  public let requestID: String
  public let status: Int?
  public let duration: Duration
  public let serverTiming: String?
  /// What went wrong when the request failed or was cancelled.
  public let failure: String?
  public init(
    operation: String, requestID: String, status: Int?, duration: Duration,
    serverTiming: String?, failure: String?
  ) {
    self.operation = operation
    self.requestID = requestID
    self.status = status
    self.duration = duration
    self.serverTiming = serverTiming
    self.failure = failure
  }
}

/// Tags each request with an ID and reports how long it took. Innermost, so
/// each retry is its own request with its own ID.
public struct RequestTraceMiddleware: ClientMiddleware {
  private let newID: @Sendable () -> String
  private let report: @Sendable (RequestTrace) -> Void
  public init(
    newID: @escaping @Sendable () -> String, report: @escaping @Sendable (RequestTrace) -> Void
  ) {
    self.newID = newID
    self.report = report
  }
  public func intercept(
    _ request: HTTPRequest, body: HTTPBody?, baseURL: URL, operationID: String,
    next: (HTTPRequest, HTTPBody?, URL) async throws -> (HTTPResponse, HTTPBody?)
  ) async throws -> (HTTPResponse, HTTPBody?) {
    var outgoing = request
    let id = newID()
    outgoing.headerFields[HTTPField.Name("X-Request-ID")!] = id
    let began = ContinuousClock.now
    do {
      let result = try await next(outgoing, body, baseURL)
      report(
        RequestTrace(
          operation: operationID, requestID: id, status: result.0.status.code,
          duration: .now - began,
          serverTiming: result.0.headerFields[HTTPField.Name("Server-Timing")!], failure: nil))
      return result
    } catch {
      report(
        RequestTrace(
          operation: operationID, requestID: id, status: nil, duration: .now - began,
          serverTiming: nil, failure: Self.describe(error)))
      throw error
    }
  }
  /// Short and free of URLs: "URLError -1001", "cancelled" or the type's name.
  static func describe(_ error: Error) -> String {
    if let error = error as? URLError { return "URLError \(error.code.rawValue)" }
    if error is CancellationError { return "cancelled" }
    return String(describing: type(of: error))
  }
}
