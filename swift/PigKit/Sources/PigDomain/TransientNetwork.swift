import Foundation

extension URLError.Code {
  /// A connection that failed before any answer and is worth trying again at
  /// once: a reused connection the server or tunnel had closed, a connect that
  /// timed out or was refused while the network was waking. The API and the
  /// song transfers retry the same failures.
  public var isTransientConnectionFailure: Bool {
    switch self {
    case .networkConnectionLost, .timedOut, .cannotConnectToHost, .secureConnectionFailed: true
    default: false
    }
  }
}
