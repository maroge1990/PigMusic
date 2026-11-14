import AVFoundation
import Foundation
import PigDomain
import PigPlayback
import PigProbes
import Testing

private func audioDirectory() throws -> URL {
  let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
  try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
  return directory
}
private func constantAudio(
  _ file: URL, rate: Double = 48000, seconds: Double = 1,
  values: [Float] = [0.5]
) throws {
  let format = AVAudioFormat(
    standardFormatWithSampleRate: rate, channels: AVAudioChannelCount(values.count))!
  let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 4096)!
  let output = try AVAudioFile(forWriting: file, settings: format.settings)
  var remaining = Int(rate * seconds)
  while remaining > 0 {
    let frames = min(4096, remaining)
    buffer.frameLength = AVAudioFrameCount(frames)
    for channel in values.indices {
      buffer.floatChannelData![channel].update(repeating: values[channel], count: frames)
    }
    try output.write(from: buffer)
    remaining -= frames
  }
}
private func decodedAudio(_ url: URL) throws -> AVAudioPCMBuffer {
  let file = try AVAudioFile(forReading: url)
  let buffer = AVAudioPCMBuffer(
    pcmFormat: file.processingFormat, frameCapacity: AVAudioFrameCount(file.length))!
  try file.read(into: buffer)
  return buffer
}

@Test func boundedEnginePreservesGaplessBoundaryAndCrossfadeAcrossBlocks() async throws {
  let root = try audioDirectory()
  defer { try? FileManager.default.removeItem(at: root) }
  let first = root.appendingPathComponent("first.wav")
  let second = root.appendingPathComponent("second.wav")
  try constantAudio(first, values: [0.5, 0.5])
  try constantAudio(second, values: [0.25, -0.25])
  let gaplessURL = root.appendingPathComponent("gapless.wav")
  let joined = try await BoundedAudioEngineProbe.render(
    first: first, second: second, output: gaplessURL, crossfadeSeconds: 0)
  #expect(joined.frames == 96000)
  #expect(joined.peakScheduledFrames <= 8192)
  let audio = try decodedAudio(gaplessURL)
  #expect(abs(audio.floatChannelData![0][47999] - 0.5) < 0.00001)
  #expect(abs(audio.floatChannelData![0][48000] - 0.25) < 0.00001)
  #expect(abs(audio.floatChannelData![1][48000] + 0.25) < 0.00001)
  let fadeURL = root.appendingPathComponent("fade.wav")
  let faded = try await BoundedAudioEngineProbe.render(
    first: first, second: second, output: fadeURL, crossfadeSeconds: 0.25)
  #expect(faded.frames == 84000)
  #expect(faded.overlapFrames == 12000)
  #expect(faded.peakScheduledFrames <= 8192)
  #expect(faded.peakRetainedFrames <= 24000 + 2048)
  let fade = try decodedAudio(fadeURL)
  var maxError: Float = 0
  for frame in 0..<12000 {
    let fraction = Float(frame) / 11999
    maxError = max(
      maxError,
      abs(fade.floatChannelData![0][36000 + frame] - (0.5 * (1 - fraction) + 0.25 * fraction)))
  }
  #expect(maxError < 0.00001)
}

@Test func boundedDecoderNormalizesMixedRatesAndMonoStereoWithoutLengthDrift() async throws {
  let root = try audioDirectory()
  defer { try? FileManager.default.removeItem(at: root) }
  let first = root.appendingPathComponent("mono44100.wav")
  let second = root.appendingPathComponent("stereo96000.wav")
  try constantAudio(first, rate: 44100, values: [0.4])
  try constantAudio(second, rate: 96000, values: [0.2, -0.2])
  let stream = try PCMTransitionStream(first: first, second: second, crossfadeSeconds: 0)
  var frames = 0
  var firstCenter: [Float] = []
  var secondCenter: [Float] = []
  while let block = try await stream.next() {
    let length = block.frameCount
    #expect(length <= 2048)
    #expect(block.sampleRate == 48000 && block.channels.count == 2)
    if frames <= 24000 && frames + length > 24000 {
      firstCenter = (0..<2).map { block.channels[$0][24000 - frames] }
    }
    if frames <= 72000 && frames + length > 72000 {
      secondCenter = (0..<2).map { block.channels[$0][72000 - frames] }
    }
    frames += length
  }
  #expect(frames == 96000)
  #expect(firstCenter.count == 2 && secondCenter.count == 2)
  #expect(firstCenter.allSatisfy { abs($0 - 0.4) < 0.0001 })
  #expect(abs(secondCenter[0] - 0.2) < 0.0001 && abs(secondCenter[1] + 0.2) < 0.0001)
}

