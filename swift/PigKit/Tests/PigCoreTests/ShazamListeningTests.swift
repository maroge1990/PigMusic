import Foundation
import PigDomain
import Testing

// #124: "What's playing?" stayed on "Listening…" forever when ShazamKit
// never answered. Listening now has a hard limit, records through an audio
// session set up for it, and logs every step.

/// A managed session that answers after `delay`, or never.
private final class FakeSession: ShazamListeningSession, @unchecked Sendable {
  enum Outcome: Sendable, Equatable { case match, noMatch }
  private let lock = NSLock()
  private let answer: Outcome?
  private let delay: Duration
  private var cancelled = false
  private var prepared = false
  /// Kept so the never-answered call isn't reported as leaked.
  private var stuck: [CheckedContinuation<Outcome, Never>] = []
  init(answer: Outcome?, delay: Duration = .zero) {
    self.answer = answer
    self.delay = delay
  }
  var wasCancelled: Bool { lock.withLock { cancelled } }
  func prepare() async { lock.withLock { prepared = true } }
  func result() async -> Outcome {
    if let answer {
      try? await Task.sleep(for: delay)
      return answer
    }
    // Like the device in #124: never returns, not even when cancelled.
    return await withCheckedContinuation { continuation in
      lock.withLock { stuck.append(continuation) }
    }
  }
  func cancel() { lock.withLock { cancelled = true } }
  @MainActor var stateName: String {
    lock.withLock { cancelled ? "idle" : prepared ? "matching" : "idle" }
  }
}

@MainActor private final class Probe {
  var lines: [String] = []
  var began = 0
  var ended = 0
  var failBegin = false
  var hooks: ShazamAudioHooks {
    ShazamAudioHooks(
      begin: { [self] in
        if failBegin { throw NSError(domain: "AVAudioSessionErrorDomain", code: 561_017_449) }
        began += 1
      },
      end: { [self] in ended += 1 })
  }
  func log(_ line: String) { lines.append(line) }
}

@MainActor @Test func aSessionThatNeverAnswersStopsAtTheLimit() async {
  let probe = Probe()
  let session = FakeSession(answer: nil)
  let started = ContinuousClock.now
  let end = await ShazamListening.listen(
    session, audio: probe.hooks, limit: .milliseconds(300), log: probe.log)
  guard case .timedOut = end else {
    Issue.record("expected a timeout, got \(end)")
    return
  }
  #expect(ContinuousClock.now - started < .seconds(5))
  // The microphone is closed and the app's audio session put back.
  #expect(session.wasCancelled)
  #expect(probe.began == 1 && probe.ended == 1)
  #expect(probe.lines.contains { $0.hasPrefix("timed out") })
  #expect(probe.lines.contains { $0.hasPrefix("prepared") })
}

@MainActor @Test func anAnswerInTimeIsReturnedAndTheAudioRestored() async {
  let probe = Probe()
  let session = FakeSession(answer: .match, delay: .milliseconds(20))
  let end = await ShazamListening.listen(
    session, audio: probe.hooks, limit: .seconds(10), log: probe.log)
  guard case .finished(let outcome) = end else {
    Issue.record("expected a result, got \(end)")
    return
  }
  #expect(outcome == .match)
  #expect(!session.wasCancelled)
  #expect(probe.began == 1 && probe.ended == 1)
  #expect(probe.lines.first == "audio session ready to record")
  #expect(probe.lines.last == "audio session restored")
}

@MainActor @Test func closingTheScreenStopsListeningAtOnce() async {
  let probe = Probe()
  let session = FakeSession(answer: nil)
  let task = Task { @MainActor in
    await ShazamListening.listen(
      session, audio: probe.hooks, limit: .seconds(60), log: probe.log)
  }
  try? await Task.sleep(for: .milliseconds(100))
  task.cancel()
  let started = ContinuousClock.now
  let end = await task.value
  guard case .cancelled = end else {
    Issue.record("expected cancelled, got \(end)")
    return
  }
  #expect(ContinuousClock.now - started < .seconds(5))
  #expect(session.wasCancelled)
  #expect(probe.ended == 1)
}

@MainActor @Test func anAudioSessionThatCantRecordFailsWithoutListening() async {
  let probe = Probe()
  probe.failBegin = true
  let session = FakeSession(answer: .match)
  let end = await ShazamListening.listen(
    session, audio: probe.hooks, limit: .seconds(10), log: probe.log)
  guard case .audioUnavailable = end else {
    Issue.record("expected audioUnavailable, got \(end)")
    return
  }
  #expect(probe.ended == 0)
  #expect(probe.lines == ["audio session can't record: AVAudioSessionErrorDomain 561017449"])
}

@Test func aTimeoutSaysTheSongCouldNotBeIdentified() {
  #expect(ShazamFailure.timedOut.message.hasPrefix("Couldn't identify the song"))
}
