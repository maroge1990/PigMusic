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
    // Past its end, a song added still follows, straight after.
    #expect(
      await stream.replaceUpcoming(orders[4] + [items[4]], repeatMode: .off, version: 9)
        == .applied)
    var added: [Float] = []
    while let block = try await stream.next() {
      added += block.pcm.channels[0]
      markers += block.markers
    }
    #expect(firstBreak(added, [("e", 0..<short)], length: short) == nil)
    #expect(markers.last?.index == 3 && markers.last?.originFrame == Int64(3 * short))
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

@MainActor private final class EditOutput: QueueAudioOutput {
  var positionFrames: Int64 = 0
  var pending: [(Int, @Sendable () -> Void)] = []
  var playing = false
  var stops = 0
  var captured: [Float] = []
  func configure(sampleRate: Double, blockFrames: Int) { positionFrames = 0 }
  func schedule(_ block: PCMBlock, completion: @escaping @Sendable () -> Void) {
    pending.append((block.frameCount, completion))
    captured.append(contentsOf: block.channels[0])
  }
  func resume() throws { playing = true }
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

@MainActor private func waitUntil(_ predicate: () -> Bool) async throws {
  let deadline = ContinuousClock.now + .seconds(10)
  while !predicate() {
    guard ContinuousClock.now < deadline else { throw AudioPipelineFailure.stalled }
    try await Task.sleep(for: .milliseconds(2))
  }
}
/// Plays everything scheduled until the queue ends.
@MainActor private func playToEnd(_ session: RealtimeQueueSession, _ output: EditOutput)
  async throws
{
  let deadline = ContinuousClock.now + .seconds(20)
  while session.snapshot.state != .ended {
    guard ContinuousClock.now < deadline, session.snapshot.state != .failed else {
      Issue.record("state \(session.snapshot.state) \(session.snapshot.error ?? "")")
      throw AudioPipelineFailure.stalled
    }
    output.consume()
    try await Task.sleep(for: .milliseconds(1))
  }
}

@Suite(.serialized) struct UpcomingEditSessionTests {
  @MainActor private func start(
    _ ids: [String], rate: Double, frames: [String: Int] = [:], held: Set<String> = [],
    crossfade: Bool = false, root: URL
  ) throws -> (RealtimeQueueSession, EditOutput, EditSources, [PlaybackOccurrence]) {
    let all = Set(ids).union(["a", "b", "c", "d", "e", "x", "s", "t"]).sorted()
    let sources = EditSources(
      files: try rampSongs(root, all, frames: frames, rate: rate), held: held)
    let output = EditOutput()
    let session = try RealtimeQueueSession(output: output, sampleRate: rate)
    let items = ids.map(editTrack)
    session.replace(
      items, crossfade: crossfade, fadeSeconds: 0.25, acquire: { try await sources.get($0) },
      release: { await sources.release($0) })
    return (session, output, sources, items)
  }

  /// Removing the next song mid-song: the playing song is never stopped or
  /// replayed, and the song after the removed one follows it gaplessly.
  @Test(arguments: editRates) @MainActor
  func removingTheNextSongNeverStopsThePlayingOne(rate: Double) async throws {
    let root = try editRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let (session, output, sources, items) = try start(["a", "b", "c"], rate: rate, root: root)
    try await waitUntil { session.snapshot.state == .playing }
    output.consume()
    let stops = output.stops
    session.updateUpcoming([items[0], items[2]])
    try await playToEnd(session, output)
    let length = Int(rate)
    #expect(output.stops == stops)
    #expect(
      firstBreak(output.captured, [("a", 0..<length), ("c", 0..<length)], length: length) == nil)
    #expect(session.snapshot.index == 1 && session.snapshot.occurrenceID == items[2].id)
    #expect(await sources.busy == 0)
    #expect(await sources.peak <= 2)
    await session.stopAndWait()
    #expect(await sources.active.isEmpty)
  }

