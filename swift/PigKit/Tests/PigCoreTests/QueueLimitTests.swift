import Foundation
import PigDomain
import Testing

/// D-128 (#242): past a queue limit the server queues the songs that fit and
/// answers the rest `over_limit` (or, when none fit, a busy `queue_limit`).
/// The iPhone keeps those songs to send again and says how many wait.
struct QueueLimitTests {
  @Test func theAnswerSaysWhichSongsWereLeftOut() throws {
    let json = """
      {"created": 1, "existing": 1, "skipped": 0, "left_out": 2, "limit": "rate",
       "retry_after": 30,
       "outcomes": ["created", "over_limit", "existing", "over_limit"],
       "downloads_enabled": true}
      """
    let result = try LibraryCodec.decoder().decode(
      AcquisitionResult.self, from: Data(json.utf8))
    #expect(result.leftOut == 2)
    #expect(result.leftOut(of: ["a", "b", "c", "d"]) == ["b", "d"])
    // A song left out is not taken: it stays missing until sent again.
    #expect(result.songsTaken(4) == [true, false, true, false])
    #expect(
      result.sentence
        == "Adding 2 songs to your library. 2 songs waiting to be sent. PigMusic will try again later."
    )
  }

  @Test func anOlderServerLeavesNothingOut() throws {
    let json = #"{"created": 1, "existing": 0, "skipped": 0, "downloads_enabled": true}"#
    let result = try LibraryCodec.decoder().decode(
      AcquisitionResult.self, from: Data(json.utf8))
    #expect(result.leftOut == 0 && result.leftOut(of: ["a"]).isEmpty)
    #expect(result.outboxAnswer == .requested)
  }

  @Test func aBusyQueueLimitLeavesEverySongOut() {
    let result = AcquisitionResult.limited(3)
    #expect(result.leftOut(of: [1, 2, 3]) == [1, 2, 3])
    #expect(result.outboxAnswer == .kept)
    #expect(result.sentence == "3 songs waiting to be sent. PigMusic will try again later.")
    #expect(result.menuNotice == result.sentence)
  }

  @Test func theOutboxKeepsOnlyASongLeftOut() {
    func answer(_ outcome: String) -> AcquisitionResult.OutboxAnswer {
      AcquisitionResult(
        created: outcome == "created" ? 1 : 0, existing: 0,
        skipped: outcome == "excluded" ? 1 : 0, paused: outcome == "paused" ? 1 : 0,
        outcomes: [outcome], leftOut: outcome == "over_limit" ? 1 : 0, downloadsEnabled: true
      ).outboxAnswer
    }
    #expect(answer("over_limit") == .kept)
    #expect(answer("created") == .requested)
    #expect(answer("paused") == .paused)
    #expect(answer("excluded") == .skipped)
  }

  @Test func severalAnswersCountTheSongsLeftOut() {
    let combined = AcquisitionResult.combined([
      AcquisitionResult(created: 2, existing: 0, skipped: 0, downloadsEnabled: true),
      .limited(1),
    ])
    #expect(combined.leftOut == 1)
    #expect(
      AcquisitionResult.playlistNotice(added: 0, requested: [combined])
        == "Adding 2 songs to your library. 1 song waiting to be sent. PigMusic will try again later."
    )
  }

  @Test func anImportSaysHowManyMissingSongsWereLeftOut() throws {
    let playlist = try LibraryCodec.decoder().decode(
      Playlist.self, from: Data(#"{"id": "p", "name": "P", "revision": 0}"#.utf8))
    let counts = AppleImportCounts(
      inLibrary: 0, household: 0, missing: 3, requested: 1, leftOut: 2)
    #expect(
      AppleImport.summary(AppleImportResult(playlist: playlist, counts: counts, capped: false))
        == "Added to your playlists: 3 songs not on the server yet. Adding 1 song to your library; they join the playlist once added. Couldn't add 2 songs yet: too many are waiting on the server. Add them from the playlist later."
    )
  }

  @Test func theOutboxNoticeSaysHowManyWait() {
    #expect(
      RequestNotice.text(refused: 0, reason: nil, excluded: nil, paused: false, waiting: 4)
        == "4 songs waiting to be sent. PigMusic will try again later.")
    #expect(RequestNotice.text(refused: 0, reason: nil, excluded: nil, paused: false) == nil)
  }
}
