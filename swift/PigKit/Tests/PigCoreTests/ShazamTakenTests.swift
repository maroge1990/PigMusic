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

  @Test func oneSongAloneIsTakenWhenTheServerIsGettingIt() {
    #expect(result(created: 1).tookIt)
    #expect(result(existing: 1).tookIt)
    #expect(!result(skipped: 1).tookIt)
    #expect(!result(paused: 1).tookIt)
  }
}
