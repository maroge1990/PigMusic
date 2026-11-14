import AVFoundation
import Foundation
import PigDomain
import PigPlayback
import PigProbes
import PigStore
import Testing

@Test func decodedAudioProbePreservesSampleBoundaryAndCrossfadeEnvelope() throws {
  let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
  try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
  defer { try? FileManager.default.removeItem(at: directory) }
  let format = AVAudioFormat(standardFormatWithSampleRate: 8000, channels: 1)!
  let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 8000)!
  buffer.frameLength = 8000
  for index in 0..<8000 { buffer.floatChannelData![0][index] = 0.5 }
  let first = directory.appendingPathComponent("a.wav")
  let second = directory.appendingPathComponent("b.wav")
  try AVAudioFile(forWriting: first, settings: format.settings).write(from: buffer)
  try AVAudioFile(forWriting: second, settings: format.settings).write(from: buffer)
  let joined = directory.appendingPathComponent("joined.wav")
  let gapless = try PCMTransitionProbe.render(
    first: first, second: second, output: joined, crossfadeSeconds: 0)
  #expect(gapless.frames == 16000)
  #expect(gapless.overlapFrames == 0)
  let result = try PCMTransitionProbe.render(
    first: first, second: second, output: directory.appendingPathComponent("fade.wav"),
    crossfadeSeconds: 0.5)
  #expect(result.frames == 12000)
  #expect(result.overlapFrames == 4000)
  let file = try AVAudioFile(forReading: joined)
  let decoded = AVAudioPCMBuffer(
    pcmFormat: file.processingFormat, frameCapacity: AVAudioFrameCount(file.length))!
  try file.read(into: decoded)
  #expect(decoded.floatChannelData![0][7999] == 0.5)
  #expect(decoded.floatChannelData![0][8000] == 0.5)
  let fadeFile = try AVAudioFile(forReading: directory.appendingPathComponent("fade.wav"))
  let fadeBuffer = AVAudioPCMBuffer(
    pcmFormat: fadeFile.processingFormat, frameCapacity: AVAudioFrameCount(fadeFile.length))!
  try fadeFile.read(into: fadeBuffer)
  for frame in 4000..<8000 { #expect(abs(fadeBuffer.floatChannelData![0][frame] - 0.5) < 0.000001) }
}

@Test func fiftyThousandTrackCacheIsPagedAndSearchable() async throws {
  let store = try await makeLargeStore()
  let first = try await store.tracks(limit: 100)
  let next = try await store.tracks(offset: 100, limit: 100)
  #expect(first.count == 100)
  #expect(next.count == 100)
  #expect(Set(first.map(\.id)).isDisjoint(with: next.map(\.id)))
  #expect(try await store.count(.tracks) == 50000)
  #expect(try await store.tracks(query: "Song 49999").map(\.id) == ["49999"])
}

private func makeLargeStore() async throws -> LibraryStore {
  let store = try LibraryStore(path: ":memory:")
  let tracks = (0..<50000).map {
    Track(id: String($0), title: "Song \($0)", artist: "Fixture Artist")
  }
  try await store.replace(.init(revision: "large", tracks: tracks))
  return store
}

@Test func aacProbeHonoursDecodedDurationAndOverlap() throws {
  let swiftRoot = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
    .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
  let input = swiftRoot.appendingPathComponent("PigMusic/demo-tone.m4a")
  let output = FileManager.default.temporaryDirectory.appendingPathComponent(
    UUID().uuidString + ".wav")
  defer { try? FileManager.default.removeItem(at: output) }
  let joined = try PCMTransitionProbe.render(
    first: input, second: input, output: output, crossfadeSeconds: 0)
  #expect(joined.sampleRate == 44100)
  #expect(joined.frames == 264600)
  let faded = try PCMTransitionProbe.render(
    first: input, second: input, output: output, crossfadeSeconds: 1)
  #expect(faded.overlapFrames == 44100)
  #expect(faded.frames == 220500)
}
