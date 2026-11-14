import PigDomain
import Testing

// #123: a deferred Watch publish whose account changed returned without
// clearing itself, so no deferred publish was ever scheduled again.

/// Waits up to two seconds for `done`: fixed sleeps were too short when the
/// whole suite runs in parallel (#189).
@MainActor private func settle(_ done: () -> Bool) async throws {
  for _ in 0..<200 where !done() { try await Task.sleep(for: .milliseconds(10)) }
}

@MainActor @Test func aRunWhoseAccountChangedStillLetsTheNextOneBeScheduled() async throws {
  let deferred = DeferredRun()
  var current = true
  var runs = 0
  deferred.schedule(after: .milliseconds(20), isCurrent: { current }) { runs += 1 }
  current = false  // The account changes while it waits.
  try await Task.sleep(for: .milliseconds(120))
  #expect(runs == 0)
  #expect(!deferred.isPending)
  current = true
  deferred.schedule(after: .milliseconds(20), isCurrent: { current }) { runs += 1 }
  try await settle { runs != 0 && !deferred.isPending }
  #expect(runs == 1 && !deferred.isPending)
}

@MainActor @Test func oneRunWaitsAtATimeAndCancelDropsIt() async throws {
  let deferred = DeferredRun()
  var runs = 0
  deferred.schedule(after: .milliseconds(30), isCurrent: { true }) { runs += 1 }
  deferred.schedule(after: .milliseconds(1), isCurrent: { true }) { runs += 10 }
  try await settle { runs != 0 && !deferred.isPending }
  #expect(runs == 1)
  deferred.schedule(after: .milliseconds(30), isCurrent: { true }) { runs += 1 }
  deferred.cancel()
  // A newer run scheduled after the cancel is not cleared by the old one.
  deferred.schedule(after: .milliseconds(60), isCurrent: { true }) { runs += 100 }
  try await settle { runs >= 101 && !deferred.isPending }
  try await Task.sleep(for: .milliseconds(60))  // The cancelled run would land by now.
  #expect(runs == 101)
}
