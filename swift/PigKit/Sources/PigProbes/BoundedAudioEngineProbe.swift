import AVFoundation
import Foundation
import PigPlayback

public enum BoundedAudioEngineProbe {
  public struct Result: Sendable {
    public let frames: Int64
    public let overlapFrames: Int
    public let peakRetainedFrames: Int
    public let peakScheduledFrames: Int64
  }
  /// Captures actual AVAudioEngine/AVAudioPlayerNode output in offline manual-rendering mode.
  /// At most four full blocks' worth of PCM is scheduled ahead; tails are bounded by fade duration.
  public static func render(
    first: URL, second: URL, output: URL, crossfadeSeconds: Double,
    blockFrames: Int = 2048, sampleRate: Double = 48000
  ) async throws -> Result {
    let destinationURL = output.standardizedFileURL.resolvingSymlinksInPath()
    guard destinationURL != first.standardizedFileURL.resolvingSymlinksInPath(),
      destinationURL != second.standardizedFileURL.resolvingSymlinksInPath()
    else { throw AudioPipelineFailure.invalidConfiguration }
    let stream = try PCMTransitionStream(
      first: first, second: second,
      crossfadeSeconds: crossfadeSeconds, sampleRate: sampleRate, blockFrames: blockFrames)
    let format = AVAudioFormat(standardFormatWithSampleRate: sampleRate, channels: 2)!
    let engine = AVAudioEngine()
    let node = AVAudioPlayerNode()
    engine.attach(node)
    engine.connect(node, to: engine.mainMixerNode, format: format)
    try engine.enableManualRenderingMode(
      .offline, format: format, maximumFrameCount: AVAudioFrameCount(blockFrames))
    defer {
      node.stop()
      engine.stop()
      engine.disableManualRenderingMode()
    }
    let destination = try AVAudioFile(forWriting: output, settings: format.settings)
    let rendered = AVAudioPCMBuffer(
      pcmFormat: format, frameCapacity: AVAudioFrameCount(blockFrames))!
    var scheduled: Int64 = 0
    var delivered: Int64 = 0
    var peak: Int64 = 0
    var exhausted = false
    var started = false
    var stalls = 0
    while !exhausted || delivered < scheduled {
      try Task.checkCancellation()
      while !exhausted && scheduled - delivered <= Int64(blockFrames * 3) {
        guard let block = try await stream.next() else {
          exhausted = true
          break
        }
        node.scheduleBuffer(try block.audioBuffer(), at: nil, options: [], completionHandler: nil)
        scheduled += Int64(block.frameCount)
        peak = max(peak, scheduled - delivered)
      }
      guard delivered < scheduled else { break }
      if !started {
        try engine.start()
        node.play(at: AVAudioTime(sampleTime: 0, atRate: format.sampleRate))
        started = true
      }
      let frames = AVAudioFrameCount(min(Int64(blockFrames), scheduled - delivered))
      switch try engine.renderOffline(frames, to: rendered) {
      case .success:
        guard rendered.frameLength > 0 else { throw AudioPipelineFailure.stalled }
        try destination.write(from: rendered)
        delivered += Int64(rendered.frameLength)
        stalls = 0
      case .cannotDoInCurrentContext:
        stalls += 1
        guard stalls < 16 else { throw AudioPipelineFailure.stalled }
      default: throw AudioPipelineFailure.rendering
      }
    }
    let metrics = await stream.measurements()
    return Result(
      frames: delivered, overlapFrames: metrics.overlapFrames,
      peakRetainedFrames: metrics.peakRetainedFrames, peakScheduledFrames: peak)
  }
}
