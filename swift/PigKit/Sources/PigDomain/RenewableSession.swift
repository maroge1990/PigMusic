import Foundation

/// A URLSession that can be swapped for a fresh one. While the app is away,
/// connections the system keeps open can die without it noticing, and a
/// request sent on one waits for its timeout (25 Oct diagnostics: after a
/// break the first song address took 12 s and the library refresh 44 s, both
/// answering together; a relaunch answered at once). Renewing gives what comes
/// next new connections; requests already running finish on the old session.
///
/// Creating a task in an invalidated session raises an Objective-C exception
/// that ends the app, so a replaced session is only invalidated at the
/// renewal after next: a session read just before a renewal stays usable.
/// Long-lived users (a song fetching ranges while it plays) start each task
/// with `dataTask(with:delegate:)`, on whichever session is current.
public final class RenewableSession: @unchecked Sendable {
  private let lock = NSLock()
  private let make: @Sendable () -> URLSession
  private var current: URLSession
  /// The session the last renewal replaced, kept valid until the next one.
  private var retired: URLSession?
  public init(_ make: @escaping @Sendable () -> URLSession) {
    self.make = make
    current = make()
  }
  deinit {
    current.invalidateAndCancel()
    retired?.finishTasksAndInvalidate()
  }
  public var session: URLSession { lock.withLock { current } }
  /// Creates and starts a task on the current session. A renewal cannot
  /// invalidate that session between creating the task and starting it.
  public func dataTask(with request: URLRequest, delegate: URLSessionDataDelegate)
    -> URLSessionDataTask
  {
    lock.withLock {
      let task = current.dataTask(with: request)
      task.delegate = delegate
      task.resume()
      return task
    }
  }
  public func renew() {
    let fresh = make()
    let expired = lock.withLock {
      defer {
        retired = current
        current = fresh
      }
      return retired
    }
    expired?.finishTasksAndInvalidate()
  }
}
