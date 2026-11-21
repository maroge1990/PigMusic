import Foundation
import PigDomain
import Testing

/// D-128 (#242): past a queue limit the server queues the songs that fit;
/// the rest are `excluded` in outcomes (which every installed build reads)
/// with their positions in `over_limit`, or, when none fit, a busy
/// `queue_limit`. The iPhone keeps those songs to send again and says how
/// many wait.
struct QueueLimitTests {
  @Test func theAnswerSaysWhichSongsWereLeftOut() throws {
    let json = """
      {"created": 1, "existing": 1, "skipped": 0, "left_out": 2, "over_limit": [1, 3],
       "limit": "rate", "retry_after": 30,
       "outcomes": ["created", "excluded", "existing", "excluded"],
       "downloads_enabled": true}
      """
    let result = try LibraryCodec.decoder().decode(
      AcquisitionResult.self, from: Data(json.utf8))
    #expect(result.leftOut == 2 && result.limit == "rate" && result.retryAfter == 30)
    #expect(result.leftOut(of: ["a", "b", "c", "d"]) == ["b", "d"])
    // A song left out is not taken: it stays missing until sent again.
    #expect(result.songsTaken(4) == [true, false, true, false])
    #expect(
      result.sentence
        == "Adding 2 songs to your library. 2 songs waiting to be sent. PigMusic will try again later."
    )
  }

  @Test func aTrueExclusionIsNotKept() throws {
    let json = """
      {"created": 0, "existing": 0, "skipped": 1, "outcomes": ["excluded"],
       "downloads_enabled": true}
      """
    let result = try LibraryCodec.decoder().decode(
      AcquisitionResult.self, from: Data(json.utf8))
    #expect(result.leftOut(of: ["a"]).isEmpty)
    #expect(result.outboxAnswer == .skipped)
  }

  @Test func anOlderServerLeavesNothingOut() throws {
    let json = #"{"created": 1, "existing": 0, "skipped": 0, "downloads_enabled": true}"#
    let result = try LibraryCodec.decoder().decode(
      AcquisitionResult.self, from: Data(json.utf8))
    #expect(result.leftOut == 0 && result.leftOut(of: ["a"]).isEmpty)
    #expect(result.outboxAnswer == .requested)
  }

  @Test func aBusyQueueLimitLeavesEverySongOut() {
    let result = AcquisitionResult.limited(3, limit: "rate", retryAfter: 90)
    #expect(result.leftOut(of: [1, 2, 3]) == [1, 2, 3])
    #expect(result.outboxAnswer == .kept && result.retryAfter == 90)
    #expect(result.sentence == "3 songs waiting to be sent. PigMusic will try again later.")
    #expect(result.menuNotice == result.sentence)
  }

  @Test func theOutboxKeepsOnlyASongLeftOut() {
    func answer(_ outcome: String, limited: Bool = false) -> AcquisitionResult.OutboxAnswer {
      AcquisitionResult(
        created: outcome == "created" ? 1 : 0, existing: 0,
        skipped: outcome == "excluded" && !limited ? 1 : 0,
        paused: outcome == "paused" ? 1 : 0, outcomes: [outcome], leftOut: limited ? 1 : 0,
        overLimit: limited ? [0] : [], downloadsEnabled: true
      ).outboxAnswer
    }
    #expect(answer("excluded", limited: true) == .kept)
    #expect(answer("created") == .requested)
    #expect(answer("paused") == .paused)
    #expect(answer("excluded") == .skipped)
  }

  @Test func severalAnswersCountTheSongsLeftOut() {
    let combined = AcquisitionResult.combined([
      AcquisitionResult(created: 2, existing: 0, skipped: 0, downloadsEnabled: true),
      .limited(1, limit: "person_waiting", retryAfter: nil),
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

  @Test func aRetryARefusedLimitSaysWhy() {
    #expect(
      AcquisitionResult.retryRefused("household_waiting")
        == "Couldn't try again yet: the server already has 10,000 songs waiting. Try again once some are added."
    )
    #expect(AcquisitionResult.retryRefused("person_waiting").hasPrefix("Couldn't try again yet:"))
  }

  @Test func theOutboxNoticeSaysHowManyWait() {
    #expect(
      RequestNotice.text(refused: 0, reason: nil, excluded: nil, paused: false, waiting: 4)
        == "4 songs waiting to be sent. PigMusic will try again later.")
    #expect(RequestNotice.text(refused: 0, reason: nil, excluded: nil, paused: false) == nil)
  }
}

/// Codex round 1 on #242: songs a limit kept were sent again only at the
/// next refresh, starting at the same song. Now the rate waits for the
/// server's Retry-After; a waiting limit backs off from a minute to
/// fifteen, or tries when the queue changes, at most once a minute.
struct RequestRetryTests {
  @Test func theRateWaitsForTheServersRetryAfter() {
    var retry = RequestRetry()
    #expect(RequestRetry.stopsPass("rate"))
    #expect(retry.passed(limit: "rate", retryAfter: 42, at: 0) == 42)
    #expect(retry.passed(limit: "rate", retryAfter: nil, at: 50) == 60)
    #expect(retry.passed(limit: "rate", retryAfter: 0, at: 60) == 1)
    // A queue change doesn't free the rate.
    #expect(!retry.passOnQueueChange(at: 1000))
  }

  @Test func aWaitingLimitBacksOffAndTriesOnAQueueChange() {
    var retry = RequestRetry()
    #expect(!RequestRetry.stopsPass("person_waiting"))
    let delays = (0..<7).map { i in
      retry.passed(limit: "person_waiting", retryAfter: nil, at: Double(i))
    }
    #expect(delays == [60, 120, 240, 480, 900, 900, 900])
    #expect(!retry.passOnQueueChange(at: 6 + 59))
    #expect(retry.passOnQueueChange(at: 6 + 60))
  }

  @Test func aPassThatKeptNothingForgetsItAll() {
    var retry = RequestRetry()
    _ = retry.passed(limit: "household_waiting", retryAfter: nil, at: 0)
    #expect(retry.passed(limit: nil, retryAfter: nil, at: 100) == nil)
    #expect(retry == RequestRetry())
    #expect(!retry.passOnQueueChange(at: 1000))
    #expect(retry.passed(limit: "household_waiting", retryAfter: nil, at: 200) == 60)
  }
}
