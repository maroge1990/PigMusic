import Foundation

/// User intent survives preparation and interruption suspension independently of output state.
public struct PlaybackIntent: Sendable, Equatable {
  public private(set) var wantsPlayback = false
  public private(set) var interrupted = false
  /// Rises with every change of intent (Play, Pause, an interruption, a
  /// reset), so a later pause or interruption is told apart from an earlier
  /// one that left the same state.
  public private(set) var revision = 0
  public var canPlay: Bool { wantsPlayback && !interrupted }
  public init() {}
  public mutating func play() {
    wantsPlayback = true
    revision += 1
  }
  /// The person pressed Play or chose music. iOS does not always end an
  /// interruption it began (unplugged headphones, a session taken while
  /// suspended), so their choice ends any interruption still standing; if
  /// another app really holds audio, activating the session then fails.
  public mutating func choosePlay() {
    wantsPlayback = true
    interrupted = false
    revision += 1
  }
  public mutating func pause() {
    wantsPlayback = false
    revision += 1
  }
  public mutating func beginInterruption() {
    interrupted = true
    revision += 1
  }
  public mutating func endInterruption(shouldResume: Bool) {
    interrupted = false
    if !shouldResume { wantsPlayback = false }
    revision += 1
  }
  /// Nothing wanted and nothing interrupted, as new, with a later revision.
  public mutating func reset() {
    wantsPlayback = false
    interrupted = false
    revision += 1
  }
}

/// Where playback stands: the queue item and the intent's revision.
public struct PlaybackMark: Sendable, Equatable {
  public var item: UUID?
  public var revision: Int
  public init(item: UUID?, revision: Int) {
    self.item = item
    self.revision = revision
  }
}

/// PigMusic's song, paused so "What's playing?" can listen (#124). Playback
/// never resumes by itself afterwards (Codex: a pause, interruption or route
/// change during listening would have been overridden); "Resume playback" is
/// offered only while playback is exactly as listening left it.
public struct ListeningPause: Sendable, Equatable {
  public let mark: PlaybackMark
  /// `mark` is taken right after listening's own pause.
  public init(after mark: PlaybackMark) { self.mark = mark }
  public func offersResume(now: PlaybackMark, playing: Bool) -> Bool {
    !playing && now == mark
  }
}
