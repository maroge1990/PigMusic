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
}
