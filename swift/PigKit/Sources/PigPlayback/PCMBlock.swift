import AVFoundation
import Foundation

/// Value-owned PCM crosses the decoder boundary; mutable AVAudioPCMBuffer never crosses actors.
public struct PCMBlock: Sendable {
  public let sampleRate: Double
  public let channels: [[Float]]
  public var frameCount: Int { channels[0].count }
  package init(sampleRate: Double, channels: [[Float]]) {
    self.sampleRate = sampleRate
    self.channels = channels
  }
  public func audioBuffer() throws -> AVAudioPCMBuffer {
    guard let format = AVAudioFormat(standardFormatWithSampleRate: sampleRate, channels: 2),
      let buffer = AVAudioPCMBuffer(
        pcmFormat: format, frameCapacity: AVAudioFrameCount(frameCount)),
      let output = buffer.floatChannelData
    else { throw AudioPipelineFailure.conversion }
    buffer.frameLength = AVAudioFrameCount(frameCount)
    for channel in 0..<2 { output[channel].update(from: channels[channel], count: frameCount) }
    return buffer
  }
}

/// Fixed-capacity circular frame storage: removing a block never copies an entire held tail.
package struct FrameRing {
  private var channels: [[Float]]
  private var start = 0
  package private(set) var count = 0
  private let capacity: Int
  package init(capacity: Int) {
    self.capacity = capacity
    channels = Array(repeating: Array(repeating: 0, count: capacity), count: 2)
  }
  package mutating func push(_ buffer: AVAudioPCMBuffer) {
    let frames = Int(buffer.frameLength)
    precondition(count + frames <= capacity)
    for channel in 0..<2 {
      for frame in 0..<frames {
        channels[channel][(start + count + frame) % capacity] =
          buffer.floatChannelData![channel][frame]
      }
    }
    count += frames
  }
  package mutating func pop(_ frames: Int) -> [[Float]] {
    precondition(frames <= count)
    var result = Array(repeating: [Float](), count: 2)
    for channel in 0..<2 {
      result[channel].reserveCapacity(frames)
      for frame in 0..<frames {
        result[channel].append(channels[channel][(start + frame) % capacity])
      }
    }
    start = (start + frames) % capacity
    count -= frames
    return result
  }
}
