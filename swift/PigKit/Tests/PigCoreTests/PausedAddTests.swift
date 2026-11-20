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

  /// The request list, fetched after every mark made so far.
  private func listed(_ marks: inout RequestMarks, _ statuses: [String: String]) {
    let fetched = marks.asking()
    marks.reconcile(statuses: statuses, fetched: fetched)
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
    // A request the list leaves out says nothing, even on a one-page list:
    // the server folds a song's jobs into one entry (Codex round 2).
    listed(&marks, ["r1": "waiting_for_sources", "r2": "paused"])
    #expect(marks.paused == ["still", "unlisted"])
    listed(&marks, ["r2": "paused"])
    #expect(marks.paused == ["still", "unlisted"])
    listed(&marks, ["r2": "completed", "r3": "waiting_for_sources"])
    #expect(marks.paused.isEmpty)
  }

  /// An older server's mark names no request: the list can't place it.
  @Test func theRequestListLeavesAMarkWithoutARequest() {
    var marks = RequestMarks()
    marks.queued("old")
    let asked = marks.asking()
    marks.pausedOnServer(["old": []], asked: asked)
    listed(&marks, [:])
    #expect(marks.paused == ["old"])
  }

  /// Codex round 2 on batch K: one drain sends its songs one by one. A
  /// Resume between two of them, then a new pause, answered for the later
  /// song, is a fresh pause: each song is timed by its own Add.
  @Test func eachAddIsTimedWhenItIsSent() {
    var marks = RequestMarks()
    marks.queued("first")
    marks.queued("later")
    var askedAt: [String: Int] = [:]
    askedAt["first"] = marks.asking()
    marks.resumed(request: "r1")
    askedAt["later"] = marks.asking()
    marks.pausedOnServer(["first": ["r1"], "later": ["r1"]], askedAt: askedAt)
    #expect(marks.paused == ["later"])
    #expect(marks.contains("first"))
  }

  /// Codex confirmation on batch K: the request list only ever clears
  /// marks. A page that reports a request paused never marks a song (a page
  /// fetched before a Resume could otherwise restore Paused after it).
  @Test func theRequestListNeverSetsAMark() {
    var marks = RequestMarks()
    marks.queued("song")
    let asked = marks.asking()
    marks.resumed(request: "r1")
    marks.pausedOnServer(["song": ["r1"]], asked: asked)
    #expect(marks.paused.isEmpty)
    listed(&marks, ["r1": "paused"])
    #expect(marks.paused.isEmpty)
  }

  /// Codex confirmation on batch K: a list fetched before a newer paused
  /// answer arrived can't clear the mark that answer made.
  @Test func aListFetchedBeforeAMarkCantClearIt() {
    var marks = RequestMarks()
    marks.queued("song")
    let fetched = marks.asking()
    let asked = marks.asking()
    marks.pausedOnServer(["song": ["r1"]], asked: asked)
    marks.reconcile(statuses: ["r1": "waiting_for_sources"], fetched: fetched)
    #expect(marks.paused == ["song"])
    // A list fetched after the mark clears it.
    let later = marks.asking()
    marks.reconcile(statuses: ["r1": "waiting_for_sources"], fetched: later)
    #expect(marks.paused.isEmpty)
  }

  /// Codex round 2 on batch K: a song paused under two requests keeps its
  /// mark while the list reports only one of them, not paused.
  @Test func aMarkStaysWhileAnyOfItsRequestsIsUnreported() {
    var marks = RequestMarks()
    marks.queued("song")
    let asked = marks.asking()
    marks.pausedOnServer(["song": ["a", "b"]], asked: asked)
    listed(&marks, ["a": "waiting_for_sources"])
    #expect(marks.paused == ["song"])
    listed(&marks, ["a": "waiting_for_sources", "b": "downloading"])
    #expect(marks.paused.isEmpty)
  }
}
