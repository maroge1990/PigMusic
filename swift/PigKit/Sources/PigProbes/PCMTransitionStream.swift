import AVFoundation
import Foundation
import PigPlayback

/// Two-file transition prototype. Output blocks share one PCM clock, including mixed-rate sources.
/// Local file input only: this does not claim progressive HTTP decoding or production queue support.
public actor PCMTransitionStream {
  public struct Metrics: Sendable {
    public var outputFrames: Int64 = 0
    public var overlapFrames = 0
    public var peakRetainedFrames = 0
  }
  public let sampleRate: Double
  public let blockFrames: Int
  private let format: AVAudioFormat
  private let first: PCMFileDecoder
  private let second: PCMFileDecoder
  private let fadeFrames: Int
  private var tail: FrameRing
  private var head: FrameRing
  private var firstEnded = false
  private var transitionReady = false
  private var prefixFrames = 0
  private var mixedFrames = 0
  private var metrics = Metrics()

  public init(
    first: URL, second: URL, crossfadeSeconds: Double, sampleRate: Double = 48000,
    blockFrames: Int = 2048
  ) throws {
    guard crossfadeSeconds.isFinite, (0...12).contains(crossfadeSeconds),
      [44100.0, 48000.0, 96000.0].contains(sampleRate), (256...8192).contains(blockFrames),
      let format = AVAudioFormat(standardFormatWithSampleRate: sampleRate, channels: 2)
    else { throw AudioPipelineFailure.invalidConfiguration }
    self.sampleRate = sampleRate
    self.blockFrames = blockFrames
    self.format = format
    self.first = try PCMFileDecoder(url: first, format: format)
    self.second = try PCMFileDecoder(url: second, format: format)
    fadeFrames = min(
      Int(crossfadeSeconds * sampleRate),
      Int(min(self.first.expectedFrames, self.second.expectedFrames)))
    tail = FrameRing(capacity: fadeFrames + blockFrames)
    head = FrameRing(capacity: max(1, fadeFrames))
  }

  public func measurements() -> Metrics { metrics }

  public func next() throws -> PCMBlock? {
    try Task.checkCancellation()
    while !firstEnded {
      if tail.count > fadeFrames {
        return try emit(tail.pop(min(blockFrames, tail.count - fadeFrames)))
      }
      if let decoded = try first.read(maxFrames: blockFrames) {
        tail.push(decoded)
        recordRetained()
      } else {
        firstEnded = true
      }
    }
    if !transitionReady {
      while head.count < min(fadeFrames, tail.count) {
        try Task.checkCancellation()
        guard
          let decoded = try second.read(
            maxFrames: min(blockFrames, min(fadeFrames, tail.count) - head.count))
        else { break }
        head.push(decoded)
        recordRetained()
      }
      metrics.overlapFrames = min(tail.count, head.count)
      prefixFrames = tail.count - metrics.overlapFrames
      transitionReady = true
    }
    if prefixFrames > 0 {
      let count = min(blockFrames, prefixFrames)
      prefixFrames -= count
      return try emit(tail.pop(count))
    }
    if mixedFrames < metrics.overlapFrames {
      let count = min(blockFrames, metrics.overlapFrames - mixedFrames)
      var outgoing = tail.pop(count)
      let incoming = head.pop(count)
      for frame in 0..<count {
        let progress =
          metrics.overlapFrames == 1
          ? 0.5 : Double(mixedFrames + frame) / Double(metrics.overlapFrames - 1)
        let gain = TransitionPlanner.gains(progress: progress)
        for channel in 0..<2 {
          outgoing[channel][frame] =
            outgoing[channel][frame] * gain.outgoing + incoming[channel][frame] * gain.incoming
        }
      }
      mixedFrames += count
      return try emit(outgoing)
    }
    if let decoded = try second.read(maxFrames: blockFrames) {
      metrics.outputFrames += Int64(decoded.frameLength)
      return PCMBlock(
        sampleRate: sampleRate,
        channels: (0..<2).map {
          Array(
            UnsafeBufferPointer(
              start: decoded.floatChannelData![$0], count: Int(decoded.frameLength)))
        })
    }
    return nil
  }
  private func recordRetained() {
    metrics.peakRetainedFrames = max(metrics.peakRetainedFrames, tail.count + head.count)
  }
  private func emit(_ channels: [[Float]]) throws -> PCMBlock {
    let frames = channels[0].count
    metrics.outputFrames += Int64(frames)
    return PCMBlock(sampleRate: sampleRate, channels: channels)
  }
}
