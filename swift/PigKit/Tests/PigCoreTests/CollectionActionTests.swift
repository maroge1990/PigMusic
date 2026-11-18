import PigDomain
import Testing

/// Codex round 2 on batch D: a collection's Add keeps what the server said,
/// paused songs included, and Play never stops what plays for nothing.
struct CollectionActionTests {
  private func result(
    _ created: Int, _ existing: Int = 0, skipped: Int = 0, paused: Int = 0, enabled: Bool = true
  ) -> AcquisitionResult {
    AcquisitionResult(
      created: created, existing: existing, skipped: skipped, paused: paused,
      downloadsEnabled: enabled)
  }

  /// A playlist's batches are added up, paused songs included.
  @Test func batchesAddUpWithTheirPausedSongs() {
    let total = AcquisitionResult.combined([
      result(2, skipped: 1), result(0, 1, paused: 2, enabled: false),
    ])
    #expect(total.created == 2 && total.existing == 1 && total.skipped == 1)
    #expect(total.paused == 2)
    #expect(!total.downloadsEnabled)
    #expect(AcquisitionResult.combined([]).paused == 0)
  }

  /// A mix's Add: a plain success is a haptic; paused songs say where to
  /// resume them, alone or beside added ones.
  @Test func aMenusAddSaysWhereToResumePausedSongs() {
    #expect(result(1).menuNotice == nil)
    #expect(result(0, paused: 1).menuNotice == "Paused. Resume it in Downloads.")
    #expect(
      result(2, paused: 1).menuNotice
        == "Adding 2 songs to your library. Paused. Resume it in Downloads.")
    #expect(
      result(1, enabled: false).menuNotice
        == "Adding. Downloads are off on the server, so it will wait.")
  }
}
