import PigDomain
import Testing

/// #246 (D-115): what adding songs did reads the same everywhere, and a song
/// the server already had (another person's job, a household copy) or
/// skipped is counted, not reported as "Adding 0 songs".
struct AcquisitionSentenceTests {
  private func result(created: Int = 0, existing: Int = 0, skipped: Int = 0) -> AcquisitionResult {
    AcquisitionResult(
      created: created, existing: existing, skipped: skipped, downloadsEnabled: true)
  }

  @Test func newSongsAreBeingAdded() {
    #expect(result(created: 1).sentence == "Adding 1 song to your library.")
    #expect(result(created: 3).sentence == "Adding 3 songs to your library.")
  }

  @Test func songsTheServerAlreadyHadAreBeingAddedToo() {
    #expect(result(existing: 1).sentence == "Adding 1 song to your library.")
  }

  @Test func skippedSongsCouldNotBeAdded() {
    #expect(result(skipped: 2).sentence == "Couldn't add 2 songs.")
  }

  @Test func mixedResultsSayBoth() {
    #expect(
      result(created: 2, existing: 1, skipped: 1).sentence
        == "Adding 3 songs to your library. Couldn't add 1 song.")
  }

  @Test func nothingToAddIsAlreadyInYourLibrary() {
    #expect(result().sentence == "Already in your library.")
  }
}
