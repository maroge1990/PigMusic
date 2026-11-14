import Foundation

/// For a request that failed or was slow, which connection carried it: the
/// protocol (h3 is HTTP/3 over QUIC), a new or reused connection, and whether
/// it went over cellular (#188). Matched to the server's log by request ID.
public final class NetworkMetricsLog: NSObject, URLSessionTaskDelegate, Sendable {
  private let report: @Sendable (String) -> Void
  private let slow: Duration
  public init(slow: Duration = .seconds(1), report: @escaping @Sendable (String) -> Void) {
    self.slow = slow
    self.report = report
  }

  public func urlSession(
    _ session: URLSession, task: URLSessionTask, didFinishCollecting metrics: URLSessionTaskMetrics
  ) {
    // The metrics arrive as the task ends, so its error is already set.
    let last = metrics.transactionMetrics.last
    let seconds = metrics.taskInterval.duration
    guard
      let line = Self.line(
        requestID: task.originalRequest?.value(forHTTPHeaderField: "X-Request-ID"),
        protocolName: last?.networkProtocolName, reused: last?.isReusedConnection ?? false,
        cellular: last?.isCellular ?? false, failure: task.error,
        duration: .milliseconds(Int((seconds * 1000).rounded())), slow: slow)
    else { return }
    report(line)
  }

  static func line(
    requestID: String?, protocolName: String?, reused: Bool, cellular: Bool, failure: Error?,
    duration: Duration, slow: Duration = .seconds(1)
  ) -> String? {
    let code: URLError.Code? = failure.flatMap { error in
      if let url = error as? URLError { return url.code }
      let other = error as NSError
      return other.domain == NSURLErrorDomain ? URLError.Code(rawValue: other.code) : nil
    }
    // A cancelled request (a newer search, a closed page) is not a failure.
    if code == .cancelled { return nil }
    guard failure != nil || duration >= slow else { return nil }
    let ms = duration.components.seconds * 1000
      + duration.components.attoseconds / 1_000_000_000_000_000
    let outcome = code.map { "URLError \($0.rawValue)" } ?? (failure == nil ? "answered" : "failed")
    return "connection \(protocolName ?? "unknown") \(reused ? "reused" : "new") "
      + "\(cellular ? "cellular" : "wifi or wired"), \(outcome) after \(ms) ms "
      + "(id \(requestID ?? "unknown"))"
  }
}
