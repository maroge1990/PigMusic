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
}

/// Switches the app's audio session to one that can record before listening,
/// and back afterwards. `end` always runs once `begin` has succeeded.
public struct ShazamAudioHooks: Sendable {
  public var begin: @MainActor @Sendable () throws -> Void
  public var end: @MainActor @Sendable () -> Void
  public init(
    begin: @escaping @MainActor @Sendable () throws -> Void,
    end: @escaping @MainActor @Sendable () -> Void
  ) {
    self.begin = begin
    self.end = end
  }
}

/// One listen with a hard limit (#124: the sheet stayed on "Listening…"
/// forever when ShazamKit never answered). Every step goes to `log`.
@MainActor public enum ShazamListening {
  public static let limit: Duration = .seconds(15)

  public static func listen<Session: ShazamListeningSession>(
    _ session: Session, audio: ShazamAudioHooks, limit: Duration = limit,
    log: @escaping @MainActor @Sendable (String) -> Void
  ) async -> ShazamListenEnd<Session.Outcome> {
    if Task.isCancelled { return .cancelled }
    do {
      try audio.begin()
      log("audio session ready to record")
    } catch {
      log("audio session can't record: \(Self.describe(error))")
      return .audioUnavailable
    }
    defer {
      audio.end()
      log("audio session restored")
    }
    let race = ShazamRace<Session.Outcome>()
    let work = Task { @MainActor in
      log("prepare (state \(session.stateName))")
      await session.prepare()
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
    case .timedOut, .cancelled, .audioUnavailable:
      if case .cancelled = end { log("cancelled (state \(session.stateName))") }
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
