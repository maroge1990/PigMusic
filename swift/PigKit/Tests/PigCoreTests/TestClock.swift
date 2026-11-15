import Foundation

/// A clock that moves only when the test advances it (#222). Code under test
/// takes `clock.sleep` in place of `Task.sleep`, so a deadline fires when the
/// test says so and no assertion depends on how quickly a loaded machine
/// schedules a task. Cancelling a sleeping task ends its sleep at once, as
/// `Task.sleep` does.
final class TestClock: @unchecked Sendable {
  private struct Sleeper {
    let id: Int
    let deadline: Duration
    let continuation: CheckedContinuation<Void, Error>
  }
  private enum Arrival { case wait, wake, cancel }
  private let lock = NSLock()
  private var now: Duration = .zero
  private var sleepers: [Sleeper] = []
  private var cancelledEarly: Set<Int> = []
  private var lastID = 0

  /// Pass this where the code under test takes a sleep.
  var sleep: @Sendable (Duration) async throws -> Void {
    { [self] duration in try await self.sleep(for: duration) }
  }

  /// Sleeps in progress, waiting for the clock to reach their deadline.
  var pending: Int { lock.withLock { sleepers.count } }

  func sleep(for duration: Duration) async throws {
    let id = lock.withLock {
      lastID += 1
      return lastID
    }
    try await withTaskCancellationHandler {
      try await withCheckedThrowingContinuation {
        (continuation: CheckedContinuation<Void, Error>) in
        let arrival: Arrival = lock.withLock {
          if cancelledEarly.remove(id) != nil { return .cancel }
          if duration <= .zero { return .wake }
          sleepers.append(Sleeper(id: id, deadline: now + duration, continuation: continuation))
          return .wait
        }
        switch arrival {
        case .wait: break
        case .wake: continuation.resume()
        case .cancel: continuation.resume(throwing: CancellationError())
        }
      }
    } onCancel: {
      let sleeper: Sleeper? = lock.withLock {
        if let index = sleepers.firstIndex(where: { $0.id == id }) {
          return sleepers.remove(at: index)
        }
        cancelledEarly.insert(id)  // Cancelled before it started sleeping.
        return nil
      }
      sleeper?.continuation.resume(throwing: CancellationError())
    }
  }

  /// Moves the clock on and wakes every sleep whose deadline it reached.
  func advance(by duration: Duration) {
    let due: [Sleeper] = lock.withLock {
      now += duration
      let due = sleepers.filter { $0.deadline <= now }
      sleepers.removeAll { $0.deadline <= now }
      return due
    }
    for sleeper in due { sleeper.continuation.resume() }
  }

  /// Waits until `count` sleeps are in progress, so an advance can't land
  /// before the code under test has started waiting. The real-time bound only
  /// stops a broken test; it never decides a passing one.
  func sleeping(_ count: Int = 1) async -> Bool {
    await waitFor { self.pending >= count }
  }

  deinit {
    // A sleep still waiting at the end of a test ends as a cancellation.
    for sleeper in sleepers { sleeper.continuation.resume(throwing: CancellationError()) }
  }
}

/// Polls `done` until it holds: for work the test has already released (a
/// woken sleep, a background write). Ten seconds only bounds a broken test.
func waitFor(_ done: @escaping () -> Bool) async -> Bool {
  let limit = ContinuousClock.now + .seconds(10)
  while !done() {
    guard ContinuousClock.now < limit else { return false }
    try? await Task.sleep(for: .milliseconds(1))
  }
  return true
}

/// The same on the main actor, for state owned by it.
@MainActor func waitForOnMain(_ done: () -> Bool) async -> Bool {
  let limit = ContinuousClock.now + .seconds(10)
  while !done() {
    guard ContinuousClock.now < limit else { return false }
    try? await Task.sleep(for: .milliseconds(1))
  }
  return true
}
