import Foundation

/// What "What's playing?" needs from ShazamKit's managed session, so the
/// listening steps can be tested without a microphone (#124).
public protocol ShazamListeningSession: AnyObject, Sendable {
  associatedtype Outcome: Sendable
  func prepare() async
  func result() async -> Outcome
  func cancel()
  /// The session's state, for the diagnostics.
  @MainActor var stateName: String { get }
}

/// How one listen ended.
public enum ShazamListenEnd<Outcome: Sendable>: Sendable {
  case finished(Outcome)
  /// Nothing came back in time; the session was cancelled.
  case timedOut
  case cancelled
  /// The audio session couldn't be set up for recording.
  case audioUnavailable
  /// PigMusic started playing, which takes the audio session.
  case playbackStarted
}

/// PigMusic's playback and "What's playing?" share the app's audio session
/// (#124). Playback wins: before it activates the session it stops any
/// listening and waits until listening has put the session back, so a late
/// clean-up never releases a session playback has just taken (Codex).
@MainActor public final class ListeningAudioGate {
  public static let shared = ListeningAudioGate()
  private var next = 0
  private var listening: [Int: @MainActor () -> Void] = [:]
  private var claimed: Set<Int> = []
  private var waiters: [CheckedContinuation<Void, Never>] = []
  public init() {}

  public var isListening: Bool { !listening.isEmpty }

  /// Playback is starting: every listen stops at once.
  public func claim() {
    for (id, stop) in listening where !claimed.contains(id) {
      claimed.insert(id)
      stop()
    }
  }
  /// Stops every listen and returns once each has put the session back.
  public func yieldToPlayback() async {
    claim()
    guard isListening else { return }
    await withCheckedContinuation { waiters.append($0) }
  }

  func begin(stop: @escaping @MainActor () -> Void) -> Int {
    next += 1
    listening[next] = stop
    return next
  }
  func isClaimed(_ id: Int) -> Bool { claimed.contains(id) }
  func end(_ id: Int) {
    listening[id] = nil
    claimed.remove(id)
    guard listening.isEmpty else { return }
    let waiting = waiters
    waiters = []
    for waiter in waiting { waiter.resume() }
  }
}

/// Switches the app's audio session to one that can record before listening,
/// and back afterwards. Once `begin` is called, `end` runs exactly once,
/// even when `begin` failed part-way (Codex, #124: a category set before
/// activation failed was never put back).
public struct ShazamAudioHooks: Sendable {
  public var begin: @MainActor @Sendable () throws -> Void
  /// `playbackClaimed`: PigMusic is taking the session (`ListeningAudioGate`).
  public var end: @MainActor @Sendable (_ playbackClaimed: Bool) -> Void
  public init(
    begin: @escaping @MainActor @Sendable () throws -> Void,
    end: @escaping @MainActor @Sendable (_ playbackClaimed: Bool) -> Void
  ) {
    self.begin = begin
    self.end = end
  }

  /// The app's audio session, recording while listening: its configuration
  /// is read before anything changes and put back afterwards, and it is
  /// released. When PigMusic holds the session by then (`playbackHolds`, or
  /// it claimed it), it gets PigMusic's playback configuration instead and
  /// is never released.
  public static func recording<Audio: ListeningAudioSession>(
    _ audio: Audio, playbackHolds: @escaping @MainActor @Sendable () -> Bool,
    log: @escaping @MainActor @Sendable (String) -> Void
  ) -> ShazamAudioHooks {
    let saved = SavedAudio<Audio.Configuration>()
    return ShazamAudioHooks(
      begin: {
        let configuration = audio.configuration()
        saved.configuration = configuration
        log("audio session was \(audio.describe(configuration))")
        try audio.record()
        try audio.activate()
      },
      end: { playbackClaimed in
        guard let configuration = saved.configuration else { return }
        saved.configuration = nil
        // Checked now, not when listening began: playback may have started.
        let playback = playbackClaimed || playbackHolds()
        do {
          try audio.restore(playback ? audio.playbackConfiguration() : configuration)
        } catch {
          log("putting the audio session back failed: \(ShazamListening.describe(error))")
        }
        guard !playback else {
          log("audio session left to playback")
          return
        }
        do { try audio.release() } catch {
          log("releasing the audio session failed: \(ShazamListening.describe(error))")
        }
      })
  }
}

