import Foundation
import Testing

@testable import PigPlayback

private final class AudioSessionProbe: @unchecked Sendable {
  private let lock = NSLock()
  private var recorded: [Bool] = []
  private var blocked: Bool?
  private var failRelease = false
  let gate = DispatchSemaphore(value: 0)
  init(blocked: Bool? = nil, failRelease: Bool = false) {
    self.blocked = blocked
    self.failRelease = failRelease
  }
  var calls: [Bool] { lock.withLock { recorded } }
  func apply(_ active: Bool) throws {
    let (wait, fail) = lock.withLock {
      recorded.append(active)
      let wait = blocked == active
      if wait { blocked = nil }
      let fail = !active && failRelease
      if fail { failRelease = false }
      return (wait, fail)
    }
    if wait { gate.wait() }
    if fail { throw AudioPipelineFailure.invalidConfiguration }
  }
}

@MainActor private func eventually(_ condition: () -> Bool) async -> Bool {
  let limit = ContinuousClock.now + .seconds(3)
  while !condition(), ContinuousClock.now < limit {
    try? await Task.sleep(for: .milliseconds(2))
  }
  return condition()
}

@Suite @MainActor struct PlaybackResourcesTests {
  @Test func concurrentPlaySharesPendingActivation() async throws {
    let probe = AudioSessionProbe(blocked: true)
    defer { probe.gate.signal() }
    let session = PlaybackAudioSession(apply: probe.apply)
    let first = Task { try await session.activate() }
    #expect(await eventually { probe.calls == [true] })
    let second = Task { try await session.activate() }
    await Task.yield()
    #expect(!session.isActive)
    probe.gate.signal()
    try await first.value
    try await second.value
    #expect(session.isActive)
    #expect(probe.calls == [true])
    try await session.deactivate()?.value
  }

  @Test func backgroundPauseReleasesActivationStillInTheOS() async throws {
    let probe = AudioSessionProbe(blocked: true)
    defer { probe.gate.signal() }
    let session = PlaybackAudioSession(apply: probe.apply)
    let play = Task { try await session.activate() }
    #expect(await eventually { probe.calls == [true] })
    let release = try #require(session.deactivate())
    probe.gate.signal()
    do {
      try await play.value
      Issue.record("Superseded activation reported ready to play")
    } catch is CancellationError {}
    try await release.value
    #expect(probe.calls == [true, false])
    #expect(!session.isActive)
  }

  @Test func playDuringPendingReleaseActivatesAfterIt() async throws {
    let probe = AudioSessionProbe(blocked: false)
    defer { probe.gate.signal() }
    let session = PlaybackAudioSession(apply: probe.apply)
    try await session.activate()
    let release = try #require(session.deactivate())
    #expect(await eventually { probe.calls == [true, false] })
    let play = Task { try await session.activate() }
    await Task.yield()
    #expect(!session.isActive)
    probe.gate.signal()
    try await release.value
    try await play.value
    #expect(probe.calls == [true, false, true])
    #expect(session.isActive)
    try await session.deactivate()?.value
  }

  @Test func failedReleaseCanBeRetriedWithoutClaimingActivation() async throws {
    let probe = AudioSessionProbe(failRelease: true)
    let session = PlaybackAudioSession(apply: probe.apply)
    try await session.activate()
    do {
      try await session.deactivate()?.value
      Issue.record("Release failure was hidden")
    } catch {}
    #expect(!session.isActive)
    let retry = try #require(session.deactivate())
    try await retry.value
    #expect(!session.isActive)
    #expect(probe.calls == [true, false, false])
  }

  @Test func staleReleaseFailureCannotOverrideANewerPlay() async throws {
    let probe = AudioSessionProbe(blocked: false, failRelease: true)
    defer { probe.gate.signal() }
    let session = PlaybackAudioSession(apply: probe.apply)
    try await session.activate()
    let release = try #require(session.deactivate())
    #expect(await eventually { probe.calls == [true, false] })
    let play = Task { try await session.activate() }
    await Task.yield()
    probe.gate.signal()
    do { try await release.value } catch {}
    try await play.value
    #expect(session.isActive)
    #expect(probe.calls == [true, false, true])
    try await session.deactivate()?.value
  }

  @Test func repeatedIdleNotificationsShareOnePendingRelease() async throws {
    let probe = AudioSessionProbe(blocked: false)
    defer { probe.gate.signal() }
    let session = PlaybackAudioSession(apply: probe.apply)
    try await session.activate()
    let first = try #require(session.deactivate())
    #expect(await eventually { probe.calls == [true, false] })
    let second = try #require(session.deactivate())
    probe.gate.signal()
    try await first.value
    try await second.value
    #expect(probe.calls == [true, false])
    #expect(!session.isActive)
  }