@Test func boundedAudioRetainsFixedTailForLongTracksAndCancels() async throws {
  let root = try audioDirectory()
  defer { try? FileManager.default.removeItem(at: root) }
  let first = root.appendingPathComponent("long.wav")
  let second = root.appendingPathComponent("next.wav")
  try constantAudio(first, seconds: 60)
  try constantAudio(second, seconds: 4)
  let stream = try PCMTransitionStream(first: first, second: second, crossfadeSeconds: 3)
  while try await stream.next() != nil {}
  let metrics = await stream.measurements()
  #expect(metrics.outputFrames == 48000 * 61)
  #expect(metrics.overlapFrames == 144000)
  #expect(metrics.peakRetainedFrames <= 288000 + 2048)
  let cancelled = try PCMTransitionStream(first: first, second: second, crossfadeSeconds: 0)
  let task = Task {
    try? await Task.sleep(for: .milliseconds(10))
    return try await cancelled.next()
  }
  task.cancel()
  await #expect(throws: CancellationError.self) { try await task.value }
}

@Test func boundedAudioRejectsRemoteInputAndOutputAliasing() async throws {
  let root = try audioDirectory()
  defer { try? FileManager.default.removeItem(at: root) }
  let first = root.appendingPathComponent("first.wav")
  try constantAudio(first)
  let before = try Data(contentsOf: first)
  await #expect(throws: AudioPipelineFailure.self) {
    try await BoundedAudioEngineProbe.render(
      first: first, second: first, output: first, crossfadeSeconds: 0)
  }
  #expect(try Data(contentsOf: first) == before)
  #expect(throws: AudioPipelineFailure.self) {
    try PCMTransitionStream(
      first: URL(string: "https://fixture.invalid/audio")!, second: first, crossfadeSeconds: 0)
  }
}

@Test func boundedEngineCapturesContinuousUniqueSamplesAtAlbumRate() async throws {
  let root = try audioDirectory()
  defer { try? FileManager.default.removeItem(at: root) }
  let format = AVAudioFormat(standardFormatWithSampleRate: 44100, channels: 2)!
  let first = root.appendingPathComponent("first.wav")
  let second = root.appendingPathComponent("second.wav")
  for (index, url) in [first, second].enumerated() {
    let file = try AVAudioFile(forWriting: url, settings: format.settings)
    let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 44100)!
    buffer.frameLength = 44100
    for channel in 0..<2 {
      for frame in 0..<44100 {
        buffer.floatChannelData![channel][frame] = 0.1 + Float(index * 44100 + frame) * 0.000001
      }
    }
    try file.write(from: buffer)
  }
  let output = root.appendingPathComponent("joined.wav")
  let result = try await BoundedAudioEngineProbe.render(
    first: first, second: second, output: output,
    crossfadeSeconds: 0, sampleRate: 44100)
  #expect(result.frames == 88200)
  let audio = try decodedAudio(output)
  var maximumError: Float = 0
  for channel in 0..<2 {
    for frame in 0..<88200 {
      maximumError = max(
        maximumError, abs(audio.floatChannelData![channel][frame] - (0.1 + Float(frame) * 0.000001))
      )
    }
  }
  #expect(maximumError < 0.0000001)
}

@Test func albumTransitionsPreserveOrderButNotRepeatedOrShuffledOccurrences() {
  var first = Track(id: "one", title: "One", artist: "Artist")
  first.albumId = "release"
  first.trackNumber = 1
  var next = Track(id: "two", title: "Two", artist: "Artist")
  next.albumId = "release"
  next.trackNumber = 2
  #expect(TransitionPlanner.albumOrder(outgoing: first, incoming: next))
  #expect(!TransitionPlanner.albumOrder(outgoing: first, incoming: first))
  #expect(!TransitionPlanner.albumOrder(outgoing: next, incoming: first))
  next.albumId = "different-release"
  #expect(!TransitionPlanner.albumOrder(outgoing: first, incoming: next))
  next.albumId = "release"
  next.discNumber = 2
  next.trackNumber = 1
  #expect(TransitionPlanner.albumOrder(outgoing: first, incoming: next))
}
