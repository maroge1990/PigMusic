import Foundation
import PigCompanion
import Testing

/// #219 (D-113's shape on the Watch): the Watch asks watchOS to wake it only
/// while songs wait, every half hour, and stops after two hours without a new
/// song until the app is opened again.
struct WatchRefreshPlanTests {
  let start = Date(timeIntervalSince1970: 1_800_000_000)
  func at(_ hours: Double, minutes: Double = 0) -> Date {
    start.addingTimeInterval(hours * 3600 + minutes * 60)
  }

  @Test func theLimitAndIntervalAreTheIPhones() {
    #expect(WatchRefreshPlan.quietLimit == 2 * 60 * 60)
    #expect(WatchRefreshPlan.interval == 30 * 60)
  }

  @Test func nothingWaitingAsksForNothing() {
    let plan = WatchRefreshPlan.next(
      now: at(0, minutes: 10), waiting: 0, lastAccepted: at(0), lastOpened: at(0))
    #expect(plan == .none)
  }

  @Test func songsWaitingAskForAWakeInHalfAnHour() {
    let plan = WatchRefreshPlan.next(
      now: at(0, minutes: 10), waiting: 3, lastAccepted: nil, lastOpened: at(0))
    #expect(plan == .at(at(0, minutes: 40)))
  }

  @Test func twoHoursWithoutANewSongStops() {
    func plan(_ now: Date) -> WatchRefreshPlan {
      WatchRefreshPlan.next(now: now, waiting: 3, lastAccepted: nil, lastOpened: at(0))
    }
    #expect(plan(at(1, minutes: 59)) == .at(at(2, minutes: 29)))
    #expect(plan(at(2)) == .none)
    #expect(plan(at(30)) == .none)
    // Nothing known at all: none.
    #expect(
      WatchRefreshPlan.next(now: at(0), waiting: 3, lastAccepted: nil, lastOpened: nil) == .none)
  }

  @Test func aNewSongRestartsTheTwoHours() {
    let plan = WatchRefreshPlan.next(
      now: at(3), waiting: 2, lastAccepted: at(1, minutes: 30), lastOpened: at(0))
    #expect(plan == .at(at(3, minutes: 30)))
    let later = WatchRefreshPlan.next(
      now: at(3, minutes: 30), waiting: 2, lastAccepted: at(1, minutes: 30), lastOpened: at(0))
    #expect(later == .none)
  }

  @Test func openingTheAppAllowsItAgain() {
    let stopped = WatchRefreshPlan.next(
      now: at(10), waiting: 2, lastAccepted: at(1), lastOpened: at(0))
    #expect(stopped == .none)
    let reopened = WatchRefreshPlan.next(
      now: at(10), waiting: 2, lastAccepted: at(1), lastOpened: at(10))
    #expect(reopened == .at(at(10, minutes: 30)))
  }

  /// A clock set back leaves marks in the future: they must neither stop
  /// refreshes until the clock catches up nor keep them going for ever.
  @Test func aClockSetBackNeitherStopsNorProlongsForEver() {
    // The last song is a day ahead of the clock: ignored. Opened just now:
    // two hours from now, as usual.
    #expect(
      WatchRefreshPlan.next(now: at(0), waiting: 1, lastAccepted: at(24), lastOpened: at(0))
        == .at(at(0, minutes: 30)))
    #expect(
      WatchRefreshPlan.next(now: at(2), waiting: 1, lastAccepted: at(24), lastOpened: at(0))
        == .none)
    // Only future marks: none, until the app is opened.
    #expect(
      WatchRefreshPlan.next(now: at(0), waiting: 1, lastAccepted: at(24), lastOpened: at(24))
        == .none)
    // A mark a minute ahead (clocks drift) still counts, for at most 2 h and 1 min.
    #expect(
      WatchRefreshPlan.next(
        now: at(0), waiting: 1, lastAccepted: at(0, minutes: 1), lastOpened: nil)
        == .at(at(0, minutes: 30)))
    #expect(
      WatchRefreshPlan.next(
        now: at(2, minutes: 1), waiting: 1, lastAccepted: at(0, minutes: 1), lastOpened: nil)
        == .none)
  }

  @Test func aClockSetForwardStops() {
    let plan = WatchRefreshPlan.next(
      now: at(26), waiting: 1, lastAccepted: at(0, minutes: 30), lastOpened: at(0))
    #expect(plan == .none)
  }

  @Test func oneRefreshPendingAtATime() {
    let plan = WatchRefreshPlan.at(at(0, minutes: 30))
    // Nothing pending: ask.
    #expect(WatchRefreshPlan.request(plan, pending: nil, now: at(0)) == at(0, minutes: 30))
    // One pending and still due: ask for nothing more.
    #expect(WatchRefreshPlan.request(plan, pending: at(0, minutes: 20), now: at(0)) == nil)
    #expect(WatchRefreshPlan.request(plan, pending: at(-1), now: at(0)) == nil)
    // Long overdue (watchOS dropped it) or impossibly far ahead (a clock set
    // back): ask again.
    #expect(WatchRefreshPlan.request(plan, pending: at(-3), now: at(0)) == at(0, minutes: 30))
    #expect(WatchRefreshPlan.request(plan, pending: at(5), now: at(0)) == at(0, minutes: 30))
    // The plan says none: nothing, whatever is pending.
    #expect(WatchRefreshPlan.request(.none, pending: nil, now: at(0)) == nil)
  }
}
