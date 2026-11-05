import Foundation
import PigPlayback
import Testing

// #124, Codex: listening paused PigMusic and resumed it unconditionally
// afterwards, overriding a pause, interruption or unplugged headphones that
// came meanwhile. It never resumes by itself now; "Resume playback" is
// offered only while playback is exactly as listening left it.

private struct Player {
  var intent = PlaybackIntent()
  var item: UUID? = UUID()
  var playing = false
  var mark: PlaybackMark { PlaybackMark(item: item, revision: intent.revision) }
  /// Listening's own pause.
  mutating func pauseForListening() -> ListeningPause {
    intent.pause()
    playing = false
    return ListeningPause(after: mark)
  }
  func offersResume(_ pause: ListeningPause) -> Bool {
    pause.offersResume(now: mark, playing: playing)
  }
}

private func playingPlayer() -> Player {
  var player = Player()
  player.intent.choosePlay()
  player.playing = true
  return player
}

@Test func resumeIsOfferedWhilePlaybackIsAsListeningLeftIt() {
  var player = playingPlayer()
  let pause = player.pauseForListening()
  #expect(player.offersResume(pause))
}

@Test func aLaterPauseHidesResumeEvenThoughTheStateLooksTheSame() {
  // A remote Pause (or headphones unplugged, which pauses) while listening.
  var player = playingPlayer()
  let pause = player.pauseForListening()
  player.intent.pause()
  #expect(!player.intent.wantsPlayback)
  #expect(!player.offersResume(pause))
}

@Test func anInterruptionHidesResume() {
  var player = playingPlayer()
  let pause = player.pauseForListening()
  player.intent.beginInterruption()
  #expect(!player.offersResume(pause))
  // Still hidden once iOS ends it.
  player.intent.endInterruption(shouldResume: false)
  #expect(!player.offersResume(pause))
}

@Test func anotherSongOrAnEmptiedQueueHidesResume() {
  var player = playingPlayer()
  let pause = player.pauseForListening()
  player.item = UUID()
  #expect(!player.offersResume(pause))

  var stopped = playingPlayer()
  let paused = stopped.pauseForListening()
  stopped.intent.reset()
  stopped.item = nil
  #expect(!stopped.offersResume(paused))
}

@Test func playingAgainHidesResume() {
  var player = playingPlayer()
  let pause = player.pauseForListening()
  player.intent.choosePlay()
  player.playing = true
  #expect(!player.offersResume(pause))
  // Paused again by the person: not listening's pause any more.
  player.intent.pause()
  player.playing = false
  #expect(!player.offersResume(pause))
}

@Test func everyChangeOfIntentRaisesTheRevision() {
  var intent = PlaybackIntent()
  var seen = [intent.revision]
  intent.play()
  seen.append(intent.revision)
  intent.choosePlay()
  seen.append(intent.revision)
  intent.beginInterruption()
  seen.append(intent.revision)
  intent.endInterruption(shouldResume: true)
  seen.append(intent.revision)
  intent.pause()
  seen.append(intent.revision)
  intent.reset()
  seen.append(intent.revision)
  #expect(seen == Array(0...6))
  // A reset is not a fresh intent: an old mark never matches it again.
  #expect(intent != PlaybackIntent())
}
