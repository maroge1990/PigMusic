import Foundation

/// Keeps a stream (AVPlayer, the fallback when seamless playback cannot
/// start) from waiting silently for ever. AVPlayer has no deadline of its own:
/// after a failed connect it once waited 89 s with no sound until the app went
/// to the background and back (#115). A stream that wants to play and makes no
/// progress for `limit` is renewed once (a new item on new connections, from
/// where it stopped); still none after another `limit`, it fails, so the
/// person sees why and can press Play to try again.
public struct StreamWatch: Sendable {
  public enum Verdict: Sendable, Equatable {
    case fine
    /// Start the song again from where it stopped, on a new item.
    case renew
    /// Stop and say the server could not be reached.
    case fail
  }
  public static let stallLimit: Duration = .seconds(15)
  private let limit: Duration
  private var occurrence: UUID?
  private var since: ContinuousClock.Instant?
  private var position: Double?
  private var renewed = false

  public init(limit: Duration = StreamWatch.stallLimit) { self.limit = limit }

  /// The stream (re)started for `occurrence`: a new item, or Play after a
  /// pause. A renewal carries over within one song, so a song that stalls
  /// again after its renewal fails instead of renewing for ever.
  public mutating func started(_ occurrence: UUID?) {
    if occurrence != self.occurrence { renewed = false }
    self.occurrence = occurrence
    since = nil
    position = nil
  }

  /// One progress tick while the person wants sound, with the stream's position.
  public mutating func observe(position: Double, at now: ContinuousClock.Instant) -> Verdict {
    guard let since, let last = self.position, position == last else {
      // The first tick, or the stream moved (playing, or a seek).
      if self.position != nil, position != self.position { renewed = false }
      self.since = now
      self.position = position
      return .fine
    }
    guard now - since >= limit else { return .fine }
    if renewed { return .fail }
    renewed = true
    self.since = nil
    self.position = nil
    return .renew
  }

  /// Whether Play must prepare the song again rather than play the item: the
  /// last preparation failed (no item), or the item itself failed. While a
  /// preparation is still running it will play when it ends (#97: Play once
  /// showed Pause and played nothing after a failed preparation).
  public static func playNeedsPreparation(hasItem: Bool, itemFailed: Bool, preparing: Bool)
    -> Bool
  {
    !preparing && (!hasItem || itemFailed)
  }
}

/// Bounds a renewed stream's whole recovery: preparing the new item, its seek
/// to where the song stopped, and its first progress. The progress ticks stop
/// while the new item is prepared, and a seek the item can never complete
/// would otherwise wait for ever (Codex, #115). A new song, a pause or a stop
/// cancels it; so does the first progress past where it is measured from.
@MainActor public final class StreamRecoveryDeadline {
  private let limit: Duration
  private var task: Task<Void, Never>?
  private var expired: (@MainActor () -> Void)?
  public init(limit: Duration = StreamWatch.stallLimit) { self.limit = limit }
  /// A recovery is running, or held by an audio interruption.
  public var isActive: Bool { expired != nil }
  /// Held by an audio interruption: no time runs until `resume()`.
  public var isSuspended: Bool { expired != nil && task == nil }
  /// Where recovery is measured from: the position the stream was renewed
  /// from, or the latest seek since (#116).
  public private(set) var baseline: Double = 0
  /// Runs `expired` after the limit unless the stream recovers or `cancel()`
  /// comes first. `position` is where the renewed stream starts again.
  public func start(from position: Double = 0, _ expired: @escaping @MainActor () -> Void) {
    cancel()
    baseline = position
    self.expired = expired
    arm()
  }
  private func arm() {
    task?.cancel()
    let limit = limit
    task = Task { [weak self] in
      do { try await Task.sleep(for: limit) } catch { return }
      guard !Task.isCancelled, let self, let expired = self.expired else { return }
      self.task = nil
      self.expired = nil
      expired()
    }
  }
  /// An audio interruption began (#117): playback is held, so the deadline
  /// stops counting; it neither fails the stream meanwhile nor is dropped.
  public func suspend() {
    task?.cancel()
    task = nil
  }
  /// Playback resumes after an interruption: the recovery gets a fresh,
  /// whole limit, so an outstanding preparation or seek stays bounded and
  /// the time before the interruption doesn't fail it at once.
  public func resume() {
    guard isSuspended else { return }
    arm()
  }
  /// A seek during recovery (#116): progress counts from where it went, so a
  /// seek back from 2:00 to 0:00 recovers once the stream plays from 0:00.
  public func seeked(to position: Double) {
    guard isActive else { return }
    baseline = position
  }
  /// One progress tick: the stream recovered once it plays past the baseline.
  public func progressed(elapsed: Double, playing: Bool) {
    if isActive, playing, elapsed > baseline { cancel() }
  }
  public func cancel() {
    task?.cancel()
    task = nil
    expired = nil
  }
}
