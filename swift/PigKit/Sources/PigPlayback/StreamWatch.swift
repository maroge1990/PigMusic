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
