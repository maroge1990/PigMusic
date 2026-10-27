import AVFoundation
import Foundation
import PigDomain
import Testing

@testable import PigPlayback

private func marker(_ index: Int, at frame: Int64) -> QueueAudioMarker {
  QueueAudioMarker(index: index, switchFrame: frame, originFrame: frame, durationFrames: 48000)
}

@Test func blocksThatArriveFasterThanPlaybackAreNeverSilent() {
  var timeline = PlaybackTimeline(sampleRate: 1000)
  timeline.received(frames: 1000, markers: [marker(0, at: 0)], at: .milliseconds(800))
  timeline.received(frames: 1000, markers: [], at: .milliseconds(900))
  timeline.received(frames: 1000, markers: [marker(1, at: 2000)], at: .milliseconds(1000))
  #expect(timeline.firstSound == .milliseconds(800))
  #expect(timeline.songs.map(\.index) == [0, 1])
  #expect(timeline.songs.map(\.stalls) == [0, 0])
  #expect(timeline.songs.map(\.frames) == [2000, 1000])
  #expect(timeline.songs[1].firstAudio == .milliseconds(1000))
}

@Test func aLateBlockIsSilenceForTheSongItBelongsTo() {
  var timeline = PlaybackTimeline(sampleRate: 1000)
  // One second of audio at 0 s: the player runs dry at 1 s.
  timeline.received(frames: 1000, markers: [marker(0, at: 0)], at: .zero)
  // The next block comes at 1.5 s: half a second of silence in song 1.
  timeline.received(frames: 1000, markers: [], at: .milliseconds(1500))
  // Playback resumed at 1.5 s, so this one (due at 2.5 s) is on time.
  timeline.received(frames: 1000, markers: [], at: .milliseconds(2400))
  // Song 2 starts late: the gap is that song's wait, not song 1's.
  timeline.received(frames: 1000, markers: [marker(1, at: 3000)], at: .milliseconds(4000))
  #expect(timeline.songs[0].stalls == 1 && timeline.songs[0].silent == .milliseconds(500))
  #expect(timeline.songs[1].stalls == 1 && timeline.songs[1].silent == .milliseconds(500))
  // A marker re-sent for the playing song is not a new song; tiny gaps are ignored.
  timeline.received(frames: 1000, markers: [marker(1, at: 3000)], at: .milliseconds(5020))
  #expect(timeline.songs.count == 2 && timeline.songs[1].stalls == 1)
}

@Test func aFailureBelongsToTheSongsThatFailedNotTheNextOne() {
  // The first song could not be prepared: nothing was heard.
  #expect(PlaybackTimeline.failedSongs(start: 2, lastHeard: nil, unprepared: [2, 3]) == [2, 3])
  // Song 4 failed mid-song (a transfer or decode error): song 5 is healthy.
  #expect(PlaybackTimeline.failedSongs(start: 2, lastHeard: 4, unprepared: []) == [4])
  // None of the songs after 4 could be prepared.
  #expect(PlaybackTimeline.failedSongs(start: 2, lastHeard: 4, unprepared: [5, 6]) == [5, 6])
  // Failures from an earlier queue, or passed over before 4, do not count again.
  #expect(PlaybackTimeline.failedSongs(start: 2, lastHeard: 4, unprepared: [1, 3]) == [4])
}

/// A song's bytes that fail part-way, as a dropped transfer does.
private final class FailingBytes: AudioBytes, @unchecked Sendable {
  private let file: LocalAudioBytes
  private let failAt: Int
  init(file: URL, failAt: Int) throws {
    self.file = try LocalAudioBytes(file: file)
    self.failAt = failAt
  }
  var length: Int { file.length }
  var complete: Bool { false }
  func read(offset: Int, count: Int, into buffer: UnsafeMutableRawPointer, stop: ReadStop)
    throws -> Int
  {
    if offset + count > failAt, offset < length - 128 { throw AudioSourceFailure.unavailable }
    return try file.read(offset: offset, count: count, into: buffer, stop: stop)
  }
  func wake() {}
}

private func tone(_ root: URL, _ id: String, seconds: Int = 2) throws -> URL {
  let url = root.appendingPathComponent(id + ".wav")
  let format = AVAudioFormat(standardFormatWithSampleRate: 48000, channels: 2)!
  let output = try AVAudioFile(forWriting: url, settings: format.settings)
  let block = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 4800)!
  block.frameLength = 4800
  for channel in 0..<2 { block.floatChannelData![channel].update(repeating: 0.1, count: 4800) }
  for _ in 0..<(seconds * 10) { try output.write(from: block) }
  return url
}

/// Plays a queue as the harness does and says which song its failure is.
private func failure(
  _ items: [PlaybackOccurrence], source: @escaping @Sendable (Int) throws -> PreparedAudioSource
) async throws -> (failed: [Int], heard: [Int], unprepared: Set<Int>) {
  let unprepared = Unprepared()
  let stream = try await QueuePCMStream(
    items: items,
    acquire: { item in
      let index = items.firstIndex { $0.id == item.id }!
      do { return try source(index) } catch {
        await unprepared.add(index)
        throw error
      }
    }, release: { _ in })
  var timeline = PlaybackTimeline(sampleRate: 48000)
  do {
    while let block = try await stream.next() {
      timeline.received(frames: block.pcm.frameCount, markers: block.markers, at: .zero)
    }
  } catch {
    await stream.close()
    let failed = PlaybackTimeline.failedSongs(
      start: 0, lastHeard: timeline.songs.last?.index, unprepared: await unprepared.indices)
    return (failed, timeline.songs.map(\.index), await unprepared.indices)
  }
  await stream.close()
  return ([], timeline.songs.map(\.index), await unprepared.indices)
}
private actor Unprepared {
  var indices: Set<Int> = []
  func add(_ index: Int) { indices.insert(index) }
}

@Test func theRealQueueBlamesTheSongThatFailed() async throws {
  let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
  try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
  defer { try? FileManager.default.removeItem(at: root) }
  let files = try ["a", "b", "c"].map { try tone(root, $0) }
  let items = ["a", "b", "c"].map {
    PlaybackOccurrence(track: Track(id: $0, title: $0, artist: "Fixture", duration: 2))
  }
  // A song that cannot be prepared is passed over: the queue plays on.
  let skipped = try await failure(items) { index in
    if index == 1 { throw AudioSourceFailure.unavailable }
    return PreparedAudioSource(file: files[index], byteCount: 0)
  }
  #expect(skipped.failed == [] && skipped.heard == [0, 2] && skipped.unprepared == [1])
  // None of the rest can be prepared: the queue fails, and both are blamed.
  let rest = try await failure(items) { index in
    if index > 0 { throw AudioSourceFailure.unavailable }
    return PreparedAudioSource(file: files[index], byteCount: 0)
  }
  #expect(rest.failed == [1, 2] && rest.heard == [0])
  // The first song's transfer drops part-way: the healthy second is not blamed.
  let size = try LocalAudioBytes(file: files[0]).length
  let dropped = try await failure(items) { index in
    guard index == 0 else { return PreparedAudioSource(file: files[index], byteCount: 0) }
    let bytes = try FailingBytes(file: files[0], failAt: size / 2)
    return PreparedAudioSource(file: files[0], byteCount: size, bytes: bytes)
  }
  #expect(dropped.failed == [0] && dropped.heard == [0])
}
