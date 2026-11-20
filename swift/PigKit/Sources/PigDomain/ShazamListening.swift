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
///
/// An attempt is reserved before the microphone permission is asked, so
/// Play while the prompt is up claims it too: the attempt then never sets
/// up recording (Codex). Playback waits only for attempts that have changed
/// the session, never for a prompt.
@MainActor public final class ListeningAudioGate {
  public static let shared = ListeningAudioGate()
  private struct Entry {
    var stop: (@MainActor () -> Void)?
    var recording = false
    var claimed = false
  }
  private var next = 0
  private var entries: [Int: Entry] = [:]
  private var waiters: [CheckedContinuation<Void, Never>] = []
  public init() {}

  /// An attempt is reserved or recording.
  public var isListening: Bool { !entries.isEmpty }

  /// Playback is starting: every attempt stops at once, or never starts.
  public func claim() {
    for id in Array(entries.keys) where entries[id]?.claimed == false {
      entries[id]?.claimed = true
      entries[id]?.stop?()
    }
  }
  /// Claims every attempt and returns once each one recording has put the
  /// session back.
  public func yieldToPlayback() async {
    claim()
    guard entries.values.contains(where: \.recording) else { return }
    await withCheckedContinuation { waiters.append($0) }
  }

  /// Reserves an attempt; `end` it whatever happens.
  public func reserve() -> Int {
    next += 1
    entries[next] = Entry()
    return next
  }
  public func isClaimed(_ id: Int) -> Bool { entries[id]?.claimed ?? false }
  public func end(_ id: Int) {
    entries[id] = nil
    wake()
  }
  /// False when playback has claimed the attempt: then nothing is recorded.
  func startRecording(_ id: Int, stop: @escaping @MainActor () -> Void) -> Bool {
    guard let entry = entries[id], !entry.claimed else { return false }
    entries[id]?.stop = stop
    entries[id]?.recording = true
    return true
  }
  /// The session is put back. The claim stays until the attempt ends.
  func stopRecording(_ id: Int) {
    entries[id]?.recording = false
    entries[id]?.stop = nil
    wake()
  }
  private func wake() {
    guard !entries.values.contains(where: \.recording) else { return }
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
    gate: ListeningAudioGate = .shared, reservation: Int? = nil,
    log: @escaping @MainActor @Sendable (String) -> Void
  ) async -> ShazamListenEnd<Session.Outcome> {
    if Task.isCancelled { return .cancelled }
    // `reservation`: the caller reserved the attempt before asking for the
    // microphone, and ends it.
    let id = reservation ?? gate.reserve()
    defer { if reservation == nil { gate.end(id) } }
    let race = ShazamRace<ShazamListenEnd<Session.Outcome>>()
    guard gate.startRecording(id, stop: { race.finish(.playbackStarted) }) else {
      log("playback started before listening: not recording")
      return .playbackStarted
    }
    // Installed before anything changes: a begin that fails part-way is
    // put back too, once; playback waiting on the gate starts only after.
    defer {
      audio.end(gate.isClaimed(id))
      log("audio session restored")
      gate.stopRecording(id)
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

  /// The most the whole of "What's playing?" may take, the microphone
  /// question and setting up included: a step the listening limit doesn't
  /// cover must not leave the screen on "Listening…" (#124).
  public static let outerLimit: Duration = limit + .seconds(10)
  /// The most the song's album and length may hold back a match (#124).
  public static let lookupLimit: Duration = .seconds(3)

  /// `operation`, or `.timedOut` once `limit` has passed, or `.cancelled`
  /// when the caller is cancelled: whichever comes first. The operation is
  /// cancelled when it lost and is never waited for, so one that never
  /// returns can't hold the caller (#124).
  public static func bounded<Value: Sendable>(
    _ limit: Duration, _ operation: @escaping @MainActor @Sendable () async -> Value
  ) async -> ShazamBounded<Value> {
    if Task.isCancelled { return .cancelled }
    let race = ShazamRace<ShazamBounded<Value>>()
    let work = Task { @MainActor in
      let value = await operation()
      race.finish(.finished(value))
    }
    let deadline = Task { @MainActor in
      do { try await Task.sleep(for: limit) } catch { return }
      race.finish(.timedOut)
    }
    let end = await withTaskCancellationHandler {
      await race.wait()
    } onCancel: {
      Task { @MainActor in race.finish(.cancelled) }
    }
    deadline.cancel()
    work.cancel()
    return end
  }

  /// A match's song with its album and length when `lookup` gives them
  /// within `limit`; otherwise the plain song, so a lookup that never
  /// returns never holds the match back (#124).
  public static func detailed(
    _ plain: ShazamSong, limit: Duration = lookupLimit,
    log: @escaping @MainActor @Sendable (String) -> Void,
    lookup: @escaping @MainActor @Sendable () async -> ShazamSong
  ) async -> ShazamSong {
    switch await bounded(limit, lookup) {
    case .finished(let song): return song
    case .timedOut:
      log("song details took longer than \(limit): shown without them")
      return plain
    case .cancelled: return plain
    }
  }

  /// An error's domain and code, for the diagnostics.
  public static func describe(_ error: any Error) -> String {
    let error = error as NSError
    return "\(error.domain) \(error.code)"
  }
}

/// How a bounded step ended (`ShazamListening.bounded`).
public enum ShazamBounded<Value: Sendable>: Sendable {
  case finished(Value)
  /// The limit passed first; the step was cancelled.
  case timedOut
  /// The caller was cancelled.
  case cancelled
}

extension ShazamBounded: Equatable where Value: Equatable {}
extension ShazamBounded: Hashable where Value: Hashable {}

/// Whichever comes first: the answer, the deadline or cancelling.
@MainActor private final class ShazamRace<End: Sendable> {
  private var end: End?
  private var waiter: CheckedContinuation<End, Never>?
  var isFinished: Bool { end != nil }

  func finish(_ end: End) {
    guard self.end == nil else { return }
    self.end = end
    waiter?.resume(returning: end)
    waiter = nil
  }

  func wait() async -> End {
    if let end { return end }
    return await withCheckedContinuation { waiter = $0 }
  }
}
