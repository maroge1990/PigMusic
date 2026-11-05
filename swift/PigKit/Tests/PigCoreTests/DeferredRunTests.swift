import PigDomain
import Testing

// #123: a deferred Watch publish whose account changed returned without
// clearing itself, so no deferred publish was ever scheduled again.

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
  try await Task.sleep(for: .milliseconds(120))
  #expect(runs == 1 && !deferred.isPending)
}

@MainActor @Test func oneRunWaitsAtATimeAndCancelDropsIt() async throws {
  let deferred = DeferredRun()
  var runs = 0
  deferred.schedule(after: .milliseconds(30), isCurrent: { true }) { runs += 1 }
  deferred.schedule(after: .milliseconds(1), isCurrent: { true }) { runs += 10 }
  try await Task.sleep(for: .milliseconds(120))
  #expect(runs == 1)
  deferred.schedule(after: .milliseconds(30), isCurrent: { true }) { runs += 1 }
  deferred.cancel()
  // A newer run scheduled after the cancel is not cleared by the old one.
  deferred.schedule(after: .milliseconds(60), isCurrent: { true }) { runs += 100 }
  try await Task.sleep(for: .milliseconds(200))
  #expect(runs == 101)
}