  @Test(.timeLimit(.minutes(1)))
  func pausedProgressDoesNoPeriodicWorkAndResumeStartsOneClock() async throws {
    let clock = PlaybackProgressClock(interval: .milliseconds(5))
    defer { clock.stop() }
    let (events, continuation) = AsyncStream<Int>.makeStream()
    defer { continuation.finish() }
    var iterator = events.makeAsyncIterator()
    var ticks = 0
    let tick = {
      ticks += 1
      continuation.yield(ticks)
    }
    // Other suites can occupy the main actor for seconds on CI. Await actual
    // progress instead of imposing a three-second scheduler-latency limit.
    clock.start { tick() }
    while let count = await iterator.next(), count < 2 {}
    #expect(ticks >= 2)
    clock.stop()
    let paused = ticks
    try await Task.sleep(for: .milliseconds(40))
    #expect(ticks == paused)
    #expect(!clock.isRunning)
    clock.start { tick() }
    while let count = await iterator.next(), count <= paused {}
    #expect(ticks > paused)
    clock.stop()
    let stopped = ticks
    try await Task.sleep(for: .milliseconds(40))
    #expect(ticks == stopped)
  }

  @Test func preparingPausedOutputDoesNotStartAudioHardware() throws {
    let output = EngineQueueOutput()
    defer { output.stop() }
    try output.configure(sampleRate: 48000, blockFrames: 1024)
    #expect(output.requiresRecovery, "Only resume may start the prepared engine")
    try output.resume()
    #expect(!output.requiresRecovery)
    output.pause()
    #expect(output.requiresRecovery)
  }

  @Test func stoppingBeforeFirstClockTickDoesNotPublish() async throws {
    let clock = PlaybackProgressClock(interval: .milliseconds(5))
    var ticks = 0
    clock.start { ticks += 1 }
    clock.stop()
    try await Task.sleep(for: .milliseconds(30))
    #expect(ticks == 0)
  }
}

/// A seek that completes only when the test says so.
@MainActor private final class Seek {
  private var waiting: CheckedContinuation<Void, Never>?
  private var done = false
  func wait() async {
    guard !done else { return }
    await withCheckedContinuation { waiting = $0 }
  }
  func finish() {
    done = true
    waiting?.resume()
    waiting = nil
  }
}

/// A stream that wants to play and never moves is renewed once, then fails,
/// instead of waiting silently (#115: 89 s with no sound).
struct StreamWatchTests {
  let start = ContinuousClock.now
  func at(_ seconds: Double) -> ContinuousClock.Instant {
    start + .milliseconds(Int(seconds * 1000))
  }

  @Test func aStalledStreamIsRenewedOnceThenFails() {
    var watch = StreamWatch()
    let song = UUID()
    watch.started(song)
    #expect(watch.observe(position: 0, at: at(0)) == .fine)
    #expect(watch.observe(position: 0, at: at(14)) == .fine)
    #expect(watch.observe(position: 0, at: at(15)) == .renew)
    // The renewal starts a new item for the same song.
    watch.started(song)
    #expect(watch.observe(position: 0, at: at(16)) == .fine)
    #expect(watch.observe(position: 0, at: at(30)) == .fine)
    #expect(watch.observe(position: 0, at: at(31)) == .fail)
  }

  @Test func progressKeepsAStreamFineAndEarnsANewRenewal() {
    var watch = StreamWatch()
    let song = UUID()
    watch.started(song)
    _ = watch.observe(position: 0, at: at(0))
    #expect(watch.observe(position: 0, at: at(15)) == .renew)
    watch.started(song)
    _ = watch.observe(position: 0, at: at(15))
    // It plays: a stall later in the song (rebuffering) gets its own renewal.
    for second in 16...60 {
      #expect(watch.observe(position: Double(second - 15), at: at(Double(second))) == .fine)
    }
    #expect(watch.observe(position: 45, at: at(74)) == .fine)
    #expect(watch.observe(position: 45, at: at(75)) == .renew)
  }

  @Test func aNewSongStartsWithItsOwnRenewal() {
    var watch = StreamWatch()
    watch.started(UUID())
    _ = watch.observe(position: 0, at: at(0))
    #expect(watch.observe(position: 0, at: at(15)) == .renew)
    watch.started(UUID())
    _ = watch.observe(position: 0, at: at(20))
    #expect(watch.observe(position: 0, at: at(35)) == .renew)
  }

  /// Codex round 1: a renewal waits on the new item's seek with the progress
  /// ticks stopped; a seek that never completes must still end in a failure.
  @MainActor @Test func aRecoveryWhoseSeekNeverCompletesFailsInTime() async throws {
    let deadline = StreamRecoveryDeadline(limit: .milliseconds(100))
    var failed = false
    let seek = Seek()
    deadline.start { failed = true }
    let recovery = Task { await seek.wait() }  // The new item's seek: never completes.
    try await Task.sleep(for: .milliseconds(400))
    #expect(failed && !deadline.isActive)
    seek.finish()
    await recovery.value
  }

