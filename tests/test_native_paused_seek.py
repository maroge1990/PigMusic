"""Run the app's real pause/seek/publication methods with controlled OS adapters.

This deliberately compiles method bodies from PlayerController, not a copy of
its seek logic. It can verify persistence/publication without an iOS runtime;
physical audio-session and lock-screen presentation still need device checks.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def swift_block(source, marker):
    start = source.index(marker)
    brace = source.index("{", start)
    depth = 1
    end = brace + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


@pytest.mark.skipif(
    sys.platform != "darwin" or not shutil.which("swiftc"),
    reason="native Swift compiler is checked on macOS",
)
def test_paused_fallback_seek_persists_and_publishes_without_a_clock(tmp_path):
    controller = (ROOT / "swift/PigMusic/PlayerController.swift").read_text()
    playback = ROOT / "swift/PigKit/Sources/PigPlayback"
    methods = "\n".join(
        swift_block(controller, marker)
        for marker in (
            "  private func checkpoint(",
            "  private func suspend()",
            "  func pause()",
            "  func seek(",
            "  private func publishNowPlaying()",
        )
    )
    clock = swift_block(
        (playback / "PlaybackResources.swift").read_text(),
        "@MainActor public final class PlaybackProgressClock",
    )
    seek_policy = swift_block(
        (playback / "RealtimeQueueSession.swift").read_text(),
        "public enum PlaybackSeek",
    )
    intent = (playback / "PlaybackIntent.swift").read_text()
    adapters = r"""
struct CMTime {
  var seconds: Double
  init(seconds: Double, preferredTimescale: Int) { self.seconds = seconds }
}
@MainActor final class PlayerAdapter {
  var seconds = 30.0
  func currentTime() -> CMTime { CMTime(seconds: seconds, preferredTimescale: 600) }
  func pause() {}
  func seek(to time: CMTime) { seconds = time.seconds }
}
struct Song { var title = "Fixture"; var artist = "Fixture"; var album: String? }
struct Occurrence { var track = Song() }
struct Queue { var current: Occurrence? = Occurrence() }
struct PlaybackCheckpoint { let queue: Queue; let elapsed: Double }
struct QueueStartAdapter {
  var waiting = false
  func seconds(for occurrence: Occurrence?) -> Double { 0 }
  mutating func seek(_ queue: Queue, to seconds: Double) {}
}
@MainActor final class EngineAdapter { func pause() {}; func seek(_ seconds: Double) {} }
let MPMediaItemPropertyTitle = "title"
let MPMediaItemPropertyArtist = "artist"
let MPMediaItemPropertyAlbumTitle = "album"
let MPMediaItemPropertyPlaybackDuration = "duration"
let MPNowPlayingInfoPropertyElapsedPlaybackTime = "elapsed"
let MPNowPlayingInfoPropertyPlaybackRate = "rate"
let MPMediaItemPropertyArtwork = "artwork"
@MainActor final class MPNowPlayingInfoCenter {
  static let shared = MPNowPlayingInfoCenter()
  static func `default`() -> MPNowPlayingInfoCenter { shared }
  var nowPlayingInfo: [String: Any]?
}
@MainActor final class ControllerProbe {
  let clock = PlaybackProgressClock(interval: .milliseconds(5))
  let player = PlayerAdapter()
  var queue = Queue()
  var queueStart = QueueStartAdapter()
  var intent = PlaybackIntent()
  var elapsed = 30.0
  var duration = 180.0
  var durationIsEstimate = false
  var playing = true
  var usingEngine = false
  var engine: EngineAdapter?
  var currentArtwork: String?
  var checkpointTime = Date.distantPast
  var onCheckpoint: ((PlaybackCheckpoint) -> Void)?
  func measureListen() {}
  func releaseIdleSession() {}
  func timeStart(_ reason: String) {}
"""
    scenario = r"""
}
func verify(_ condition: Bool, _ message: String = "Regression check failed") {
  if !condition {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(1)
  }
}
@main struct PausedSeekRegression {
  @MainActor static func main() async throws {
    let saved = URL(fileURLWithPath: CommandLine.arguments[1])
    let probe = ControllerProbe()
    probe.onCheckpoint = { checkpoint in
      try! JSONEncoder().encode(checkpoint.elapsed).write(to: saved)
    }
    var ticks = 0
    probe.clock.start { ticks += 1 }
    try await Task.sleep(for: .milliseconds(20))
    probe.pause()
    let pausedTicks = ticks
    verify(!probe.clock.isRunning)
    probe.seek(90)
    try await Task.sleep(for: .milliseconds(30))
    let restored = try JSONDecoder().decode(Double.self, from: Data(contentsOf: saved))
    verify(restored == 90, "Saved position did not follow paused seek")
    let info = MPNowPlayingInfoCenter.default().nowPlayingInfo!
    verify(info[MPNowPlayingInfoPropertyElapsedPlaybackTime] as? Double == 90,
                 "Now Playing position did not follow paused seek")
    verify(info[MPNowPlayingInfoPropertyPlaybackRate] as? Double == 0)
    verify(ticks == pausedTicks && !probe.clock.isRunning,
                 "Seeking while paused restarted periodic work")
  }
}
"""
    source = tmp_path / "PausedSeek.swift"
    source.write_text(
        "\n".join(
            (
                "import Foundation",
                intent,
                seek_policy,
                clock,
                adapters,
                methods,
                scenario,
            )
        )
    )
    executable = tmp_path / "paused-seek"
    environment = {**os.environ, "CLANG_MODULE_CACHE_PATH": str(tmp_path / "modules")}
    compile_result = subprocess.run(
        [
            shutil.which("swiftc"),
            "-swift-version",
            "6",
            "-parse-as-library",
            "-module-cache-path",
            str(tmp_path / "modules"),
            str(source),
            "-o",
            str(executable),
        ],
        capture_output=True,
        text=True,
        env=environment,
        timeout=120,
    )
    assert compile_result.returncode == 0, compile_result.stderr
    result = subprocess.run(
        [str(executable), str(tmp_path / "position.json")],
        capture_output=True,
        text=True,
        env=environment,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr + result.stdout
