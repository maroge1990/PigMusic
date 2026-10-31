import AVFoundation
import Foundation
import PigDomain
import Testing

@testable import PigPlayback

private actor QueueSources {
  let files: [String: URL]
  var active: Set<UUID> = []
  var peak = 0
  var acquired: [String] = []
  var failed: Set<String>
  var delayed: String?
  /// Songs whose preparation takes a while and then fails.
  var slowFailures: Set<String> = []
  var gate: CheckedContinuation<Void, Never>?
  var gated = false
  var entered = false
  init(
    files: [String: URL], gated: Bool = false, failed: String? = nil,
    failing: Set<String> = [], delayed: String? = nil, slowFailures: Set<String> = []
  ) {
    self.slowFailures = slowFailures
    self.files = files
    self.gated = gated
    self.failed = failing.union(failed.map { [$0] } ?? [])
    self.delayed = delayed
  }
  func get(_ item: PlaybackOccurrence) async throws -> PreparedAudioSource {
    acquired.append(item.track.id)
    if gated {
      entered = true
      await withCheckedContinuation { gate = $0 }
      gated = false
    }
    if item.track.id == delayed {
      entered = true
      try await Task.sleep(for: .seconds(30))
    }
    if slowFailures.contains(item.track.id) {
      try await Task.sleep(for: .milliseconds(200))
      throw AudioSourceFailure.unavailable
    }
    try Task.checkCancellation()
    if failed.contains(item.track.id) { throw AudioSourceFailure.unavailable }
    let id = UUID()
    active.insert(id)
    peak = max(peak, active.count)
    return PreparedAudioSource(id: id, file: files[item.track.id]!, byteCount: 0)
  }
  func release(_ source: PreparedAudioSource) { active.remove(source.id) }
  func open() {
    gate?.resume()
    gate = nil
  }
}
@MainActor private final class SilentQueueOutput: QueueAudioOutput {
  var positionFrames: Int64 = 0
  var pending: [(Int, @Sendable () -> Void)] = []
  var playing = false
  var starts = 0
  var resumeFails = false
  var stops = 0
  var captured: [Float] = []
  func configure(sampleRate: Double, blockFrames: Int) { positionFrames = 0 }
  func schedule(_ block: PCMBlock, completion: @escaping @Sendable () -> Void) {
    pending.append((block.frameCount, completion))
    captured.append(contentsOf: block.channels[0])
  }
  func resume() throws {
    if resumeFails { throw AudioPipelineFailure.invalidConfiguration }
    playing = true
    starts += 1
  }
  func pause() { playing = false }
  func stop() {
    pending = []
    positionFrames = 0
    playing = false
    stops += 1
  }
  func consume() {
    guard playing, !pending.isEmpty else { return }
    let first = pending.removeFirst()
    positionFrames += Int64(first.0)
    first.1()
  }
}
private func queueFixture(_ root: URL, id: String, value: Float, frames: Int = 48000) throws -> URL
{
  let url = root.appendingPathComponent(id + ".wav")
  let format = AVAudioFormat(standardFormatWithSampleRate: 48000, channels: 2)!
  let output = try AVAudioFile(forWriting: url, settings: format.settings)
  let block = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 2048)!
  var remaining = frames
  while remaining > 0 {
    block.frameLength = AVAudioFrameCount(min(2048, remaining))
    for channel in 0..<2 {
      block.floatChannelData![channel].update(repeating: value, count: Int(block.frameLength))
    }
    try output.write(from: block)
    remaining -= Int(block.frameLength)
  }
  return url
}
private func queueTrack(_ id: String, album: String? = nil, number: Int? = nil)
  -> PlaybackOccurrence
{
  var track = Track(id: id, title: id, artist: "Fixture", duration: 1)
  track.albumId = album
  track.trackNumber = number
  return PlaybackOccurrence(track: track)
}
private func queueRoot() throws -> URL {
  let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
  try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
  return url
}
@MainActor private func waitQueue(_ predicate: () -> Bool) async throws {
  let deadline = ContinuousClock.now + .seconds(5)
  while !predicate() {
    guard ContinuousClock.now < deadline else { throw AudioPipelineFailure.stalled }
    try await Task.sleep(for: .milliseconds(10))
  }
}