/// The configuration listening put aside, used on the main actor only.
private final class SavedAudio<Configuration>: @unchecked Sendable {
  var configuration: Configuration?
}

/// The app's audio session as listening uses it (AVAudioSession on the
/// iPhone; a fake in tests).
public protocol ListeningAudioSession: Sendable {
  associatedtype Configuration
  /// The category, mode, policy and options in force.
  @MainActor func configuration() -> Configuration
  @MainActor func describe(_ configuration: Configuration) -> String
  /// The configuration PigMusic plays with.
  @MainActor func playbackConfiguration() -> Configuration
  /// A category that records (`.playAndRecord`).
  @MainActor func record() throws
  @MainActor func activate() throws
  @MainActor func restore(_ configuration: Configuration) throws
  @MainActor func release() throws
}

/// One listen with a hard limit (#124: the sheet stayed on "Listening…"
/// forever when ShazamKit never answered). Every step goes to `log`.
@MainActor public enum ShazamListening {
  public static let limit: Duration = .seconds(15)

  public static func listen<Session: ShazamListeningSession>(
    _ session: Session, audio: ShazamAudioHooks, limit: Duration = limit,
    gate: ListeningAudioGate = .shared, log: @escaping @MainActor @Sendable (String) -> Void
  ) async -> ShazamListenEnd<Session.Outcome> {
    if Task.isCancelled { return .cancelled }
    let race = ShazamRace<Session.Outcome>()
    let id = gate.begin { race.finish(.playbackStarted) }
    // Installed before anything changes: a begin that fails part-way is
    // put back too, once; playback waiting on the gate starts only after.
    defer {
      audio.end(gate.isClaimed(id))
      log("audio session restored")
      gate.end(id)
    }
    do {
      try audio.begin()
      log("audio session ready to record")
    } catch {
      log("audio session can't record: \(Self.describe(error))")
      return .audioUnavailable
    }
    let work = Task { @MainActor in
      log("prepare (state \(session.stateName))")
      await session.prepare()
      // Preparation may end after the time limit or the screen closed:
      // then the microphone must not start recording (Codex, #124).
      guard !Task.isCancelled, !race.isFinished else {
        log("prepared after listening ended")
        return
      }
      log("prepared (state \(session.stateName))")
      let outcome = await session.result()
      race.finish(.finished(outcome))
    }
    let deadline = Task { @MainActor in
      let clock = ContinuousClock()
      let end = clock.now.advanced(by: limit)
      var last = session.stateName
      while clock.now < end {
        let step = min(Duration.milliseconds(500), clock.now.duration(to: end))
        do { try await Task.sleep(for: step) } catch { return }
        let state = session.stateName
        if state != last {
          log("state \(state)")
          last = state
        }
      }
      log("timed out after \(limit) (state \(session.stateName))")
      race.finish(.timedOut)
    }
    let end = await withTaskCancellationHandler {
      await race.wait()
    } onCancel: {
      Task { @MainActor in race.finish(.cancelled) }
    }
    deadline.cancel()
    work.cancel()
    switch end {
    case .finished: break
    case .timedOut, .cancelled, .audioUnavailable, .playbackStarted:
      if case .cancelled = end { log("cancelled (state \(session.stateName))") }
      if case .playbackStarted = end { log("stopped: playback started") }
      // ShazamKit may never answer: stop it so the microphone closes.
      session.cancel()
    }
    return end
  }

  /// An error's domain and code, for the diagnostics.
  public static func describe(_ error: any Error) -> String {
    let error = error as NSError
    return "\(error.domain) \(error.code)"
  }
}

/// Whichever comes first: the session's answer, the deadline or cancelling.
@MainActor private final class ShazamRace<Outcome: Sendable> {
  private var end: ShazamListenEnd<Outcome>?
  private var waiter: CheckedContinuation<ShazamListenEnd<Outcome>, Never>?
  var isFinished: Bool { end != nil }

  func finish(_ end: ShazamListenEnd<Outcome>) {
    guard self.end == nil else { return }
    self.end = end
    waiter?.resume(returning: end)
    waiter = nil
  }

  func wait() async -> ShazamListenEnd<Outcome> {
    if let end { return end }
    return await withCheckedContinuation { waiter = $0 }
  }
}
