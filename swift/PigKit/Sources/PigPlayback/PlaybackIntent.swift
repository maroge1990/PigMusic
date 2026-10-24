/// User intent survives preparation and interruption suspension independently of output state.
public struct PlaybackIntent: Sendable, Equatable {
  public private(set) var wantsPlayback = false
  public private(set) var interrupted = false
  public var canPlay: Bool { wantsPlayback && !interrupted }
  public init() {}
  public mutating func play() { wantsPlayback = true }
  /// The person pressed Play or chose music. iOS does not always end an
  /// interruption it began (unplugged headphones, a session taken while
  /// suspended), so their choice ends any interruption still standing; if
  /// another app really holds audio, activating the session then fails.
  public mutating func choosePlay() {
    wantsPlayback = true
    interrupted = false
  }
  public mutating func pause() { wantsPlayback = false }
  public mutating func beginInterruption() { interrupted = true }
  public mutating func endInterruption(shouldResume: Bool) {
    interrupted = false
    if !shouldResume { wantsPlayback = false }
  }
}
