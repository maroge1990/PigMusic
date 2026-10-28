import Foundation

/// Serialises blocking system audio-session changes without blocking the UI.
/// A release invalidates pending activations immediately; a later Play queues
/// behind that release rather than trusting a stale active flag.
@MainActor public final class PlaybackAudioSession {
  public private(set) var isActive = false
  private var generation = 0
  private var activation: (generation: Int, task: Task<Void, any Error>)?
  private var deactivation: (generation: Int, task: Task<Void, any Error>)?
  private var needsRelease = false
  private let queue = DispatchQueue(label: "au.com.pig.music.audio-session", qos: .userInitiated)
  private let apply: @Sendable (Bool) throws -> Void

  public init(apply: @escaping @Sendable (Bool) throws -> Void) { self.apply = apply }

  public func activate() async throws {
    if isActive { return }
    let request: (generation: Int, task: Task<Void, any Error>)
    if let activation {
      request = activation
    } else {
      generation += 1
      needsRelease = true
      deactivation = nil
      request = (generation, enqueue(true))
      activation = request
    }
    do {
      try await request.task.value
    } catch {
      guard generation == request.generation else { throw CancellationError() }
      activation = nil
      throw error
    }
    guard generation == request.generation else { throw CancellationError() }
    isActive = true
    activation = nil
  }

  /// The operation is enqueued synchronously, before a subsequent activation.
  /// Keep the returned task to observe a failure; the hardware change itself
  /// always runs even if its caller has gone away.
  @discardableResult public func deactivate() -> Task<Void, any Error>? {
    if let deactivation, deactivation.generation == generation { return deactivation.task }
    guard needsRelease else { return nil }
    generation += 1
    let stamp = generation
    activation = nil
    isActive = false
    let operation = enqueue(false)
    let completion = Task {
      do {
        try await operation.value
        if generation == stamp {
          needsRelease = false
          deactivation = nil
        }
      } catch {
        // Failure leaves release needed, but must not make a later Play trust
        // an active flag after interruption/reset. It activates explicitly.
        if generation == stamp { deactivation = nil }
        throw error
      }
    }
    deactivation = (stamp, completion)
    return completion
  }

  private func enqueue(_ active: Bool) -> Task<Void, any Error> {
    let (results, continuation) = AsyncStream<Result<Void, any Error>>.makeStream()
    let apply = apply
    queue.async {
      continuation.yield(Result { try apply(active) })
      continuation.finish()
    }
    return Task {
      for await result in results { return try result.get() }
      throw CancellationError()
    }
  }
}

/// The AVPlayer fallback's UI clock, independent of the audio render clock.
/// Stopping releases its periodic work while retaining the playback position.
@MainActor public final class PlaybackProgressClock {
  private var task: Task<Void, Never>?
  public var isRunning: Bool { task != nil }
  private let interval: Duration
  public init(interval: Duration = .milliseconds(250)) { self.interval = interval }

  public func start(_ tick: @escaping @MainActor () -> Void) {
    stop()
    let interval = interval
    task = Task {
      while !Task.isCancelled {
        tick()
        do { try await Task.sleep(for: interval) } catch { return }
      }
    }
  }

  public func stop() {
    task?.cancel()
    task = nil
  }

  deinit { task?.cancel() }
}