  /// Skipping, pausing or the first progress during the recovery: no failure.
  @MainActor @Test func aRecoveryCancelledInTimeNeverFails() async throws {
    let deadline = StreamRecoveryDeadline(limit: .milliseconds(100))
    var failed = false
    deadline.start { failed = true }
    deadline.cancel()
    // Starting again (a second renewal) replaces the first deadline.
    deadline.start { failed = true }
    deadline.start {}
    try await Task.sleep(for: .milliseconds(400))
    #expect(!failed && !deadline.isActive)
  }

  /// #116: renewed at 2:00, the person seeks back to 0:00; the stream then
  /// plays normally from there and must never fail for being below 2:00.
  @MainActor @Test func aBackwardSeekDuringRecoveryRecoversFromTheSeek() async throws {
    let deadline = StreamRecoveryDeadline(limit: .milliseconds(150))
    var failed = false
    deadline.start(from: 120) { failed = true }
    deadline.progressed(elapsed: 120, playing: false)  // Still preparing.
    deadline.seeked(to: 0)
    #expect(deadline.baseline == 0 && deadline.isActive)
    // At the seek target, not yet moving: not recovered.
    deadline.progressed(elapsed: 0, playing: true)
    #expect(deadline.isActive)
    // Normal playback from 0:00, still far below 2:00.
    deadline.progressed(elapsed: 0.25, playing: true)
    #expect(!deadline.isActive)
    try await Task.sleep(for: .milliseconds(400))
    #expect(!failed)

    // A forward seek moves the baseline the same way; a seek with no
    // recovery running changes nothing.
    deadline.start(from: 30) {}
    deadline.seeked(to: 90)
    deadline.progressed(elapsed: 60, playing: true)
    #expect(deadline.isActive)
    deadline.progressed(elapsed: 90.5, playing: true)
    #expect(!deadline.isActive)
    deadline.seeked(to: 10)
    #expect(deadline.baseline == 90)
  }

  /// #117: an interruption (a call, Siri) that ends just before the deadline
  /// must not let the old timer fail the stream at once on resume.
  @MainActor @Test func anInterruptionEndingBeforeTheDeadlineGetsAFreshOne() async throws {
    let deadline = StreamRecoveryDeadline(limit: .milliseconds(300))
    var failed = false
    deadline.start(from: 60) { failed = true }
    try await Task.sleep(for: .milliseconds(200))
    deadline.suspend()
    #expect(deadline.isActive && deadline.isSuspended)
    try await Task.sleep(for: .milliseconds(50))
    deadline.resume()  // 250 ms in: the old deadline had 50 ms left.
    try await Task.sleep(for: .milliseconds(150))
    #expect(!failed && deadline.isActive)  // Past the old 300 ms.
    // The stream then plays on: recovered, never failed.
    deadline.progressed(elapsed: 60.5, playing: true)
    try await Task.sleep(for: .milliseconds(400))
    #expect(!failed && !deadline.isActive)
  }

  /// #117: an interruption outlasting the deadline neither fails the stream
  /// meanwhile nor drops the deadline: resumed, the recovery is still bounded.
  @MainActor @Test func anInterruptionOutlastingTheDeadlineKeepsTheRecoveryBounded() async throws {
    let deadline = StreamRecoveryDeadline(limit: .milliseconds(100))
    var failed = false
    deadline.start(from: 60) { failed = true }
    deadline.suspend()
    try await Task.sleep(for: .milliseconds(300))
    #expect(!failed && deadline.isActive)
    // Resume without a running recovery does nothing; this one has one.
    deadline.resume()
    #expect(!deadline.isSuspended)
    // The preparation or seek never completes: it still fails, in time.
    try await Task.sleep(for: .milliseconds(400))
    #expect(failed && !deadline.isActive)
    // After a cancel (pause, new song) there is nothing to resume.
    failed = false
    deadline.start {}
    deadline.suspend()
    deadline.cancel()
    deadline.resume()
    #expect(!deadline.isActive)
  }

  /// #97: after a failed preparation Play showed Pause and played nothing.
  @Test func playPreparesAgainAfterAFailure() {
    #expect(StreamWatch.playNeedsPreparation(hasItem: false, itemFailed: false, preparing: false))
    #expect(StreamWatch.playNeedsPreparation(hasItem: true, itemFailed: true, preparing: false))
    #expect(!StreamWatch.playNeedsPreparation(hasItem: true, itemFailed: false, preparing: false))
    // A preparation still running plays when it ends.
    #expect(!StreamWatch.playNeedsPreparation(hasItem: false, itemFailed: false, preparing: true))
  }
}