@Suite(.serialized) struct RealtimeQueueTests {
  @Test func threeSongStreamKeepsDecoderPositionAcrossCrossfadesAndReleasesSources() async throws {
    let root = try queueRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let sources = QueueSources(files: [
      "a": try queueFixture(root, id: "a", value: 0.2),
      "b": try queueFixture(root, id: "b", value: 0.4),
      "c": try queueFixture(root, id: "c", value: 0.6),
    ])
    let stream = try await QueuePCMStream(
      items: [queueTrack("a"), queueTrack("b"), queueTrack("c")], crossfade: true,
      fadeSeconds: 0.25,
      acquire: { try await sources.get($0) }, release: { await sources.release($0) })
    var samples: [Float] = []
    var markers: [QueueAudioMarker] = []
    while let block = try await stream.next() {
      samples += block.pcm.channels[0]
      markers += block.markers
    }
    await stream.close()
    #expect(samples.count == 120000)
    #expect(markers.map(\.index) == [0, 1, 2])
    #expect(markers.map(\.originFrame) == [0, 36000, 72000])
    #expect(markers.map(\.switchFrame) == [0, 42000, 78000])
    #expect(abs(samples[47999] - 0.4) < 0.00001)
    #expect(abs(samples[83999] - 0.6) < 0.00001)
    #expect(await sources.peak <= 2)
    #expect(await sources.active.isEmpty)
  }
  @Test func queuedEngineCaptureMatchesThreeSongTimelineAndMix() async throws {
    let root = try queueRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let sources = QueueSources(files: [
      "a": try queueFixture(root, id: "a", value: 0.2),
      "b": try queueFixture(root, id: "b", value: 0.4),
      "c": try queueFixture(root, id: "c", value: 0.6),
    ])
    let stream = try await QueuePCMStream(
      items: [queueTrack("a"), queueTrack("b"), queueTrack("c")], crossfade: true,
      fadeSeconds: 0.25,
      acquire: { try await sources.get($0) }, release: { await sources.release($0) })
    let destination = root.appendingPathComponent("capture.wav")
    let result = try await QueuedAudioEngineProbe.render(stream: stream, output: destination)
    await stream.close()
    #expect(result.frames == 120000)
    #expect(result.peakScheduledFrames <= 8192)
    #expect(result.markers.map(\.switchFrame) == [0, 42000, 78000])
    let file = try AVAudioFile(forReading: destination)
    let buffer = AVAudioPCMBuffer(
      pcmFormat: file.processingFormat, frameCapacity: AVAudioFrameCount(file.length))!
    try file.read(into: buffer)
    for frame in 0..<120000 {
      let expected: Float
      if frame < 36000 {
        expected = 0.2
      } else if frame < 48000 {
        expected = 0.2 + 0.2 * Float(frame - 36000) / 11999
      } else if frame < 72000 {
        expected = 0.4
      } else if frame < 84000 {
        expected = 0.4 + 0.2 * Float(frame - 72000) / 11999
      } else {
        expected = 0.6
      }
      #expect(abs(buffer.floatChannelData![0][frame] - expected) < 0.00001)
    }
    #expect(await sources.active.isEmpty)
  }
  @Test func threePartAlbumCapturePreservesEveryUniqueSample() async throws {
    let root = try queueRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let format = AVAudioFormat(standardFormatWithSampleRate: 48000, channels: 2)!
    var paths: [String: URL] = [:]
    var items: [PlaybackOccurrence] = []
    for part in 0..<3 {
      let id = "part-\(part)"
      let url = root.appendingPathComponent(id + ".wav")
      let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 4800)!
      buffer.frameLength = 4800
      for channel in 0..<2 {
        for frame in 0..<4800 {
          buffer.floatChannelData![channel][frame] = Float(part * 4800 + frame) / 14400
        }
      }
      do {
        let file = try AVAudioFile(forWriting: url, settings: format.settings)
        try file.write(from: buffer)
      }
      paths[id] = url
      items.append(queueTrack(id, album: "continuous", number: part + 1))
    }
    let sources = QueueSources(files: paths)
    let stream = try await QueuePCMStream(
      items: items, crossfade: true,
      acquire: { try await sources.get($0) }, release: { await sources.release($0) })
    let destination = root.appendingPathComponent("capture.wav")
    let result = try await QueuedAudioEngineProbe.render(stream: stream, output: destination)
    await stream.close()
    #expect(result.frames == 14400)
    #expect(result.markers.map(\.switchFrame) == [0, 4800, 9600])
    let file = try AVAudioFile(forReading: destination)
    let buffer = AVAudioPCMBuffer(pcmFormat: file.processingFormat, frameCapacity: 14400)!
    try file.read(into: buffer)
    var maximumError: Float = 0
    for frame in 0..<14400 {
      maximumError = max(
        maximumError, abs(buffer.floatChannelData![0][frame] - Float(frame) / 14400))
    }
    #expect(maximumError < 0.0000001)
    #expect(await sources.active.isEmpty)
  }
  @Test func veryShortRepeatedOccurrencesDoNotReplayConsumedHeads() async throws {
    let root = try queueRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let sources = QueueSources(files: [
      "a": try queueFixture(root, id: "a", value: 0.2, frames: 2400)
    ])
    let items = [queueTrack("a"), queueTrack("a"), queueTrack("a")]
    let stream = try await QueuePCMStream(
      items: items, crossfade: true, fadeSeconds: 0.25,
      acquire: { try await sources.get($0) }, release: { await sources.release($0) })
    var frames = 0
    var markers: [QueueAudioMarker] = []
    while let block = try await stream.next() {
      frames += block.pcm.frameCount
      markers += block.markers
    }
    await stream.close()
    #expect(frames == 4800)
    #expect(markers.map(\.index) == [0, 1, 2])
    #expect(Set(items.map(\.id)).count == 3)
    #expect(await sources.acquired.count == 3)
    #expect(await sources.active.isEmpty)
  }
  @Test func orderedAlbumStaysGaplessAndSeekStartsAtRequestedFrame() async throws {
    let root = try queueRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let sources = QueueSources(files: [
      "a": try queueFixture(root, id: "a", value: 0.2),
      "b": try queueFixture(root, id: "b", value: 0.4),
    ])
    let stream = try await QueuePCMStream(
      items: [
        queueTrack("a", album: "album", number: 1), queueTrack("b", album: "album", number: 2),
      ], startSeconds: 0.5, crossfade: true,
      acquire: { try await sources.get($0) }, release: { await sources.release($0) })
    var samples: [Float] = []
    var markers: [QueueAudioMarker] = []
    while let block = try await stream.next() {
      samples += block.pcm.channels[0]
      markers += block.markers
    }
    await stream.close()
    #expect(samples.count == 72000)
    #expect(samples[23999] == 0.2)
    #expect(samples[24000] == 0.4)
    #expect(markers.map(\.originFrame) == [-24000, 24000])
    #expect(markers.map(\.switchFrame) == [0, 24000])
    #expect(await sources.active.isEmpty)
  }
  @Test @MainActor func pauseDuringPreparationSurvivesCompletionAndSeekPreservesIntent()
    async throws
  {
    let root = try queueRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let sources = QueueSources(
      files: ["a": try queueFixture(root, id: "a", value: 0.2)], gated: true)
    let output = SilentQueueOutput()
    let session = try RealtimeQueueSession(output: output)
    session.replace(
      [queueTrack("a")], acquire: { try await sources.get($0) },
      release: { await sources.release($0) })
    while !(await sources.entered) { await Task.yield() }
    session.pause()
    await sources.open()
    try await waitQueue { session.snapshot.state == .paused }
    #expect(output.starts == 0)
    #expect(session.peakScheduledFrames <= 8192)
    session.seek(0.5)
    try await waitQueue { session.snapshot.state == .paused && session.snapshot.elapsed >= 0.5 }
    #expect(output.starts == 0)
    session.resume()
    #expect(session.snapshot.wantsPlayback)
    #expect(output.playing)
    await session.stopAndWait()
    #expect(await sources.active.isEmpty)
  }
  @Test func screenOffPlaybackOnlyPublishesMeaningfulChanges() {
    let previous = QueuePlaybackSnapshot(state: .playing, elapsed: 1, duration: 300)
    var next = previous
    next.elapsed = 120
    #expect(!next.shouldPublish(over: previous, sceneActive: false))
    #expect(next.shouldPublish(over: previous, sceneActive: true))
    next.state = .paused
    #expect(next.shouldPublish(over: previous, sceneActive: false))
    next = previous
    next.occurrenceID = UUID()
    #expect(next.shouldPublish(over: previous, sceneActive: false))
    next = previous
    next.error = "Fixture failure"
    #expect(next.shouldPublish(over: previous, sceneActive: false))
  }
  @Test @MainActor func pausedQueueStopsPublishingUntilResumedAndStillReleasesSources() async throws {
    let root = try queueRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let sources = QueueSources(files: ["a": try queueFixture(root, id: "a", value: 0.2)])
    let output = SilentQueueOutput()
    let session = try RealtimeQueueSession(output: output)
    var updates = 0
    session.onUpdate = { _ in updates += 1 }
    session.replace(
      [queueTrack("a")], acquire: { try await sources.get($0) },
      release: { await sources.release($0) })
    try await waitQueue { session.snapshot.state == .playing }
    session.pause()
    let pausedUpdates = updates
    let buffered = output.pending.count
    try await Task.sleep(for: .milliseconds(350))
    #expect(updates == pausedUpdates)
    #expect(output.pending.count == buffered)
    session.resume()
    try await waitQueue { updates > pausedUpdates }
    output.consume()
    try await waitQueue { output.pending.count == buffered }
    await session.stopAndWait()
    #expect(await sources.active.isEmpty)
  }
  @Test @MainActor func pausedRouteRecoveryIgnoresDiscardedBuffersAndKeepsPosition() async throws {
    let root = try queueRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let sources = QueueSources(files: ["a": try queueFixture(root, id: "a", value: 0.2)])
    let output = SilentQueueOutput()
    let session = try RealtimeQueueSession(output: output)
    session.replace(
      [queueTrack("a")], acquire: { try await sources.get($0) },
      release: { await sources.release($0) })
    try await waitQueue { session.snapshot.state == .playing }
    output.consume()
    session.pause()
    let saved = session.snapshot.elapsed
    #expect(saved > 0)
    let discarded = output.pending.map { $0.1 }
    // A hardware configuration change discards the old output's buffers.
    output.stop()
    session.recoverOutput()
    try await waitQueue { session.snapshot.state == .paused }
    #expect(abs(session.snapshot.elapsed - saved) < 0.0001)
    for completion in discarded { completion() }
    session.resume()
    let deadline = ContinuousClock.now + .seconds(5)
    while session.snapshot.state != .ended {
      guard ContinuousClock.now < deadline else { throw AudioPipelineFailure.stalled }
      output.consume()
      try await Task.sleep(for: .milliseconds(2))
    }
    #expect(session.peakScheduledFrames <= 8192)
    #expect(abs(session.snapshot.elapsed - 1) < 0.0001)
    await session.stopAndWait()
    #expect(await sources.active.isEmpty)
  }
  @Test @MainActor func failedAudioRestartStopsPausedQueueAndReleasesSources() async throws {
    let root = try queueRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let sources = QueueSources(files: ["a": try queueFixture(root, id: "a", value: 0.2)])
    let output = SilentQueueOutput()
    let session = try RealtimeQueueSession(output: output)
    session.replace(
      [queueTrack("a")], autoplay: false, acquire: { try await sources.get($0) },
      release: { await sources.release($0) })
    try await waitQueue { session.snapshot.state == .paused }
    output.resumeFails = true
    session.resume()
    #expect(session.snapshot.state == .failed)
    #expect(!session.snapshot.wantsPlayback && !output.playing)
    #expect(session.snapshot.error != nil)
    await session.stopAndWait()
    #expect(await sources.active.isEmpty)
  }
  @Test @MainActor func restoredQueueStartsPausedAtItsSavedPosition() async throws {
    let root = try queueRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let sources = QueueSources(
      files: [
        "a": try queueFixture(root, id: "a", value: 0.2, frames: 96000),
        "b": try queueFixture(root, id: "b", value: 0.4, frames: 96000),
      ])
    let output = SilentQueueOutput()
    let session = try RealtimeQueueSession(output: output)
    let second = PlaybackOccurrence(track: Track(id: "b", title: "b", artist: "Fixture", duration: 2))
    session.replace(
      [queueTrack("a"), second], startingAt: 1, startSeconds: 1.25, autoplay: false,
      acquire: { try await sources.get($0) }, release: { await sources.release($0) })
    try await waitQueue { session.snapshot.state == .paused && session.snapshot.elapsed >= 1.25 }
    #expect(session.snapshot.index == 1)
    #expect(output.starts == 0)
    await session.stopAndWait()
    #expect(await sources.active.isEmpty)
  }
  enum RestoredAction: String, CaseIterable, Sendable {
    case play, next, previousRestarts, previousGoesBack
  }
  /// A restored queue (paused, at a saved position, its resolver slow to
  /// answer) started the way the app's seamless mode starts it: Play, Next or
  /// Previous chooses the song and position first, and only that song is
  /// fetched. Next never carries the saved position to the following song,
  /// and Previous decides from the saved position, not from zero.
  @Test(arguments: RestoredAction.allCases) @MainActor
  func aRestoredQueueStartsOnlyTheChosenSong(_ action: RestoredAction) async throws {
    let root = try queueRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let sources = QueueSources(
      files: [
        "a": try queueFixture(root, id: "a", value: 0.2, frames: 48000 * 5),
        "b": try queueFixture(root, id: "b", value: 0.4, frames: 48000 * 5),
        "c": try queueFixture(root, id: "c", value: 0.6, frames: 48000 * 5),
      ], gated: true)
    var queue = PlaybackQueue()
    queue.replace(
      ["a", "b", "c"].map { Track(id: $0, title: $0, artist: "Fixture", duration: 5) })
    queue.select(index: 1)
    var start = QueueStart()
    let saved = action == .previousGoesBack ? 2.0 : 4.5
    start.restore(queue, at: saved, waiting: true)
    #expect(start.waiting && start.seconds(for: queue.current) == saved)
    switch action {
    case .play: break
    case .next:
      let moved = start.next(&queue, repeatMode: .off)
      #expect(moved)
    case .previousRestarts, .previousGoesBack: start.previous(&queue)
    }
    let began = start.begin()
    #expect(began)
    let expected: (id: String, index: Int, seconds: Double) =
      switch action {
      case .play: ("b", 1, saved)
      case .next: ("c", 2, 0)
      case .previousRestarts: ("b", 1, 0)
      case .previousGoesBack: ("a", 0, 0)
      }
    let output = SilentQueueOutput()
    let session = try RealtimeQueueSession(output: output)
    let seconds = start.take(for: queue.current)
    session.replace(
      queue.items, startingAt: queue.index, startSeconds: seconds,
      autoplay: false, acquire: { try await sources.get($0) },
      release: { await sources.release($0) })
    while !(await sources.entered) { await Task.yield() }
    #expect(await sources.acquired == [expected.id])
    #expect(session.snapshot.elapsed == expected.seconds)
    await sources.open()
    try await waitQueue { session.snapshot.state == .paused }
    #expect(session.snapshot.index == expected.index)
    #expect(abs(session.snapshot.elapsed - expected.seconds) < 0.05)
    #expect(output.starts == 0)
    #expect(Array(await sources.acquired.prefix(1)) == [expected.id])
    await session.stopAndWait()
  }
  /// Streaming mode takes the start when it begins resolving a song. A Next
  /// while that resolve is slow starts the following song from zero, and
  /// coming back does not reuse the position: it belonged to one preparation.
  @Test func aSavedPositionNeverCrossesToAnotherSong() {
    var queue = PlaybackQueue()
    queue.replace(["a", "b"].map { Track(id: $0, title: $0, artist: "Fixture", duration: 60) })
    var start = QueueStart()
    start.restore(queue, at: 30, waiting: false)
    let began = start.begin()
    #expect(!began)
    let first = queue.current
    let taken = start.take(for: first)
    #expect(taken == 30)
    queue.next()
    let following = start.take(for: queue.current)
    #expect(following == 0)
    _ = queue.previous(elapsed: 0)
    let again = start.take(for: first)
    #expect(queue.current == first && again == 0)
    // A position set for one song is not one for another.
    start.seek(queue, to: 12)
    #expect(start.seconds(for: queue.items[1]) == 0)
    #expect(start.seconds(for: queue.current) == 12)
  }
  @Test func nextAtTheEndOfAWaitingQueueKeepsItsPlace() {
    var queue = PlaybackQueue()
    queue.replace(["a", "b"].map { Track(id: $0, title: $0, artist: "Fixture", duration: 60) })
    queue.select(index: 1)
    var start = QueueStart()
    start.restore(queue, at: 20, waiting: true)
    let ended = !start.next(&queue, repeatMode: .off)
    #expect(ended)
    #expect(start.waiting && queue.index == 1 && start.seconds(for: queue.current) == 20)
    let before = queue.items.map(\.id)
    let wrapped = start.next(&queue, repeatMode: .all)
    #expect(wrapped)
    #expect(queue.index == 0 && start.seconds(for: queue.current) == 0)
    #expect(Set(queue.items.map(\.id)).isDisjoint(with: before))
  }
  @Test @MainActor func liveLookaheadStartsSmallThenBuildsCushion() async throws {
    let root = try queueRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let sources = QueueSources(files: ["a": try queueFixture(root, id: "a", value: 0.2)])
    let output = SilentQueueOutput()
    let session = try RealtimeQueueSession(output: output, lookaheadSeconds: 0.5)
    session.replace(
      [queueTrack("a")], acquire: { try await sources.get($0) },
      release: { await sources.release($0) })
    try await waitQueue { session.snapshot.state == .playing }
    // Nothing has played yet, so the session keeps filling to half a second.
    try await waitQueue { session.peakScheduledFrames >= 24000 }
    #expect(session.peakScheduledFrames <= 24000 + 2048)
    await session.stopAndWait()
    #expect(await sources.active.isEmpty)
  }
  @Test @MainActor func aSourceFallingBehindRefillsBeforePlayingOn() async throws {
    let root = try queueRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let file = try queueFixture(root, id: "a", value: 0.2, frames: 48000 * 3)
    let bytes = try MeteredBytes(file: file, allowed: 4096 + 384_000 / 4)
    let output = SilentQueueOutput()
    let session = try RealtimeQueueSession(output: output, rebufferSeconds: 0.5)
    session.replace(
      [queueTrack("a")],
      acquire: { _ in PreparedAudioSource(file: file, byteCount: bytes.length, bytes: bytes) },
      release: { _ in })
    try await waitQueue { session.snapshot.state == .playing }
    // Play everything that has arrived (a quarter second): the source is behind.
    let drained = ContinuousClock.now + .seconds(2)
    while ContinuousClock.now < drained {
      output.consume()
      if output.pending.isEmpty, bytes.waiting { break }
      try await Task.sleep(for: .milliseconds(5))
    }
    #expect(output.pending.isEmpty)
    // A little more arrives: not enough to go on without stuttering.
    bytes.allow(4096 + 384_000 / 2)
    try await Task.sleep(for: .milliseconds(200))
    #expect(output.pending.isEmpty)
    try await waitQueue { session.snapshot.state == .buffering }
    // Once half a second is in hand, it is scheduled in one go.
    bytes.allow(bytes.length)
    try await waitQueue { output.pending.reduce(0) { $0 + $1.0 } >= 24000 }
    await session.stopAndWait()
  }
  /// An MP3 that states no length and drops from 320 to 32 kbps after five
  /// seconds is estimated at under nine seconds from its opening. A seek far
  /// past the estimate as soon as it is ready lands there; playing runs past
  /// it, a seek back works before the song has arrived, the duration is
  /// corrected once every byte is here, and from then on it bounds a seek.
  @Test(.enabled(if: AudioFixtures.ffmpeg != nil)) @MainActor
  func anUnderestimatedSongPlaysAndSeeksPastItsEstimate() async throws {
    let root = try queueRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let file = root.appendingPathComponent("varying.mp3")
    try AudioFixtures.headerlessMP3(at: file, parts: [(5, 320), (35, 32)])
    let bytes = try MeteredBytes(file: file, allowed: 0)
    bytes.allow(bytes.length - 8 * 1024)
    let output = SilentQueueOutput()
    let session = try RealtimeQueueSession(output: output, lookaheadSeconds: 2, startSeconds: 0.5)
    let track = Track(id: "varying", title: "varying", artist: "Fixture", duration: 8)
    session.replace(
      [PlaybackOccurrence(track: track)],
      acquire: { _ in PreparedAudioSource(file: file, byteCount: bytes.length, bytes: bytes) },
      release: { _ in })
    @MainActor func play(until done: () -> Bool) async throws {
      let deadline = ContinuousClock.now + .seconds(15)
      while !done() {
        guard ContinuousClock.now < deadline else { throw AudioPipelineFailure.stalled }
        if output.pending.isEmpty {
          try await Task.sleep(for: .milliseconds(2))
        } else {
          output.consume()
          await Task.yield()
        }
      }
    }
    try await waitQueue { session.snapshot.state == .playing }
    #expect(session.snapshot.durationIsEstimate && session.snapshot.duration < 15)
    // Forward past the estimate before playback has extended it.
    session.seek(20)
    #expect(session.snapshot.durationIsEstimate && session.snapshot.elapsed == 20)
    try await waitQueue { session.snapshot.state == .playing }
    #expect(abs(session.snapshot.elapsed - 20) < 0.1)
    #expect(session.snapshot.durationIsEstimate && session.snapshot.duration >= 20)
    try await play { session.snapshot.elapsed > 21 }
    // Back to the start, then play past the estimate.
    session.seek(0)
    try await waitQueue { session.snapshot.state == .playing && session.snapshot.elapsed < 1 }
    try await play { session.snapshot.elapsed > 12 }
    #expect(session.snapshot.durationIsEstimate)
    #expect(session.snapshot.duration >= session.snapshot.elapsed)
    #expect(!bytes.complete)
    // Back a little, still past the estimate, with the end not here yet.
    session.seek(11)
    try await waitQueue { session.snapshot.state == .playing }
    #expect(abs(session.snapshot.elapsed - 11) < 0.1)
    try await play { session.snapshot.elapsed > 13 }
    // Every byte arrives: the duration is measured, then a seek near the end.
    bytes.allow(bytes.length)
    try await play { !session.snapshot.durationIsEstimate }
    #expect(abs(session.snapshot.duration - 40.07) < 0.1)
    session.seek(30)
    try await waitQueue { session.snapshot.state == .playing }
    #expect(abs(session.snapshot.elapsed - 30) < 0.1)
    #expect(!session.snapshot.durationIsEstimate)
    // An exact duration still bounds a seek.
    session.seek(120)
    #expect(abs(session.snapshot.elapsed - 40.07) < 0.1)
    session.seek(30)
    try await waitQueue { session.snapshot.state == .playing }
    try await play { session.snapshot.state == .ended }
    #expect(abs(session.snapshot.duration - 40.07) < 0.1)
    #expect(session.snapshot.elapsed == session.snapshot.duration)
    await session.stopAndWait()
  }
  /// A seek past an estimate can also be past the real end. The song then
  /// ends there, measured at its real length, and the next one plays (or,
  /// as the last song, the queue ends showing that length, no longer an
  /// estimate): no failure, no fallback, and no length taken from the request.
  @Test(.enabled(if: AudioFixtures.ffmpeg != nil), arguments: [false, true]) @MainActor
  func aSeekPastTheRealEndOfAnEstimatedSongEndsItThere(last: Bool) async throws {
    let root = try queueRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let file = root.appendingPathComponent("varying.mp3")
    try AudioFixtures.headerlessMP3(at: file, parts: [(5, 320), (35, 32)])
    let following = try queueFixture(root, id: "b", value: 0.4, frames: 48000 * 2)
    let bytes = try MeteredBytes(file: file, allowed: 0)
    bytes.allow(bytes.length - 8 * 1024)
    let output = SilentQueueOutput()
    let session = try RealtimeQueueSession(output: output)
    let varying = PlaybackOccurrence(
      track: Track(id: "varying", title: "varying", artist: "Fixture", duration: 8))
    session.replace(
      last ? [varying] : [varying, queueTrack("b")],
      acquire: { item in
        item.track.id == "b"
          ? PreparedAudioSource(file: following, byteCount: 0)
          : PreparedAudioSource(file: file, byteCount: bytes.length, bytes: bytes)
      }, release: { _ in })
    try await waitQueue { session.snapshot.state == .playing }
    #expect(session.snapshot.durationIsEstimate)
    // The transfer brings the rest while the seek looks for the end.
    Task {
      try await Task.sleep(for: .milliseconds(300))
      bytes.allow(bytes.length)
    }
    session.seek(120)
    #expect(session.snapshot.elapsed == 120)
    let deadline = ContinuousClock.now + .seconds(15)
    while last ? session.snapshot.state != .ended : session.snapshot.index != 1 {
      guard ContinuousClock.now < deadline, session.snapshot.state != .failed else {
        Issue.record("state \(session.snapshot.state) \(session.snapshot.error ?? "")")
        break
      }
      output.consume()
      try await Task.sleep(for: .milliseconds(2))
    }
    if last {
      #expect(session.snapshot.state == .ended && session.snapshot.index == 0)
      #expect(abs(session.snapshot.duration - 40.07) < 0.1)
      #expect(!session.snapshot.durationIsEstimate)
      #expect(session.snapshot.elapsed == session.snapshot.duration)
    } else {
      #expect(session.snapshot.index == 1 && session.snapshot.state != .failed)
      #expect(session.snapshot.duration == 2)
    }
    await session.stopAndWait()
  }
  @Test @MainActor func queueReplacementCancelsPendingSourcesAndIgnoresOldCallbacks() async throws {
    let root = try queueRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let sources = QueueSources(files: [
      "a": try queueFixture(root, id: "a", value: 0.2),
      "b": try queueFixture(root, id: "b", value: 0.4),
    ])
    let output = SilentQueueOutput()
    let owned = try RealtimeQueueSession(output: output)
    owned.replace(
      [queueTrack("a")], acquire: { try await sources.get($0) },
      release: { await sources.release($0) })
    try await waitQueue { owned.snapshot.state == .playing }
    let callbacks = output.pending.map { $0.1 }
    let replacement = queueTrack("b")
    owned.replace(
      [replacement], autoplay: false, acquire: { try await sources.get($0) },
      release: { await sources.release($0) })
    for callback in callbacks { callback() }
    try await waitQueue { owned.snapshot.state == .paused }
    #expect(owned.snapshot.occurrenceID == replacement.id)
    #expect(owned.peakScheduledFrames <= 8192)
    await owned.stopAndWait()
    #expect(await sources.active.isEmpty)
  }
  @Test @MainActor func skipDuringPreparationCancelsOldOccurrenceAndPreservesPausedIntent()
    async throws
  {
    let root = try queueRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let sources = QueueSources(
      files: [
        "a": try queueFixture(root, id: "a", value: 0.2),
        "b": try queueFixture(root, id: "b", value: 0.4),
      ], gated: true)
    let output = SilentQueueOutput()
    let session = try RealtimeQueueSession(output: output)
    let items = [queueTrack("a"), queueTrack("b")]
    session.replace(
      items, acquire: { try await sources.get($0) }, release: { await sources.release($0) })
    while !(await sources.entered) { await Task.yield() }
    session.pause()
    session.next()
    await sources.open()
    try await waitQueue { session.snapshot.state == .paused }
    #expect(session.snapshot.occurrenceID == items[1].id)
    #expect(output.starts == 0)
    #expect(output.captured.allSatisfy { abs($0 - 0.4) < 0.00001 })
    await session.stopAndWait()
    #expect(await sources.active.isEmpty)
  }
  @Test @MainActor func selectingALaterSongPlaysFromItAndKeepsTheQueue() async throws {
    let root = try queueRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let sources = QueueSources(files: [
      "a": try queueFixture(root, id: "a", value: 0.2),
      "b": try queueFixture(root, id: "b", value: 0.4),
      "c": try queueFixture(root, id: "c", value: 0.6),
    ])
    let output = SilentQueueOutput()
    let session = try RealtimeQueueSession(output: output)
    let items = [queueTrack("a"), queueTrack("b"), queueTrack("c")]
    session.replace(
      items, acquire: { try await sources.get($0) }, release: { await sources.release($0) })
    try await waitQueue { session.snapshot.state == .playing }
    session.select(index: 2)
    try await waitQueue {
      session.snapshot.occurrenceID == items[2].id && session.snapshot.state == .playing
    }
    #expect(session.snapshot.index == 2)
    session.select(index: 7)  // Out of range: nothing changes.
    #expect(session.snapshot.occurrenceID == items[2].id)
    await session.stopAndWait()
    #expect(await sources.active.isEmpty)
  }
  @Test @MainActor func skipCancelsAwaitedUpcomingTransferInsteadOfWaitingForItsTimeout()
    async throws
  {
    let root = try queueRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let sources = QueueSources(
      files: [
        "a": try queueFixture(root, id: "a", value: 0.2, frames: 2400),
        "c": try queueFixture(root, id: "c", value: 0.6),
      ], delayed: "b")
    let output = SilentQueueOutput()
    let session = try RealtimeQueueSession(output: output)
    let items = [queueTrack("a"), queueTrack("b"), queueTrack("c")]
    session.replace(
      items, crossfade: true, acquire: { try await sources.get($0) },
      release: { await sources.release($0) })
    while !(await sources.entered) { await Task.yield() }
    session.pause()
    // Choose a replacement directly, so the deliberately unavailable source is no longer needed.
    session.replace(
      [items[2]], autoplay: false, acquire: { try await sources.get($0) },
      release: { await sources.release($0) })
    try await waitQueue { session.snapshot.state == .paused }
    #expect(session.snapshot.occurrenceID == items[2].id)
    #expect(output.captured.allSatisfy { abs($0 - 0.6) < 0.00001 })
    await session.stopAndWait()
    #expect(await sources.active.isEmpty)
  }
  @Test @MainActor func clockContinuesDuringUpcomingBufferingAndRecoveryPreservesPause()
    async throws
  {
    let root = try queueRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let sources = QueueSources(
      files: ["a": try queueFixture(root, id: "a", value: 0.2)], delayed: "b")
    let output = SilentQueueOutput()
    let session = try RealtimeQueueSession(output: output)
    session.replace(
      [queueTrack("a"), queueTrack("b")], acquire: { try await sources.get($0) },
      release: { await sources.release($0) })
    try await waitQueue { session.snapshot.state == .playing }
    let deadline = ContinuousClock.now + .seconds(5)
    while session.snapshot.state != .buffering {
      guard ContinuousClock.now < deadline else {
        await session.stopAndWait()
        throw AudioPipelineFailure.stalled
      }
      output.consume()
      try await Task.sleep(for: .milliseconds(10))
    }
    #expect(session.snapshot.elapsed > 0.9)
    session.pause()
    session.seek(0)
    try await waitQueue { session.snapshot.state == .paused }
    session.recoverOutput()
    try await waitQueue { session.snapshot.state == .paused }
    #expect(!session.snapshot.wantsPlayback)
    #expect(!output.playing)
    await session.stopAndWait()
    #expect(await sources.active.isEmpty)
  }
  @Test @MainActor func sourceFailureDrainsOutgoingAudioBeforeReportingFallback() async throws {
    let root = try queueRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let sources = QueueSources(
      files: ["a": try queueFixture(root, id: "a", value: 0.2)], failed: "b")
    let output = SilentQueueOutput()
    let owned = try RealtimeQueueSession(output: output)
    owned.replace(
      [queueTrack("a"), queueTrack("b")], crossfade: true, fadeSeconds: 0.25,
      acquire: { try await sources.get($0) }, release: { await sources.release($0) })
    let deadline = ContinuousClock.now + .seconds(5)
    while owned.snapshot.state != .failed {
      guard ContinuousClock.now < deadline else {
        await owned.stopAndWait()
        throw AudioPipelineFailure.stalled
      }
      output.consume()
      try await Task.sleep(for: .milliseconds(5))
    }
    #expect(output.captured.count == 48000)
    #expect(output.positionFrames == 48000)
    #expect(owned.snapshot.index == 0)
    #expect(owned.snapshot.error != nil)
    await owned.stopAndWait()
    #expect(await sources.active.isEmpty)
  }
}

