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
  // Whatever begin changed before failing is put back (Codex, #124).
  #expect(probe.ended == 1)
  #expect(
    probe.lines == [
      "audio session can't record: AVAudioSessionErrorDomain 561017449",
      "audio session restored",
    ])
}

/// The app's audio session: what was set, in order.
@MainActor private final class FakeAudio: ListeningAudioSession, @unchecked Sendable {
  var current = "playback longForm"
  var calls: [String] = []
  var failActivate = false
  func configuration() -> String { current }
  func describe(_ configuration: String) -> String { configuration }
  func record() throws {
    current = "playAndRecord"
    calls.append("record")
  }
  func activate() throws {
    calls.append("activate")
    if failActivate { throw NSError(domain: "AVAudioSessionErrorDomain", code: 560_557_684) }
  }
  func restore(_ configuration: String) throws {
    current = configuration
    calls.append("restore \(configuration)")
  }
  func release() throws { calls.append("release") }
}

@MainActor @Test func aCategorySetBeforeActivationFailsIsPutBackOnce() async {
  let audio = FakeAudio()
  audio.failActivate = true
  let probe = Probe()
  let end = await ShazamListening.listen(
    FakeSession(answer: .match),
    audio: .recording(audio, keepActive: false, log: probe.log), limit: .seconds(10),
    log: probe.log)
  guard case .audioUnavailable = end else {
    Issue.record("expected audioUnavailable, got \(end)")
    return
  }
  #expect(audio.current == "playback longForm")
  #expect(audio.calls == ["record", "activate", "restore playback longForm", "release"])
}

@MainActor @Test func theAudioSessionIsPutBackOnceOnEveryEnding() async {
  for answer in [FakeSession.Outcome.match, .noMatch, nil] {
    for keepActive in [false, true] {
      let audio = FakeAudio()
      let probe = Probe()
      _ = await ShazamListening.listen(
        FakeSession(answer: answer),
        audio: .recording(audio, keepActive: keepActive, log: probe.log),
        limit: .milliseconds(200), log: probe.log)
      #expect(audio.current == "playback longForm")
      #expect(
        audio.calls
          == ["record", "activate", "restore playback longForm"] + (keepActive ? [] : ["release"]))
    }
  }
  // Closing the screen mid-listen.
  let audio = FakeAudio()
  let probe = Probe()
  let task = Task { @MainActor in
    await ShazamListening.listen(
      FakeSession(answer: nil), audio: .recording(audio, keepActive: false, log: probe.log),
      limit: .seconds(60), log: probe.log)
  }
  try? await Task.sleep(for: .milliseconds(100))
  task.cancel()
  _ = await task.value
  #expect(audio.calls == ["record", "activate", "restore playback longForm", "release"])
  // Ending twice still restores once.
  let hooks = ShazamAudioHooks.recording(audio, keepActive: false, log: probe.log)
  audio.calls = []
  try? hooks.begin()
  hooks.end()
  hooks.end()
  #expect(audio.calls == ["record", "activate", "restore playback longForm", "release"])
}

@Test func aTimeoutSaysTheSongCouldNotBeIdentified() {
  #expect(ShazamFailure.timedOut.message.hasPrefix("Couldn't identify the song"))
}

/// A managed session whose preparation finishes only when released.
@MainActor private final class SlowPrepareSession: ShazamListeningSession {
  private var waiting: CheckedContinuation<Void, Never>?
  private(set) var asked = false
  private(set) var prepared = false
  func prepare() async {
    await withCheckedContinuation { waiting = $0 }
    prepared = true
  }
  func finishPreparing() {
    waiting?.resume()
    waiting = nil
  }
  func result() async -> FakeSession.Outcome {
    asked = true
    return .match
  }
  nonisolated func cancel() {}
  var stateName: String { "idle" }
}

@MainActor @Test func aPreparationEndingAfterTheLimitNeverStartsRecording() async {
  for closing in [false, true] {
    let session = SlowPrepareSession()
    let probe = Probe()
    let task = Task { @MainActor in
      await ShazamListening.listen(
        session, audio: probe.hooks, limit: .milliseconds(closing ? 60_000 : 100),
        log: probe.log)
    }
    if closing {
      try? await Task.sleep(for: .milliseconds(50))
      task.cancel()
    }
    _ = await task.value
    session.finishPreparing()
    while !session.prepared { await Task.yield() }
    for _ in 0..<20 { await Task.yield() }
    #expect(!session.asked)
  }
}