  /// Cross-fade on, the next song's fetch still waiting: removing it drops
  /// the fetch, and the song after it fades in instead.
  @Test(arguments: editRates) @MainActor
  func aHeldFetchIsDroppedAndTheNewNextSongFadesIn(rate: Double) async throws {
    let root = try editRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let (session, output, sources, items) = try start(
      ["a", "b", "c"], rate: rate, held: ["b"], crossfade: true, root: root)
    try await waitUntil { session.snapshot.state == .playing }
    try await eventually { await sources.entered.contains("b") }
    let stops = output.stops
    session.updateUpcoming([items[0], items[2]])
    try await playToEnd(session, output)
    let length = Int(rate)
    let fade = Int(0.25 * rate)
    #expect(output.stops == stops)
    #expect(output.captured.count == 2 * length - fade)
    #expect(
      firstBreak(output.captured.prefix(length - fade), [("a", 0..<length - fade)], length: length)
        == nil)
    #expect(
      firstBreak(output.captured.suffix(length - fade), [("c", fade..<length)], length: length)
        == nil)
    #expect(session.snapshot.occurrenceID == items[2].id)
    #expect(await sources.acquired == ["a", "b", "c"])
    #expect(await sources.busy == 0)
    #expect(await sources.peak <= 2)
    await session.stopAndWait()
    #expect(await sources.active.isEmpty)
  }

  /// The playing song has all been heard and the queue waits for the next
  /// song's source: an edit then is not that song failing.
  @Test(arguments: editRates) @MainActor
  func anEditWhileWaitingForTheNextSongIsNotAFailure(rate: Double) async throws {
    let root = try editRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let short = Int(rate / 4)
    let (session, output, sources, items) = try start(
      ["a", "b", "c"], rate: rate, frames: ["a": short], held: ["b"], root: root)
    var states: [QueuePlaybackState] = []
    session.onUpdate = { states.append($0.state) }
    try await waitUntil { session.snapshot.state == .playing }
    let deadline = ContinuousClock.now + .seconds(10)
    while session.snapshot.state != .buffering {
      guard ContinuousClock.now < deadline else { throw AudioPipelineFailure.stalled }
      output.consume()
      try await Task.sleep(for: .milliseconds(5))
    }
    let stops = output.stops
    session.updateUpcoming([items[0], items[2]])
    try await playToEnd(session, output)
    let length = Int(rate)
    #expect(!states.contains(.failed))
    #expect(output.stops == stops)
    #expect(firstBreak(output.captured.prefix(short), [("a", 0..<short)], length: short) == nil)
    #expect(
      firstBreak(output.captured.dropFirst(short), [("c", 0..<length)], length: length) == nil)
    #expect(session.snapshot.occurrenceID == items[2].id)
    #expect(await sources.busy == 0)
    await session.stopAndWait()
    #expect(await sources.active.isEmpty)
  }

  /// Five edits in one go: the last one is what plays; nothing stops, no
  /// more than two sources at once, none left over.
  @Test(arguments: editRates) @MainActor
  func fiveEditsInARowPlayTheLastOrder(rate: Double) async throws {
    let root = try editRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let (session, output, sources, items) = try start(
      ["a", "b", "c", "d", "e"], rate: rate, root: root)
    let byID = Dictionary(uniqueKeysWithValues: items.map { ($0.track.id, $0) })
    try await waitUntil { session.snapshot.state == .playing }
    output.consume()
    let stops = output.stops
    for order in ["acbde", "adbce", "aebcd", "abe", "aed"] {
      session.updateUpcoming(order.map { byID[String($0)]! })
    }
    try await playToEnd(session, output)
    let length = Int(rate)
    #expect(output.stops == stops)
    #expect(
      firstBreak(
        output.captured, [("a", 0..<length), ("e", 0..<length), ("d", 0..<length)],
        length: length) == nil)
    #expect(await sources.busy == 0)
    #expect(await sources.peak <= 2)
    await session.stopAndWait()
    #expect(await sources.active.isEmpty)
  }

  /// An edit while a seek prepares the song is taken by the stream once it
  /// is built: the seek's own stop is the only one.
  @Test(arguments: editRates) @MainActor
  func anEditDuringASeekUsesTheNewList(rate: Double) async throws {
    let root = try editRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let (session, output, sources, items) = try start(["a", "b", "c"], rate: rate, root: root)
    try await waitUntil { session.snapshot.state == .playing }
    output.consume()
    let stops = output.stops
    let mark = output.captured.count
    session.seek(0.5)
    #expect(session.snapshot.state == .preparing)
    session.updateUpcoming([items[0], items[2]])
    try await playToEnd(session, output)
    let length = Int(rate)
    #expect(output.stops == stops + 1)
    #expect(
      firstBreak(
        output.captured.dropFirst(mark), [("a", length / 2..<length), ("c", 0..<length)],
        length: length) == nil)
    #expect(await sources.busy == 0)
    await session.stopAndWait()
    #expect(await sources.active.isEmpty)
  }

  /// Repeat One mid-song: the song plays again from its start, no stop.
  @Test(arguments: editRates) @MainActor
  func repeatOneMidSongRepeatsWithoutAStop(rate: Double) async throws {
    let root = try editRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let (session, output, sources, items) = try start(["a", "b"], rate: rate, root: root)
    try await waitUntil { session.snapshot.state == .playing }
    output.consume()
    let stops = output.stops
    session.setRepeatMode(.one)
    let length = Int(rate)
    let deadline = ContinuousClock.now + .seconds(20)
    while output.captured.count < 2 * length + 4096 {
      guard ContinuousClock.now < deadline, session.snapshot.state != .failed else {
        throw AudioPipelineFailure.stalled
      }
      output.consume()
      try await Task.sleep(for: .milliseconds(1))
    }
    #expect(output.stops == stops)
    #expect(
      firstBreak(
        output.captured.prefix(2 * length), [("a", 0..<length), ("a", 0..<length)],
        length: length) == nil)
    // Now Playing moves to the new listening of the same song.
    try await waitUntil { session.snapshot.occurrenceID != items[0].id }
    #expect(session.snapshot.index == 0)
    #expect(await sources.busy == 0)
    #expect(await sources.peak <= 2)
    await session.stopAndWait()
    #expect(await sources.active.isEmpty)
  }

  /// Once the next song is fading in, removing it is too late: the queue
  /// restarts once, from the position heard at that moment.
  @Test(arguments: editRates) @MainActor
  func anEditAfterTheTransitionIsReadyRestartsOnceWhereItWas(rate: Double) async throws {
    let root = try editRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let half = Int(rate / 2)
    let fade = Int(0.25 * rate)
    let (session, output, sources, items) = try start(
      ["a", "b", "c"], rate: rate, frames: ["a": half], crossfade: true, root: root)
    try await waitUntil { session.snapshot.state == .playing }
    // Mixed audio has been scheduled: the cross-fade into "b" has begun.
    while output.captured.count <= half - fade {
      output.consume()
      try await Task.sleep(for: .milliseconds(2))
    }
    let stops = output.stops
    var restarts: [Double] = []
    session.onUpdate = { if $0.state == .preparing { restarts.append($0.elapsed) } }
    // Heard since the last clock reading: only a fresh position has it.
    output.consume()
    let heard = output.positionFrames
    let mark = output.captured.count
    session.updateUpcoming([items[0], items[2]])
    try await waitUntil { !restarts.isEmpty }
    try await playToEnd(session, output)
    #expect(output.stops == stops + 1)
    #expect(restarts == [Double(heard) / rate])
    // From where it was: "a" at the frame heard, and "c" to its end.
    let after = output.captured.dropFirst(mark)
    let first = after.first.map { decodeRamp($0, frames: half) }
    let last = after.last.map { decodeRamp($0, frames: Int(rate)) }
    #expect(first?.code == 1 && first?.frame == Int(heard))
    #expect(last?.code == 3 && last?.frame == Int(rate) - 1)
    #expect(session.snapshot.occurrenceID == items[2].id)
    await session.stopAndWait()
    #expect(await sources.active.isEmpty)
  }

  /// The queue has already moved on to "b" (heard, not yet shown) when "b"
  /// is removed: too late for the stream, and the restart must not give
  /// "b"'s elapsed time to "c", which takes its place in the queue. "c"
  /// starts from its beginning (Codex round 1 on batch L, high).
  @Test(arguments: editRates) @MainActor
  func removingASongAlreadyHeardStartsItsSuccessorFromTheStart(rate: Double) async throws {
    let root = try editRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let short = Int(rate / 4)
    let (session, output, sources, items) = try start(
      ["a", "b", "c"], rate: rate, frames: ["a": short], root: root)
    try await waitUntil { session.snapshot.state == .playing }
    // "b" is scheduled behind the rest of "a".
    while output.captured.count < short + 4096 {
      output.consume()
      try await Task.sleep(for: .milliseconds(2))
    }
    // Into "b" with no await, so no clock reading has seen it.
    while output.positionFrames <= Int64(short) + 1024, !output.pending.isEmpty {
      output.consume()
    }
    #expect(output.positionFrames > Int64(short))
    #expect(session.snapshot.occurrenceID == items[0].id)
    let stops = output.stops
    var restarts: [QueuePlaybackSnapshot] = []
    session.onUpdate = { if $0.state == .preparing { restarts.append($0) } }
    let mark = output.captured.count
    session.updateUpcoming([items[0], items[2]])
    try await waitUntil { !restarts.isEmpty }
    try await playToEnd(session, output)
    let length = Int(rate)
    #expect(output.stops == stops + 1)
    #expect(restarts.map(\.index) == [1] && restarts.map(\.elapsed) == [0])
    #expect(restarts.first?.occurrenceID == items[2].id)
    #expect(
      firstBreak(output.captured.dropFirst(mark), [("c", 0..<length)], length: length) == nil)
    await session.stopAndWait()
    #expect(await sources.active.isEmpty)
  }

  /// Similar songs arriving as the queue ends: the last song is all decoded
  /// and the stream has said so, but seconds of it are still to be heard.
  /// The new songs follow it with nothing stopped or replayed (Codex round 1
  /// on batch L).
  @Test(arguments: editRates) @MainActor
  func songsAddedWhileTheLastSongDrainsFollowIt(rate: Double) async throws {
    let root = try editRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let (session, output, sources, items) = try start(["a"], rate: rate, root: root)
    try await waitUntil { session.snapshot.state == .playing }
    let length = Int(rate)
    while output.captured.count < length {
      output.consume()
      try await Task.sleep(for: .milliseconds(2))
    }
    // Room in the lookahead: the pump asks again and hears the end.
    output.consume()
    try await Task.sleep(for: .milliseconds(20))
    #expect(output.captured.count == length && !output.pending.isEmpty)
    #expect(session.snapshot.state == .playing)
    let stops = output.stops
    let added = [editTrack("s"), editTrack("t")]
    session.updateUpcoming(items + added)
    try await playToEnd(session, output)
    #expect(output.stops == stops)
    #expect(
      firstBreak(
        output.captured, [("a", 0..<length), ("s", 0..<length), ("t", 0..<length)],
        length: length) == nil)
    #expect(session.snapshot.index == 2 && session.snapshot.occurrenceID == added[1].id)
    #expect(await sources.busy == 0)
    await session.stopAndWait()
    #expect(await sources.active.isEmpty)
  }

  enum Repeated: String, CaseIterable, Sendable { case one, allWrapped }
  /// After a song repeats, the iPhone's queue holds the new listening's id
  /// (PlayerController.accept renews it from each snapshot). An edit made
  /// from that queue still reaches the engine, with no stop (Codex round 1
  /// on batch L: it was dropped, so a removed song still played).
  @Test(arguments: Repeated.allCases) @MainActor
  func editsAfterASongRepeatsStillReachTheEngine(_ repeated: Repeated) async throws {
    let root = try editRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let rate = 48000.0
    let (session, output, sources, items) = try start(["a", "b", "c"], rate: rate, root: root)
    // The phone's queue, kept as PlayerController.accept keeps it.
    var queue = PlaybackQueue()
    queue.replace(items.map(\.track))
    for (index, item) in items.enumerated() { queue.renewOccurrence(at: index, id: item.id) }
    session.onUpdate = { snapshot in
      queue.select(index: snapshot.index)
      if let id = snapshot.occurrenceID { queue.renewOccurrence(at: snapshot.index, id: id) }
    }
    try await waitUntil { session.snapshot.state == .playing }
    session.setRepeatMode(repeated == .one ? .one : .all)
    // Play until "a" is heard again, as a new listening.
    let deadline = ContinuousClock.now + .seconds(20)
    while !(session.snapshot.index == 0 && session.snapshot.occurrenceID != items[0].id
      && session.snapshot.elapsed > 0.05)
    {
      guard ContinuousClock.now < deadline else { throw AudioPipelineFailure.stalled }
      output.consume()
      try await Task.sleep(for: .milliseconds(1))
    }
    #expect(queue.items[0].id == session.snapshot.occurrenceID)
    let stops = output.stops
    let mark = output.captured.count
    queue.removeUpcoming(items[1].id)
    session.updateUpcoming(queue.items)
    session.setRepeatMode(.off)
    try await playToEnd(session, output)
    #expect(output.stops == stops)
    // The rest of "a", then "c": "b" is never heard again.
    let heard = output.captured.dropFirst(mark).map { decodeRamp($0, frames: Int(rate)).code }
    var songs: [Int] = []
    for code in heard where songs.last != code { songs.append(code) }
    #expect(songs == [1, 3])
    #expect(await sources.busy == 0)
    await session.stopAndWait()
    #expect(await sources.active.isEmpty)
  }

  /// The same, with nothing after the removed song: the queue ends there.
  @Test @MainActor func removingTheLastSongAlreadyHeardEndsTheQueue() async throws {
    let root = try editRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let short = 12000
    let (session, output, sources, items) = try start(
      ["a", "b"], rate: 48000, frames: ["a": short], root: root)
    try await waitUntil { session.snapshot.state == .playing }
    while output.captured.count < short + 4096 {
      output.consume()
      try await Task.sleep(for: .milliseconds(2))
    }
    while output.positionFrames <= Int64(short) + 1024, !output.pending.isEmpty {
      output.consume()
    }
    session.updateUpcoming([items[0]])
    try await waitUntil { session.snapshot.state == .ended }
    #expect(session.snapshot.index == 0 && session.snapshot.occurrenceID == items[0].id)
    #expect(!output.playing)
    await session.stopAndWait()
    #expect(await sources.active.isEmpty)
  }

  /// A song added at the end while the next one fades in leaves the fade alone.
  @Test(arguments: editRates) @MainActor
  func anEditBeyondAFadingInSongLeavesItPlaying(rate: Double) async throws {
    let root = try editRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let half = Int(rate / 2)
    let fade = Int(0.25 * rate)
    let (session, output, sources, items) = try start(
      ["a", "b", "c"], rate: rate, frames: ["a": half], crossfade: true, root: root)
    try await waitUntil { session.snapshot.state == .playing }
    while output.captured.count <= half - fade {
      output.consume()
      try await Task.sleep(for: .milliseconds(2))
    }
    let stops = output.stops
    let added = editTrack("d")
    session.updateUpcoming(items + [added])
    try await playToEnd(session, output)
    #expect(output.stops == stops)
    #expect(session.snapshot.index == 3 && session.snapshot.occurrenceID == added.id)
    #expect(output.captured.count == half + 3 * Int(rate) - 3 * fade)
    #expect(await sources.busy == 0)
    #expect(await sources.peak <= 2)
    await session.stopAndWait()
    #expect(await sources.active.isEmpty)
  }

  enum Edit: String, CaseIterable, Sendable {
    case moveLaterSongUp, playNext, furtherDown, similarSongsAfterTheLast
  }
  /// Everyday edits mid-song: the new order plays, gaplessly, with no stop.
  @Test(arguments: Edit.allCases, editRates) @MainActor
  func everydayEditsPlayTheNewOrderWithoutAStop(_ edit: Edit, rate: Double) async throws {
    let root = try editRoot()
    defer { try? FileManager.default.removeItem(at: root) }
    let (before, after): (String, String) =
      switch edit {
      case .moveLaterSongUp: ("abcd", "adbc")
      case .playNext: ("abc", "axbc")
      case .furtherDown: ("abc", "abdc")
      case .similarSongsAfterTheLast: ("a", "ast")
      }
    let (session, output, sources, items) = try start(
      before.map(String.init), rate: rate, root: root)
    var byID = Dictionary(uniqueKeysWithValues: items.map { ($0.track.id, $0) })
    for id in after.map(String.init) where byID[id] == nil { byID[id] = editTrack(id) }
    let edited = after.map { byID[String($0)]! }
    try await waitUntil { session.snapshot.state == .playing }
    output.consume()
    let stops = output.stops
    session.updateUpcoming(edited)
    try await playToEnd(session, output)
    let length = Int(rate)
    #expect(output.stops == stops)
    #expect(
      firstBreak(output.captured, after.map { (String($0), 0..<length) }, length: length) == nil)
    #expect(
      session.snapshot.index == edited.count - 1
        && session.snapshot.occurrenceID == edited.last!.id)
    if edit == .furtherDown {
      // The next song's fetch was left alone.
      #expect(await sources.acquired.filter { $0 == "b" }.count == 1)
    }
    #expect(await sources.busy == 0)
    #expect(await sources.peak <= 2)
    await session.stopAndWait()
    #expect(await sources.active.isEmpty)
  }
}
