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