@Suite struct UnavailableSongTests {
  @Test func anUnavailableUpcomingSongIsPassedOverWithoutAGap() async throws {
    let root = try queueRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let sources = QueueSources(
      files: [
        "a": try queueFixture(root, id: "a", value: 0.2),
        "c": try queueFixture(root, id: "c", value: 0.6),
      ], failed: "b")
    let stream = try await QueuePCMStream(
      items: [queueTrack("a"), queueTrack("b"), queueTrack("c")],
      acquire: { try await sources.get($0) }, release: { await sources.release($0) })
    var samples: [Float] = []
    var markers: [QueueAudioMarker] = []
    while let block = try await stream.next() {
      samples += block.pcm.channels[0]
      markers += block.markers
    }
    await stream.close()
    // "c" follows "a" directly, sample for sample.
    #expect(markers.map(\.index) == [0, 2])
    #expect(samples.count == 96000)
    #expect(abs(samples[48000] - 0.6) < 0.00001)
    #expect(await sources.acquired == ["a", "b", "c"])
    #expect(await sources.active.isEmpty)
  }
  /// Polls an actor-held condition (the stream's prefetch runs on its own).
  private func eventually(_ condition: () async -> Bool) async throws {
    let deadline = ContinuousClock.now + .seconds(5)
    while !(await condition()) {
      guard ContinuousClock.now < deadline else { throw AudioPipelineFailure.stalled }
      try await Task.sleep(for: .milliseconds(10))
    }
  }
  @Test func theNextViableSongIsPreparedAsSoonAsAPrefetchFails() async throws {
    let root = try queueRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let sources = QueueSources(
      files: [
        "a": try queueFixture(root, id: "a", value: 0.2),
        "d": try queueFixture(root, id: "d", value: 0.8),
      ], failing: ["b", "c"])
    let stream = try await QueuePCMStream(
      items: [queueTrack("a"), queueTrack("b"), queueTrack("c"), queueTrack("d")],
      acquire: { try await sources.get($0) }, release: { await sources.release($0) })
    // Nothing of "a" has been read: "d" is already being prepared.
    try await eventually { await sources.acquired == ["a", "b", "c", "d"] }
    #expect(await sources.peak == 2)
    var markers: [QueueAudioMarker] = []
    while let block = try await stream.next() { markers += block.markers }
    await stream.close()
    #expect(markers.map(\.index) == [0, 3])
    #expect(await sources.active.isEmpty)
  }
  @Test func closingDuringADelayedReplacementReleasesEverySource() async throws {
    let root = try queueRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let sources = QueueSources(
      files: ["a": try queueFixture(root, id: "a", value: 0.2)], failed: "b", delayed: "c")
    let stream = try await QueuePCMStream(
      items: [queueTrack("a"), queueTrack("b"), queueTrack("c")],
      acquire: { try await sources.get($0) }, release: { await sources.release($0) })
    try await eventually { await sources.entered }
    let started = ContinuousClock.now
    await stream.close()
    // Closing does not wait out the 30 s replacement. Generous: a loaded
    // shared CI runner took 2.2 s here (25 Oct).
    #expect(ContinuousClock.now - started < .seconds(10))
    #expect(await sources.acquired == ["a", "b", "c"])
    #expect(await sources.active.isEmpty)
  }
  @Test func repeatAllPassesOverAFailedSongAcrossTheWrap() async throws {
    let root = try queueRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let sources = QueueSources(
      files: [
        "a": try queueFixture(root, id: "a", value: 0.2, frames: 4800),
        "b": try queueFixture(root, id: "b", value: 0.4, frames: 4800),
      ], failed: "c")
    let stream = try await QueuePCMStream(
      items: [queueTrack("a"), queueTrack("b"), queueTrack("c")], startingAt: 1,
      repeatMode: .all,
      acquire: { try await sources.get($0) }, release: { await sources.release($0) })
    var markers: [QueueAudioMarker] = []
    while markers.count < 3, let block = try await stream.next() { markers += block.markers }
    await stream.close()
    #expect(markers.map(\.index) == [1, 0, 1])
    #expect(await sources.active.isEmpty)
  }
  /// Reads the whole stream at once, so the current song ends before the
  /// upcoming one fails: the replacement must play once, at its own position.
  private func drain(_ stream: QueuePCMStream, limit: Int = .max) async throws -> (
    markers: [QueueAudioMarker], frames: Int, samples: [Float]
  ) {
    var markers: [QueueAudioMarker] = []
    var samples: [Float] = []
    while markers.count < limit, let block = try await stream.next() {
      markers += block.markers
      samples += block.pcm.channels[0]
    }
    return (markers, samples.count, samples)
  }
  @Test func aLateFailurePlaysTheReplacementOnce() async throws {
    let root = try queueRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let sources = QueueSources(
      files: [
        "a": try queueFixture(root, id: "a", value: 0.2, frames: 256),
        "c": try queueFixture(root, id: "c", value: 0.6, frames: 256),
      ], slowFailures: ["b"])
    let stream = try await QueuePCMStream(
      items: [queueTrack("a"), queueTrack("b"), queueTrack("c")], blockFrames: 256,
      acquire: { try await sources.get($0) }, release: { await sources.release($0) })
    let result = try await drain(stream)
    await stream.close()
    #expect(result.markers.map(\.index) == [0, 2])
    #expect(result.frames == 512)
    #expect(await sources.acquired == ["a", "b", "c"])
    #expect(await sources.active.isEmpty)
  }
  @Test func severalLateFailuresInARowAreAllPassedOver() async throws {
    let root = try queueRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let sources = QueueSources(
      files: [
        "a": try queueFixture(root, id: "a", value: 0.2, frames: 256),
        "d": try queueFixture(root, id: "d", value: 0.8, frames: 256),
      ], slowFailures: ["b", "c"])
    let stream = try await QueuePCMStream(
      items: [queueTrack("a"), queueTrack("b"), queueTrack("c"), queueTrack("d")],
      blockFrames: 256,
      acquire: { try await sources.get($0) }, release: { await sources.release($0) })
    let result = try await drain(stream)
    await stream.close()
    #expect(result.markers.map(\.index) == [0, 3])
    #expect(result.markers.map(\.occurrenceID).allSatisfy { $0 != nil })
    #expect(result.frames == 512)
    #expect(await sources.active.isEmpty)
  }
  @Test func aLateFailureAcrossTheRepeatWrapKeepsPositions() async throws {
    let root = try queueRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let sources = QueueSources(
      files: [
        "a": try queueFixture(root, id: "a", value: 0.2, frames: 256),
        "b": try queueFixture(root, id: "b", value: 0.4, frames: 256),
      ], slowFailures: ["c"])
    let stream = try await QueuePCMStream(
      items: [queueTrack("a"), queueTrack("b"), queueTrack("c")], startingAt: 1,
      repeatMode: .all, blockFrames: 256,
      acquire: { try await sources.get($0) }, release: { await sources.release($0) })
    let result = try await drain(stream, limit: 3)
    await stream.close()
    #expect(result.markers.map(\.index) == [1, 0, 1])
    #expect(result.frames == 768)
    #expect(await sources.active.isEmpty)
  }
  @Test func aLateFailureCrossfadesIntoTheReplacement() async throws {
    let root = try queueRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let sources = QueueSources(
      files: [
        "a": try queueFixture(root, id: "a", value: 0.2),
        "c": try queueFixture(root, id: "c", value: 0.6),
      ], slowFailures: ["b"])
    let stream = try await QueuePCMStream(
      items: [queueTrack("a"), queueTrack("b"), queueTrack("c")], crossfade: true,
      fadeSeconds: 0.25,
      acquire: { try await sources.get($0) }, release: { await sources.release($0) })
    let result = try await drain(stream)
    await stream.close()
    #expect(result.markers.map(\.index) == [0, 2])
    #expect(result.markers.map(\.originFrame) == [0, 36000])
    #expect(result.markers.map(\.switchFrame) == [0, 42000])
    #expect(result.frames == 84000)
    #expect(abs(result.samples[83999] - 0.6) < 0.00001)
    #expect(await sources.active.isEmpty)
  }
  @Test func closingWhileWaitingOnALateReplacementReleasesEverything() async throws {
    let root = try queueRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let sources = QueueSources(
      files: ["a": try queueFixture(root, id: "a", value: 0.2, frames: 256)],
      delayed: "c", slowFailures: ["b"])
    let stream = try await QueuePCMStream(
      items: [queueTrack("a"), queueTrack("b"), queueTrack("c")], blockFrames: 256,
      acquire: { try await sources.get($0) }, release: { await sources.release($0) })
    let reader = Task { try await drain(stream) }
    try await eventually { await sources.entered }
    let started = ContinuousClock.now
    await stream.close()
    #expect(ContinuousClock.now - started < .seconds(2))
    await #expect(throws: (any Error).self) { _ = try await reader.value }
    #expect(await sources.acquired == ["a", "b", "c"])
    #expect(await sources.active.isEmpty)
  }
  @Test func repeatAllWithNothingElsePlayableEndsAfterTheCurrentSong() async throws {
    let root = try queueRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let sources = QueueSources(
      files: ["a": try queueFixture(root, id: "a", value: 0.2)], failing: ["b", "c"])
    let stream = try await QueuePCMStream(
      items: [queueTrack("a"), queueTrack("b"), queueTrack("c")], repeatMode: .all,
      acquire: { try await sources.get($0) }, release: { await sources.release($0) })
    var frames = 0
    await #expect(throws: AudioSourceFailure.self) {
      while let block = try await stream.next() { frames += block.pcm.frameCount }
    }
    await stream.close()
    #expect(frames == 48000)
    // Never retried "a" as its own replacement.
    #expect(await sources.acquired == ["a", "b", "c"])
    #expect(await sources.active.isEmpty)
  }
}

