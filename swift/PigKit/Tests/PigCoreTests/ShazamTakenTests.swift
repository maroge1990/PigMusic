import PigDomain
import Testing

/// #259: a Shazamed song is marked Adding only when the server took it. A
/// batch answer gives totals, so only a batch the server took whole marks
/// its songs; otherwise each song's own answer decides.
struct ShazamTakenTests {
  private func result(created: Int = 0, existing: Int = 0, skipped: Int = 0, paused: Int = 0)
    -> AcquisitionResult
  {
    AcquisitionResult(
      created: created, existing: existing, skipped: skipped, paused: paused,
      downloadsEnabled: true)
  }

  @Test func aBatchTheServerTookWholeMarksEverySong() {
    #expect(result(created: 2, existing: 1).tookAll(3))
  }

  @Test func aBatchWithASkipOrAPauseCantSayWhichSongWasTaken() {
    // One excluded (or already in your library): which one isn't said.
    #expect(!result(created: 2, skipped: 1).tookAll(3))
    #expect(!result(existing: 2, paused: 1).tookAll(3))
    #expect(!result(skipped: 1).tookAll(1))
  }

  /// Codex round 1 on batch K: the server's answer for each song decides;
  /// a song in your library now is no longer missing either.
  @Test func eachSongIsTakenByTheServersAnswerForIt() {
    let answer = AcquisitionResult(
      created: 1, existing: 2, skipped: 2, paused: 1,
      outcomes: ["created", "added", "excluded", "paused", "existing", "in_library"],
      downloadsEnabled: true)
    #expect(answer.songsTaken(6) == [true, true, false, false, true, true])
  }

  /// An older server gives totals only: every song when it took them all,
  /// none otherwise (asking again to find out could undo a cancel).
  @Test func anOlderServersTotalsMarkAllOrNone() {
    #expect(result(created: 2, existing: 1).songsTaken(3) == [true, true, true])
    #expect(result(created: 2, skipped: 1).songsTaken(3) == [false, false, false])
    // Outcomes that don't match the songs sent are not trusted.
    let short = AcquisitionResult(
      created: 2, existing: 0, skipped: 1, outcomes: ["created"], downloadsEnabled: true)
    #expect(short.songsTaken(3) == [false, false, false])
  }
}
