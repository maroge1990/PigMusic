import AVFoundation
import Foundation

public enum AudioPipelineFailure: Error, Sendable {
  case invalidConfiguration, unsupportedChannels, emptyAudio, conversion, stalled, rendering
}

/// Owned by the transition actor. Reads compressed files incrementally; never loads a song into RAM.
package final class PCMFileDecoder {
  let file: AVAudioFile
  let converter: AVAudioConverter
  let input: AVAudioPCMBuffer
  let format: AVAudioFormat
  package let expectedFrames: Int64
  private var delivered: Int64 = 0
  private var ended = false

  package init(url: URL, format: AVAudioFormat, startSeconds: Double = 0) throws {
    guard url.isFileURL else { throw AudioPipelineFailure.invalidConfiguration }
    file = try AVAudioFile(forReading: url, commonFormat: .pcmFormatFloat32, interleaved: false)
    guard file.length > 0 else { throw AudioPipelineFailure.emptyAudio }
    guard (1...2).contains(file.processingFormat.channelCount) else {
      throw AudioPipelineFailure.unsupportedChannels
    }
    guard let converter = AVAudioConverter(from: file.processingFormat, to: format),
      let input = AVAudioPCMBuffer(pcmFormat: file.processingFormat, frameCapacity: 8192)
    else { throw AudioPipelineFailure.conversion }
    self.converter = converter
    self.input = input
    self.format = format
    let frames = (Double(file.length) * format.sampleRate / file.processingFormat.sampleRate)
      .rounded()
    guard frames.isFinite, frames >= 1, frames < Double(Int64.max) else {
      throw AudioPipelineFailure.invalidConfiguration
    }
    expectedFrames = Int64(frames)
    // No leading/trailing resampler prime frames are added to the song's output timeline.
    converter.primeMethod = .none
    guard startSeconds.isFinite, startSeconds >= 0 else {
      throw AudioPipelineFailure.invalidConfiguration
    }
    let position = min(
      Double(file.length), (startSeconds * file.processingFormat.sampleRate).rounded())
    file.framePosition = AVAudioFramePosition(position)
    delivered = min(
      expectedFrames,
      Int64((position * format.sampleRate / file.processingFormat.sampleRate).rounded()))
  }

  package func read(maxFrames: Int) throws -> AVAudioPCMBuffer? {
    guard !ended, delivered < expectedFrames else { return nil }
    let capacity = min(maxFrames, Int(expectedFrames - delivered))
    guard capacity > 0,
      let output = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(capacity))
    else { throw AudioPipelineFailure.invalidConfiguration }
    for _ in 0..<16 {
      var failure: Error?
      var error: NSError?
      let status = converter.convert(to: output, error: &error) { [self] packets, status in
        do {
          input.frameLength = 0
          try file.read(into: input, frameCount: min(packets, input.frameCapacity))
          if input.frameLength == 0 {
            status.pointee = .endOfStream
            return nil
          }
          status.pointee = .haveData
          return input
        } catch {
          failure = error
          status.pointee = .endOfStream
          return nil
        }
      }
      if let failure { throw failure }
      if let error { throw error }
      if status == .error { throw AudioPipelineFailure.conversion }
      if status == .endOfStream { ended = true }
      if output.frameLength > 0 {
        delivered += Int64(output.frameLength)
        return output
      }
      if ended { return nil }
    }
    throw AudioPipelineFailure.stalled
  }
}
