import Foundation
import PigDomain
import Testing

/// #264: Add no longer resumes a song the person paused; the answer says
/// `paused`, and the iPhone says where to resume it, never "excluded" nor
/// "Already in your library".
struct PausedAddTests {
  private func decode(_ json: String) throws -> AcquisitionResult {
    try LibraryCodec.decoder().decode(AcquisitionResult.self, from: Data(json.utf8))
  }

  @Test func anAlbumWhoseSongsWerePausedSaysWhereToResumeThem() throws {
    let result = try decode(
      #"{"created":0,"existing":0,"skipped":0,"paused":3,"downloads_enabled":true}"#)
    #expect(result.paused == 3)
    #expect(result.onlyPaused)
    #expect(result.sentence == "Paused. Resume it in Downloads.")
  }

  @Test func pausedSongsBesideNewOnesAreCounted() throws {
    let result = try decode(
      #"{"created":2,"existing":0,"skipped":0,"paused":1,"downloads_enabled":true}"#)
    #expect(!result.onlyPaused)
    // The same sentence as the web's, after what was added.
    #expect(
      result.sentence == "Adding 2 songs to your library. Paused. Resume it in Downloads.")
  }

  /// An older server sends no `paused`.
  @Test func anAnswerWithoutPausedStillReads() throws {
    let result = try decode(
      #"{"created":0,"existing":0,"skipped":0,"downloads_enabled":true}"#)
    #expect(result.paused == 0)
    #expect(result.sentence == "Already in your library.")
  }

  @Test func aPausedSongIsMarkedPausedUntilTheRequestsChange() {
    var marks = RequestMarks()
    marks.queued("song")
    let asked = marks.asking()
    marks.pausedOnServer(["song": ["request"]], asked: asked)
    #expect(marks.paused == ["song"])
    #expect(marks.contains("song"))
    marks.requestsChanged()
    #expect(!marks.contains("song"))
  }

  /// Codex round 1 on batch D: resuming one request clears only that
  /// song's paused mark; other paused songs and sent marks stay.
  @Test func resumingOneSongKeepsTheOtherMarks() {
    var marks = RequestMarks()
    for id in ["one", "two", "sent"] { marks.queued(id) }
    marks.sent(["sent"])
    let asked = marks.asking()
    marks.pausedOnServer(["one": ["request-one"], "two": ["request-two"]], asked: asked)
    marks.resumed(request: "request-one")
    #expect(marks.paused == ["two"])
    #expect(!marks.contains("one"))
    #expect(marks.contains("two") && marks.contains("sent"))
  }

  /// #276: the server joined an Add from Greatest Hits to the paused request
  /// of the studio album. Resuming that request (its item reads "Studio")
  /// clears the Greatest Hits row's mark, which no title, artist and album
  /// match could.
  @Test func resumingARequestClearsTheOtherEditionsSongPausedUnderIt() {
    var marks = RequestMarks()
    marks.queued("hits-song")
    let asked = marks.asking()
    marks.pausedOnServer(["hits-song": ["studio-request"]], asked: asked)
    marks.resumed(request: "studio-request")
    #expect(!marks.contains("hits-song"))
  }

  /// #276: two requests whose songs read alike (same title, artist and
  /// album) keep their own marks: resuming one leaves the other paused.
  @Test func twoRequestsWithTheSameNamesKeepTheirOwnMarks() {
    var marks = RequestMarks()
    marks.queued("first")
    marks.queued("second")
    let asked = marks.asking()
    marks.pausedOnServer(["first": ["request-a"], "second": ["request-b"]], asked: asked)
    marks.resumed(request: "request-a")
    #expect(!marks.contains("first"))
    #expect(marks.paused == ["second"])
    // A request no paused song was left under clears nothing.
    marks.resumed(request: "someone-elses")
    #expect(marks.paused == ["second"])
  }

  /// An older server names no request: the mark goes on any Resume rather
  /// than stay Paused for the rest of the session.
  @Test func aMarkWithoutARequestGoesOnAnyResume() {
    var marks = RequestMarks()
    marks.queued("old")
    let asked = marks.asking()
    marks.pausedOnServer(["old": []], asked: asked)
    marks.resumed(request: "anything")
    #expect(!marks.contains("old"))
  }

  @Test func theAnswerNamesThePausedRequests() throws {
    let result = try decode(
      #"{"created":0,"existing":0,"skipped":0,"paused":1,"paused_ids":["r1"],"downloads_enabled":true}"#
    )
    #expect(result.pausedIds == ["r1"])
    let older = try decode(
      #"{"created":0,"existing":0,"skipped":0,"downloads_enabled":true}"#)
    #expect(older.pausedIds.isEmpty)
  }

  /// Codex round 1 on batch K: Resume goes through while an Add's answer
  /// is on its way; the late "paused" can't mark the song again. The song
  /// is being added, so it stays marked as asked for.
  @Test func aDelayedAddAnswerCantUndoAResume() {
    var marks = RequestMarks()
    marks.queued("song")
    let asked = marks.asking()
    marks.resumed(request: "r1")
    marks.pausedOnServer(["song": ["r1"]], asked: asked)
    #expect(marks.paused.isEmpty)
    #expect(marks.contains("song"))
    // An Add sent after the Resume and answered "paused" is a new pause.
    marks.queued("again")
    let later = marks.asking()
    marks.pausedOnServer(["again": ["r1"]], asked: later)
    #expect(marks.paused == ["again"])
  }

  /// Codex round 1 on batch K: resumed on another device, or by a Resume
  /// whose answer was lost: the request list says it isn't paused, and the
  /// mark goes. Still paused there, it stays.
  @Test func theRequestListClearsMarksResumedElsewhere() {
    var marks = RequestMarks()
    for id in ["elsewhere", "still", "unlisted"] { marks.queued(id) }
    let asked = marks.asking()
    marks.pausedOnServer(
      ["elsewhere": ["r1"], "still": ["r2"], "unlisted": ["r3"]], asked: asked)
    // One page of a longer list: an unlisted request is not known.
    marks.reconcile(statuses: ["r1": "waiting_for_sources", "r2": "paused"], complete: false)
    #expect(marks.paused == ["still", "unlisted"])
    // The whole list: a request not on it is gone.
    marks.reconcile(statuses: ["r2": "paused"], complete: true)
    #expect(marks.paused == ["still"])
    marks.reconcile(statuses: ["r2": "completed"], complete: true)
    #expect(marks.paused.isEmpty)
  }

  /// An older server's mark names no request: the list can't place it.
  @Test func theRequestListLeavesAMarkWithoutARequest() {
    var marks = RequestMarks()
    marks.queued("old")
    let asked = marks.asking()
    marks.pausedOnServer(["old": []], asked: asked)
    marks.reconcile(statuses: [:], complete: true)
    #expect(marks.paused == ["old"])
  }
}
