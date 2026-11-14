import AVFoundation
import Foundation

/// Silent offline capture of the same multi-occurrence decoder used by the realtime candidate.
public enum QueuedAudioEngineProbe {
  public struct Result: Sendable {
    public let frames: Int64
    public let peakScheduledFrames: Int64
    public let markers: [QueueAudioMarker]
  }
  public static func render(
    stream: QueuePCMStream, output: URL, sampleRate: Double = 48000,
    blockFrames: Int = 2048
  ) async throws -> Result {
    guard output.isFileURL, !FileManager.default.fileExists(atPath: output.path),
      [44100.0, 48000.0, 96000.0].contains(sampleRate),
      (256...8192).contains(blockFrames),
      let format = AVAudioFormat(standardFormatWithSampleRate: sampleRate, channels: 2),
      let buffer = AVAudioPCMBuffer(
        pcmFormat: format, frameCapacity: AVAudioFrameCount(blockFrames))
    else { throw AudioPipelineFailure.invalidConfiguration }
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
    let file = try AVAudioFile(forWriting: output, settings: format.settings)
    var scheduled: Int64 = 0
    var delivered: Int64 = 0
    var peak: Int64 = 0
    var exhausted = false
    var started = false
    var stalls = 0
    var markers: [QueueAudioMarker] = []
    while !exhausted || delivered < scheduled {
      try Task.checkCancellation()
      while !exhausted && scheduled - delivered <= Int64(blockFrames * 3) {
        guard let block = try await stream.next() else {
          exhausted = true
          break
        }
        guard block.pcm.sampleRate == sampleRate, block.pcm.frameCount <= blockFrames else {
          throw AudioPipelineFailure.invalidConfiguration
        }
        node.scheduleBuffer(
          try block.pcm.audioBuffer(), at: nil, options: [], completionHandler: nil)
        scheduled += Int64(block.pcm.frameCount)
        peak = max(peak, scheduled - delivered)
        markers += block.markers
      }
      guard delivered < scheduled else { break }
      if !started {
        try engine.start()
        node.play(at: AVAudioTime(sampleTime: 0, atRate: sampleRate))
        started = true
      }
      let frames = AVAudioFrameCount(min(Int64(blockFrames), scheduled - delivered))
      switch try engine.renderOffline(frames, to: buffer) {
      case .success:
        guard buffer.frameLength > 0 else { throw AudioPipelineFailure.stalled }
        try file.write(from: buffer)
        delivered += Int64(buffer.frameLength)
        stalls = 0
      case .cannotDoInCurrentContext:
        stalls += 1
        guard stalls < 16 else { throw AudioPipelineFailure.stalled }
      default: throw AudioPipelineFailure.rendering
      }
    }
    return Result(frames: delivered, peakScheduledFrames: peak, markers: markers)
  }
}
