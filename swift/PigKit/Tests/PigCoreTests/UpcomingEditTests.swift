#if canImport(AVFoundation)
  import AVFoundation
#endif
import Foundation
import PigDomain
import Testing

@testable import PigPlayback

// Queue edits while a song plays (#279): the songs after the playing one
// change without stopping the audio. Every sample of a fixture says which
// song it is and which frame of it, so a restart, a replay or a skipped
// stretch shows in the captured audio.

private let editRates: [Double] = [48000, 44100]
private let editCodes = ["a": 1, "b": 2, "c": 3, "d": 4, "e": 5, "x": 6, "s": 6, "t": 7]

private func rampValue(code: Int, frame: Int, frames: Int) -> Float {
  (Float(code) + Float(frame) / Float(frames)) / 8
}
private func rampSong(_ root: URL, code: Int, frames: Int, rate: Double) throws -> URL {
  #if canImport(AVFoundation)
    let url = root.appendingPathComponent("\(code).wav")
    let format = AVAudioFormat(standardFormatWithSampleRate: rate, channels: 2)!
    let file = try AVAudioFile(forWriting: url, settings: format.settings)
    let block = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 4096)!
    var written = 0
    while written < frames {
      let count = min(4096, frames - written)
      block.frameLength = AVAudioFrameCount(count)
      for channel in 0..<2 {
        for frame in 0..<count {
          block.floatChannelData![channel][frame] = rampValue(
            code: code, frame: written + frame, frames: frames)
        }
      }
      try file.write(from: block)
      written += count
    }
    return url
  #else
    // Linux stand-in decoder (scratch runs only): the same ramp, computed.
    return FakeAudio.url(code: code, frames: frames)
  #endif
}
private func rampSongs(_ root: URL, _ ids: [String], frames: [String: Int], rate: Double) throws
  -> [String: URL]
{
  var files: [String: URL] = [:]
  for id in ids {
    files[id] = try rampSong(root, code: editCodes[id]!, frames: frames[id] ?? Int(rate), rate: rate)
  }
  return files
}
private func decodeRamp(_ sample: Float, frames: Int) -> (code: Int, frame: Int) {
  let scaled = Double(sample) * 8
  let code = Int(scaled.rounded(.down))
  return (code, Int(((scaled - Double(code)) * Double(frames)).rounded()))
}
/// Where `samples` first differ from these stretches of songs played one
/// after another (each song `length` frames long), or nil when they match
/// sample for sample.
private func firstBreak<S: Collection>(
  _ samples: S, _ songs: [(id: String, frames: Range<Int>)], length: Int
) -> String? where S.Element == Float {
  var iterator = samples.makeIterator()
  var position = 0
  for song in songs {
    for frame in song.frames {
      guard let sample = iterator.next() else {
        return "ended at \(position), expecting \(song.id) frame \(frame)"
      }
      let heard = decodeRamp(sample, frames: length)
      if heard.code != editCodes[song.id]! || heard.frame != frame {
        return "at \(position): heard song \(heard.code) frame \(heard.frame),"
          + " expecting \(song.id) frame \(frame)"
      }
      position += 1
    }
  }
  return iterator.next() == nil ? nil : "more than \(position) samples"
}

private func editTrack(_ id: String) -> PlaybackOccurrence {
  PlaybackOccurrence(track: Track(id: id, title: id, artist: "Fixture", duration: 1))
}
private func editRoot() throws -> URL {
  let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
  try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
  return url
}

/// Sources that count what is held, and behave like the repository: a fetch
/// started while another is still running fails as busy (counted).
private actor EditSources {
  let files: [String: URL]
  var active: Set<UUID> = []
  var peak = 0
  var acquired: [String] = []
  var entered: Set<String> = []
  /// Songs whose fetch waits (until opened or cancelled).
  var held: Set<String>
  var running = 0
  var busy = 0
  init(files: [String: URL], held: Set<String> = []) {
    self.files = files
    self.held = held
  }
  func get(_ item: PlaybackOccurrence) async throws -> PreparedAudioSource {
    acquired.append(item.track.id)
    guard running == 0 else {
      busy += 1
      throw AudioSourceFailure.busy
    }
    running += 1
    defer { running -= 1 }
    entered.insert(item.track.id)
    while held.contains(item.track.id) { try await Task.sleep(for: .milliseconds(5)) }
    try Task.checkCancellation()
    let id = UUID()
    active.insert(id)
    peak = max(peak, active.count)
    return PreparedAudioSource(id: id, file: files[item.track.id]!, byteCount: 0)
  }
  func release(_ source: PreparedAudioSource) { active.remove(source.id) }
  func open(_ id: String) { held.remove(id) }
  func openAll() { held.removeAll() }
}

