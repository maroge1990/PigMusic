import Foundation
import PigPlayback
import PigProbes

let arguments = CommandLine.arguments
let bounded = arguments.dropFirst().first == "--bounded"
let values = bounded ? Array(arguments.dropFirst(2)) : Array(arguments.dropFirst())
guard values.count == 4, let seconds = Double(values[3]), seconds.isFinite,
  (0...12).contains(seconds)
else {
  FileHandle.standardError.write(
    Data(
      "Usage: PigAudioProbe [--bounded] first-audio second-audio output.wav crossfade-seconds (0 for gapless)\n"
        .utf8))
  exit(2)
}
do {
  if bounded {
    let result = try await BoundedAudioEngineProbe.render(
      first: URL(fileURLWithPath: values[0]),
      second: URL(fileURLWithPath: values[1]), output: URL(fileURLWithPath: values[2]),
      crossfadeSeconds: seconds)
    print(
      "Engine rendered \(result.frames) frames at 48000 Hz stereo; overlap \(result.overlapFrames), peak held \(result.peakRetainedFrames), peak scheduled \(result.peakScheduledFrames) frames."
    )
    exit(0)
  }
  let result = try PCMTransitionProbe.render(
    first: URL(fileURLWithPath: values[0]), second: URL(fileURLWithPath: values[1]),
    output: URL(fileURLWithPath: values[2]), crossfadeSeconds: seconds)
  print(
    "Rendered \(result.frames) frames at \(result.sampleRate) Hz, \(result.channels) channels; overlap \(result.overlapFrames) frames."
  )
} catch {
  FileHandle.standardError.write(Data("Audio probe failed: \(error)\n".utf8))
  exit(1)
}
