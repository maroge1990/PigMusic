import Foundation
@testable import PigDomain
import Testing

/// Codex round 1 on batch E (D-120, #251).
struct PlainScreensQueueTests {
  private func song(_ id: String, _ seconds: Double? = 240) -> Track {
    var track = Track(id: id, title: "Song " + id, artist: "Artist")
    track.duration = seconds
    return track
  }

  /// Codex round 1 on batch E: a song you add after the similar songs stays
  /// in Up next; each song is judged on its own, not by where similar songs
  /// begin.
  @Test func aSongAddedAfterSimilarSongsStaysYours() {
    let upcoming = [song("a"), song("s1"), song("s2"), song("mine")]
    let layout = UpNextLayout(upcoming, keepPlaying: true) { $0.id.hasPrefix("s") }
    #expect(layout.chosen.map(\.id) == ["a", "mine"])
    #expect(layout.similar.map(\.id) == ["s1", "s2"])
    #expect(SongSpan.summary(layout.chosen) == "2 songs · 8 min")
  }
}