private func eventually(_ condition: @Sendable () async -> Bool) async throws {
  let deadline = ContinuousClock.now + .seconds(10)
  while !(await condition()) {
    guard ContinuousClock.now < deadline else { throw AudioPipelineFailure.stalled }
    try await Task.sleep(for: .milliseconds(2))
  }
}

@Suite struct UpcomingEditStreamTests {
  /// The stream itself, edits racing in any order: the newest version wins,
  /// every dropped fetch is released, and fetches never overlap.
  @Test(arguments: editRates) func editsRacingInAnyOrderLeaveTheNewest(rate: Double) async throws
  {
    let root = try editRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let short = Int(rate / 8)
    let ids = ["a", "b", "c", "d", "e"]
    let lengths = Dictionary(uniqueKeysWithValues: ids.map { ($0, short) })
    let sources = EditSources(
      files: try rampSongs(root, ids, frames: lengths, rate: rate), held: ["b", "c", "d", "e"])
    let items = ids.map(editTrack)
    let stream = try await QueuePCMStream(
      items: items, sampleRate: rate, acquire: { try await sources.get($0) },
      release: { await sources.release($0) })
    try await eventually { await sources.entered.contains("b") }
    let orders = ["acbde", "adbce", "aebcd", "abe", "adc"].map { order in
      order.map { id in items.first { $0.track.id == String(id) }! }
    }
    await withTaskGroup(of: Void.self) { group in
      for (offset, order) in orders.enumerated().reversed() {
        group.addTask {
          _ = await stream.replaceUpcoming(order, repeatMode: .off, version: offset + 1)
        }
      }
    }
    // A late older edit changes nothing.
    #expect(await stream.replaceUpcoming(orders[0], repeatMode: .off, version: 2) == .applied)
    await sources.openAll()
    var samples: [Float] = []
    var markers: [QueueAudioMarker] = []
    while let block = try await stream.next() {
      samples += block.pcm.channels[0]
      markers += block.markers
    }
    #expect(
      firstBreak(samples, [("a", 0..<short), ("d", 0..<short), ("c", 0..<short)], length: short)
        == nil)
    #expect(markers.map(\.index) == [0, 1, 2])
    #expect(markers.map(\.occurrenceID) == orders[4].map(\.id))
    #expect(await stream.passedOverSongs.isEmpty)
    #expect(await stream.replaceUpcoming(orders[4], repeatMode: .off, version: 9) == .tooLate)
    await stream.close()
    #expect(await sources.busy == 0)
    #expect(await sources.peak <= 2)
    #expect(await sources.active.isEmpty)
  }

  /// Closing straight after an edit still releases the fetch it dropped.
  @Test func closingRightAfterAnEditReleasesTheDroppedFetch() async throws {
    let root = try editRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let sources = EditSources(
      files: try rampSongs(root, ["a", "b", "c"], frames: [:], rate: 48000), held: ["b"])
    let items = ["a", "b", "c"].map(editTrack)
    let stream = try await QueuePCMStream(
      items: items, acquire: { try await sources.get($0) }, release: { await sources.release($0) })
    try await eventually { await sources.entered.contains("b") }
    #expect(
      await stream.replaceUpcoming([items[0], items[2]], repeatMode: .off, version: 1) == .applied)
    await stream.close()
    #expect(await sources.active.isEmpty)
    #expect(await sources.busy == 0)
  }

  /// The songs up to the one being decoded are already audio.
  @Test func changingTheDecodingSongIsTooLate() async throws {
    let root = try editRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let sources = EditSources(files: try rampSongs(root, ["a", "b"], frames: [:], rate: 48000))
    let items = ["a", "b"].map(editTrack)
    let stream = try await QueuePCMStream(
      items: items, acquire: { try await sources.get($0) }, release: { await sources.release($0) })
    #expect(await stream.replaceUpcoming([items[1]], repeatMode: .off, version: 1) == .tooLate)
    #expect(await stream.replaceUpcoming([editTrack("a")], repeatMode: .off, version: 2) == .tooLate)
    #expect(await stream.replaceUpcoming([items[0]], repeatMode: .off, version: 3) == .applied)
    await stream.close()
    #expect(await stream.replaceUpcoming(items, repeatMode: .off, version: 4) == .tooLate)
    #expect(await sources.active.isEmpty)
  }
}
