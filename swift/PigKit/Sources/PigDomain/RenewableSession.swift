import Foundation

/// A URLSession that can be swapped for a fresh one. While the app is away,
/// connections the system keeps open can die without it noticing, and a
/// request sent on one waits for its timeout (25 Oct diagnostics: after a
/// break the first song address took 12 s and the library refresh 44 s, both
/// answering together; a relaunch answered at once). Renewing gives what comes
/// next new connections; requests already running finish on the old session.
public final class RenewableSession: @unchecked Sendable {
  private let lock = NSLock()
  private let make: @Sendable () -> URLSession
  private var current: URLSession
  public init(_ make: @escaping @Sendable () -> URLSession) {
    self.make = make
    current = make()
  }
  deinit { current.invalidateAndCancel() }
  public var session: URLSession { lock.withLock { current } }
  public func renew() {
    let fresh = make()
    let old = lock.withLock {
      defer { current = fresh }
      return current
    }
    old.finishTasksAndInvalidate()
  }
}
