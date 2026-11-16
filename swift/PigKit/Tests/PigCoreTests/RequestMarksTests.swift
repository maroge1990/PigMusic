import PigDomain
import Testing

/// Codex round 1 on #206: a cancelled request must not leave its song
/// showing Requested for the rest of the session.
struct RequestMarksTests {
  @Test func aSongStaysRequestedFromTheTapUntilItsRequestIsCancelled() {
    var marks = RequestMarks()
    marks.queued("a")
    #expect(marks.contains("a"))
    marks.sent(["a"])
    #expect(marks.contains("a"))
    marks.requestsChanged()
    #expect(!marks.contains("a"))
    // It can be requested again.
    marks.queued("a")
    #expect(marks.contains("a"))
  }

  @Test func aWaitingRequestSurvivesACancelElsewhere() {
    var marks = RequestMarks()
    marks.queued("waiting")
    marks.queued("sent")
    marks.sent(["sent"])
    marks.requestsChanged()
    #expect(marks.contains("waiting") && !marks.contains("sent"))
  }

  @Test func aRefusedRequestIsNoLongerMarked() {
    var marks = RequestMarks(waiting: ["a", "b"])
    marks.refused(["a"])
    #expect(!marks.contains("a") && marks.contains("b"))
  }

  @Test func onlyWaitingSongsBecomeSent() {
    var marks = RequestMarks()
    marks.sent(["never-asked"])
    #expect(!marks.contains("never-asked"))
  }

  /// #211: a request the server finished must not keep its song Requested.
  @Test func aCompletedRequestClearsItsMark() {
    var marks = RequestMarks()
    for id in ["done", "failed", "busy", "library"] { marks.queued(id) }
    marks.sent(["done", "failed", "busy"])
    func song(_ id: String, _ state: String, library: String? = nil) -> Track {
      var track = Track(id: id, title: id, artist: "A")
      track.serverState = state
      track.libraryTrackId = library
      return track
    }
    // A fresh server answer: finished (under its library id too), failed,
    // still being got.
    marks.observed([
      song("done", "in_library", library: "lib-done"), song("failed", "failed"),
      song("busy", "requested"),
    ])
    #expect(!marks.contains("done") && !marks.contains("failed"))
    #expect(marks.contains("busy"))
    // A song now in this phone's copy of the library, under any id.
    marks.queued("lib-x")
    marks.observed([song("x", "in_library", library: "lib-x")])
    #expect(!marks.contains("lib-x"))
    marks.settled(["library"])
    #expect(!marks.contains("library"))
    #expect(marks.all == ["busy"])
  }

  /// #206: a row shows Requested as soon as the song is asked for here,
  /// before the server's answer says so; any other state is kept.
  @Test func aRowShowsRequestedOnceTheSongIsAskedFor() {
    var song = Track(id: "s", title: "Song", artist: "A")
    song.serverState = "not_in_library"
    #expect(song.shownState(requested: true) == "requested")
    #expect(song.shownState(requested: false) == "not_in_library")
    for state in ["in_library", "household_available", "failed"] {
      song.serverState = state
      #expect(song.shownState(requested: true) == state)
    }
    song.serverState = nil
    #expect(song.shownState(requested: true) == nil)
  }
}
