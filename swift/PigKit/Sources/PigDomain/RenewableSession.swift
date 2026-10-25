import Foundation

/// A URLSession that can be swapped for a fresh one. While the app is away,
/// connections the system keeps open can die without it noticing, and a
/// request sent on one waits for its timeout (25 Oct diagnostics: after a
/// break the first song address took 12 s and the library refresh 44 s, both
/// answering together; a relaunch answered at once). Renewing gives what comes
/// next new connections; requests already running finish on the old session.
///
/// Creating a task in an invalidated session raises an Objective-C exception
/// that ends the app, so the session is never handed out bare: a caller
/// borrows it with `withSession` (an async call that creates its task inside)
/// or starts a task with `dataTask(with:delegate:)`. A replaced session is
/// invalidated once nothing borrows it; its running tasks still finish.
public final class RenewableSession: @unchecked Sendable {
  private let lock = NSLock()
  private let make: @Sendable () -> URLSession
  private var current: URLSession
  /// How many callers borrow each session now.
  private var borrowed: [ObjectIdentifier: Int] = [:]
  /// Replaced sessions waiting for their last borrower.
  private var retired: [ObjectIdentifier: URLSession] = [:]
  public init(_ make: @escaping @Sendable () -> URLSession) {
    self.make = make
    current = make()
  }
  deinit {
    // A borrower keeps this alive, so nothing is borrowed here.
    current.invalidateAndCancel()
    for session in retired.values { session.finishTasksAndInvalidate() }
  }
  /// Runs `body` with the current session, which stays usable until `body`
  /// returns or throws, however many renewals happen meanwhile.
  public func withSession<T>(
    isolation: isolated (any Actor)? = #isolation,
    _ body: (URLSession) async throws -> T
  ) async rethrows -> T {
    let session = borrow()
    defer { giveBack(session) }
    return try await body(session)
  }
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
    let expired: URLSession? = lock.withLock {
      let old = current
      current = fresh
      guard borrowed[ObjectIdentifier(old)] == nil else {
        retired[ObjectIdentifier(old)] = old
        return nil
      }
      return old
    }
    expired?.finishTasksAndInvalidate()
  }
  /// Replaced sessions still borrowed (for tests).
  var retiredCount: Int { lock.withLock { retired.count } }

  private func borrow() -> URLSession {
    lock.withLock {
      borrowed[ObjectIdentifier(current), default: 0] += 1
      return current
    }
  }
  private func giveBack(_ session: URLSession) {
    let key = ObjectIdentifier(session)
    let expired: URLSession? = lock.withLock {
      let count = (borrowed[key] ?? 1) - 1
      guard count == 0 else {
        borrowed[key] = count
        return nil
      }
      borrowed[key] = nil
      return retired.removeValue(forKey: key)
    }
    expired?.finishTasksAndInvalidate()
  }
}
