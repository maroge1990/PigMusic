import AVFoundation
import Foundation
import PigPlayback

/// Offline decoding/rendering probe. It is not the streaming playback engine.
public enum PCMTransitionProbe {
  public enum Failure: Error { case incompatibleFormats, tooLarge, unsupportedPCM }
  public struct Result: Sendable {
    public let frames: Int
    public let overlapFrames: Int
    public let sampleRate: Double
    public let channels: Int
  }
  public static func render(first: URL, second: URL, output: URL, crossfadeSeconds: Double) throws
    -> Result
  {
    let left = try AVAudioFile(forReading: first)
    let right = try AVAudioFile(forReading: second)
    guard left.processingFormat.sampleRate == right.processingFormat.sampleRate,
      left.processingFormat.channelCount == right.processingFormat.channelCount
    else { throw Failure.incompatibleFormats }
    // Bound an investigation tool's allocation, independent of the future live engine.
    guard left.length > 0, right.length > 0, left.length <= 32_000_000, right.length <= 32_000_000,
      let a = AVAudioPCMBuffer(
        pcmFormat: left.processingFormat, frameCapacity: AVAudioFrameCount(left.length)),
      let b = AVAudioPCMBuffer(
        pcmFormat: right.processingFormat, frameCapacity: AVAudioFrameCount(right.length))
    else { throw Failure.tooLarge }
    try left.read(into: a)
    try right.read(into: b)
    guard let firstChannels = a.floatChannelData, let secondChannels = b.floatChannelData else {
      throw Failure.unsupportedPCM
    }
    let fade = min(12, max(0, crossfadeSeconds.isFinite ? crossfadeSeconds : 0))
    let overlap = min(
      Int(fade * left.processingFormat.sampleRate), min(Int(a.frameLength), Int(b.frameLength)))
    let frames = Int(a.frameLength) + Int(b.frameLength) - overlap
    guard
      let format = AVAudioFormat(
        standardFormatWithSampleRate: left.processingFormat.sampleRate,
        channels: left.processingFormat.channelCount),
      let rendered = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(frames)),
      let destination = rendered.floatChannelData
    else { throw Failure.unsupportedPCM }
    rendered.frameLength = AVAudioFrameCount(frames)
    for channel in 0..<Int(format.channelCount) {
      let firstSamples = Array(
        UnsafeBufferPointer(start: firstChannels[channel], count: Int(a.frameLength)))
      let secondSamples = Array(
        UnsafeBufferPointer(start: secondChannels[channel], count: Int(b.frameLength)))
      let samples = TransitionPlanner.render(firstSamples, secondSamples, overlapFrames: overlap)
      destination[channel].update(from: samples, count: samples.count)
    }
    let file = try AVAudioFile(forWriting: output, settings: format.settings)
    try file.write(from: rendered)
    return Result(
      frames: frames, overlapFrames: overlap, sampleRate: format.sampleRate,
      channels: Int(format.channelCount))
  }
}