@Suite struct RepeatQueueTests {
  @Test func repeatAllKeepsBoundedSourcesAndContinuousFramesAcrossWrap() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: root) }
    let one = try queueFixture(root, id: "one", value: 0.2, frames: 1024)
    let two = try queueFixture(root, id: "two", value: 0.4, frames: 1024)
    let sources = QueueSources(files: ["one": one, "two": two])
    let items = ["one", "two"].map {
      PlaybackOccurrence(track: Track(id: $0, title: $0, artist: "Artist"))
    }
    let stream = try await QueuePCMStream(
      items: items, repeatMode: .all, blockFrames: 256, acquire: { try await sources.get($0) },
      release: { await sources.release($0) })
    var markers: [QueueAudioMarker] = []
    var samples: [Float] = []
    while samples.count < 4096 {
      let block = try #require(await stream.next())
      samples += block.pcm.channels[0]
      markers += block.markers
    }
    await stream.close()
    #expect(markers.prefix(4).map(\.index) == [0, 1, 0, 1])
    #expect(Set(markers.prefix(4).compactMap(\.occurrenceID)).count == 4)
    #expect(abs(samples[1023] - 0.2) < 0.001)
    #expect(abs(samples[1024] - 0.4) < 0.001)
    #expect(abs(samples[2047] - 0.4) < 0.001)
    #expect(abs(samples[2048] - 0.2) < 0.001)
    #expect(await sources.peak <= 2)
    #expect(await sources.active.isEmpty)
  }
  @Test func repeatOneUsesGaplessBoundariesAndNewListeningOccurrences() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: root) }
    let file = try queueFixture(root, id: "one", value: 0.2, frames: 1024)
    let sources = QueueSources(files: ["one": file])
    let item = PlaybackOccurrence(track: Track(id: "one", title: "one", artist: "Artist"))
    let stream = try await QueuePCMStream(
      items: [item], crossfade: true, repeatMode: .one, blockFrames: 256,
      acquire: { try await sources.get($0) }, release: { await sources.release($0) })
    var markers: [QueueAudioMarker] = []
    var samples: [Float] = []
    while samples.count < 4096 {
      let block = try #require(await stream.next())
      samples += block.pcm.channels[0]
      markers += block.markers
    }
    await stream.close()
    #expect(markers.prefix(4).map(\.index) == [0, 0, 0, 0])
    #expect(Set(markers.prefix(4).compactMap(\.occurrenceID)).count == 4)
    #expect(samples.allSatisfy { abs($0 - 0.2) < 0.001 })
    #expect(await sources.peak <= 2)
    #expect(await sources.active.isEmpty)
  }
}
