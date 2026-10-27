import Foundation

/// What a listener would have heard from a queue decoded as fast as its
/// bytes arrive: when each song's first audio came, and where a player that
/// started at the first block would have run out of audio and fallen silent.
/// For measuring real transfers off the device; the app's player keeps its
/// own state.
public struct PlaybackTimeline: Sendable {
  public struct Song: Sendable, Equatable {
    public let index: Int
    /// When the song's first audio arrived, from the queue's start.
    public var firstAudio: Duration
    public var frames: Int64 = 0
    public var stalls = 0
    public var silent: Duration = .zero
  }
  public private(set) var songs: [Song] = []
  /// When the first audio arrived: the wait before any sound.
  public private(set) var firstSound: Duration?
  private let rate: Double
  private let minimumStall: Duration
  private var produced: Int64 = 0
  /// How far playback was pushed back by silences so far.
  private var shift: Duration = .zero

  public init(sampleRate: Double, minimumStall: Duration = .milliseconds(50)) {
    rate = sampleRate
    self.minimumStall = minimumStall
  }

  /// A block of `frames` arrived `at` this time from the queue's start. Its
  /// markers say which songs begin in it (a marker re-sent for a playing song
  /// changes nothing).
  public mutating func received(frames: Int, markers: [QueueAudioMarker], at: Duration) {
    var gap = Duration.zero
    if let started = firstSound {
      let runsOut = started + shift + .seconds(Double(produced) / rate)
      if at - runsOut >= minimumStall { gap = at - runsOut }
    } else {
      firstSound = at
    }
    // A song repeated later in the queue is a new entry. A block's frames
    // count for the song playing at its end, to within one block.
    for marker in markers.sorted(by: { $0.switchFrame < $1.switchFrame })
    where songs.last?.index != marker.index {
      songs.append(Song(index: marker.index, firstAudio: at))
    }
    guard !songs.isEmpty else { return produced += Int64(frames) }
    // Silence before a song's first block is that song arriving late.
    if gap > .zero {
      shift += gap
      songs[songs.count - 1].stalls += 1
      songs[songs.count - 1].silent += gap
    }
    songs[songs.count - 1].frames += Int64(frames)
    produced += Int64(frames)
  }
}
