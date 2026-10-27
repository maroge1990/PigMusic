import Foundation
import Testing

@testable import PigPlayback

private func marker(_ index: Int, at frame: Int64) -> QueueAudioMarker {
  QueueAudioMarker(index: index, switchFrame: frame, originFrame: frame, durationFrames: 48000)
}

@Test func blocksThatArriveFasterThanPlaybackAreNeverSilent() {
  var timeline = PlaybackTimeline(sampleRate: 1000)
  timeline.received(frames: 1000, markers: [marker(0, at: 0)], at: .milliseconds(800))
  timeline.received(frames: 1000, markers: [], at: .milliseconds(900))
  timeline.received(frames: 1000, markers: [marker(1, at: 2000)], at: .milliseconds(1000))
  #expect(timeline.firstSound == .milliseconds(800))
  #expect(timeline.songs.map(\.index) == [0, 1])
  #expect(timeline.songs.map(\.stalls) == [0, 0])
  #expect(timeline.songs.map(\.frames) == [2000, 1000])
  #expect(timeline.songs[1].firstAudio == .milliseconds(1000))
}

@Test func aLateBlockIsSilenceForTheSongItBelongsTo() {
  var timeline = PlaybackTimeline(sampleRate: 1000)
  // One second of audio at 0 s: the player runs dry at 1 s.
  timeline.received(frames: 1000, markers: [marker(0, at: 0)], at: .zero)
  // The next block comes at 1.5 s: half a second of silence in song 1.
  timeline.received(frames: 1000, markers: [], at: .milliseconds(1500))
  // Playback resumed at 1.5 s, so this one (due at 2.5 s) is on time.
  timeline.received(frames: 1000, markers: [], at: .milliseconds(2400))
  // Song 2 starts late: the gap is that song's wait, not song 1's.
  timeline.received(frames: 1000, markers: [marker(1, at: 3000)], at: .milliseconds(4000))
  #expect(timeline.songs[0].stalls == 1 && timeline.songs[0].silent == .milliseconds(500))
  #expect(timeline.songs[1].stalls == 1 && timeline.songs[1].silent == .milliseconds(500))
  // A marker re-sent for the playing song is not a new song; tiny gaps are ignored.
  timeline.received(frames: 1000, markers: [marker(1, at: 3000)], at: .milliseconds(5020))
  #expect(timeline.songs.count == 2 && timeline.songs[1].stalls == 1)
}
