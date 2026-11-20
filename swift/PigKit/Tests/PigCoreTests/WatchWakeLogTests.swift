import Foundation
import PigCompanion
import Testing

/// #219: the Watch records when watchOS gave it time and what it did with it,
/// so the diagnostics tell "never woken" from "woken but didn't accept".
struct WatchWakeLogTests {
  let start = Date(timeIntervalSince1970: 1_800_000_000)
  func at(_ hours: Double, minutes: Double = 0) -> Date {
    start.addingTimeInterval(hours * 3600 + minutes * 60)
  }

  @Test func countsEachKindOfWake() {
    var log = WatchWakeLog()
    log.launched(background: true, at: at(0))
    log.launched(background: false, at: at(1))
    log.opened(at: at(1))
    log.linkTask(pending: true, at: at(2))
    log.linkTask(pending: false, at: at(3))
    log.refreshAsked(for: at(3, minutes: 30), at: at(3))
    log.refreshRan(at: at(4))
    #expect(log.count(.launched, now: at(5)) == 2)
    #expect(log.count(.launched, now: at(5)) { $0.active == false } == 1)
    #expect(log.count(.linkTask, now: at(5)) == 2)
    #expect(log.count(.linkTask, now: at(5)) { $0.pending == true } == 1)
    #expect(log.count(.refreshRan, now: at(5)) == 1)
    #expect(log.lastOpened == at(1))
    // A launch in the background is not the person opening the app.
    var away = WatchWakeLog()
    away.launched(background: true, at: at(0))
    #expect(away.lastOpened == nil)
    away.launched(background: false, at: at(1))
    #expect(away.lastOpened == at(1))
    // The refresh that ran is no longer pending.
    #expect(log.pendingRefresh == nil)
  }

  @Test func refreshAskedIsPendingUntilItRunsOrIsTurnedDown() {
    var log = WatchWakeLog()
    log.refreshAsked(for: at(0, minutes: 30), at: at(0))
    #expect(log.pendingRefresh == at(0, minutes: 30))
    log.refreshRefused(at: at(0))
    #expect(log.pendingRefresh == nil)
  }

  @Test func acceptedSongsKeepTheirGapAndWhetherTheAppWasOpen() {
    var log = WatchWakeLog()
    log.received(at: at(0))
    log.received(at: at(0))
    log.accepted(receivedAt: at(0), active: false, at: at(0, minutes: 1))
    log.accepted(receivedAt: at(0), active: true, at: at(3))
    // A received time after the accept (clock change) is no negative gap.
    log.accepted(receivedAt: at(5), active: true, at: at(4))
    let accepted = log.events.filter { $0.kind == .accepted }
    #expect(accepted.map(\.gap) == [60, 3 * 3600, 0])
    #expect(log.count(.accepted, now: at(4)) { $0.active == false } == 1)
    #expect(log.lastAccepted == at(4))
  }

  @Test func summarySaysHowOftenTheWatchWasWokenAndWhy() {
    var log = WatchWakeLog()
    log.launched(background: true, at: at(0))
    log.linkTask(pending: true, at: at(0))
    log.received(at: at(0))
    log.accepted(receivedAt: at(0), active: false, at: at(0, minutes: 2))
    log.refreshAsked(for: at(0, minutes: 32), at: at(0, minutes: 2))
    log.refreshRan(at: at(1))
    log.linkTask(pending: false, at: at(2))
    let text = log.summary(now: at(3))
    #expect(
      text.contains(
        "Woken in the background: 3 times in the last 7 days (2 by the iPhone link, 1 by a scheduled refresh)."))
    #expect(text.contains("iPhone link wakes with something waiting: 1 of 2."))
    #expect(text.contains("App started: 1 times (1 in the background); opened by you: 0 times."))
    #expect(text.contains("Scheduled refreshes asked for: 1; ran: 1; turned down by watchOS: 0."))
    #expect(text.contains("Scheduled refresh still waiting: none."))
    #expect(text.contains("Songs received: 1; songs accepted: 1 (1 while the app was not open)."))
    #expect(text.contains("Received to accepted: median 2 min 0 s, longest 2 min 0 s."))
    #expect(text.contains("woken: scheduled refresh"))
    #expect(text.contains("woken: iPhone link, nothing waiting"))
  }

  @Test func anEmptyLogSaysNothingWokeIt() {
    let text = WatchWakeLog().summary(now: at(0))
    #expect(text.contains("Woken in the background: 0 times in the last 7 days"))
    #expect(!text.contains("Received to accepted"))
  }

  @Test func oldEventsAndOverflowAreTrimmed() {
    var log = WatchWakeLog()
    log.linkTask(pending: true, at: at(0))
    log.linkTask(pending: true, at: at(24 * 8))
    #expect(log.events.count == 1)
    for index in 0..<(WatchWakeLog.capacity + 10) {
      log.received(at: at(24 * 8, minutes: Double(index) / 100))
    }
    #expect(log.events.count == WatchWakeLog.capacity)
    #expect(log.events.last?.kind == .received)
    // Counts are of the last seven days only.
    var counted = WatchWakeLog()
    counted.linkTask(pending: true, at: at(0))
    #expect(counted.count(.linkTask, now: at(24 * 6)) == 1)
    #expect(counted.count(.linkTask, now: at(24 * 7 + 1)) == 0)
  }

  /// Codex round 1 on batch G: a request watchOS accepted but never ran
  /// must not read as refreshes working.
  @Test func summaryShowsAskedAgainstRanAndHowLongOneHasWaited() {
    var log = WatchWakeLog()
    log.refreshAsked(for: at(0, minutes: 30), at: at(0))
    let text = log.summary(now: at(3, minutes: 30))
    #expect(text.contains("Scheduled refreshes asked for: 1; ran: 0; turned down by watchOS: 0."))
    #expect(
      text.contains(
        "Scheduled refresh still waiting: one, asked 3 h 30 min ago, for "
          + at(0, minutes: 30).formatted(.iso8601) + " (3 h 0 min overdue)."))
    #expect(
      text.contains(
        "No scheduled refresh has run: watchOS may give no background time to an app "
          + "without a watch-face complication."))
    log.refreshRan(at: at(3, minutes: 40))
    let after = log.summary(now: at(4))
    #expect(after.contains("ran: 1"))
    #expect(!after.contains("No scheduled refresh has run"))
  }

  /// Codex round 1 on batch G: after three quiet hours a song taken in the
  /// background re-armed the refresh chain with no opening of the app.
  @Test func anExpiredWindowStaysStoppedUntilTheAppIsOpened() throws {
    var log = WatchWakeLog()
    log.opened(at: at(0))
    #expect(log.refreshPlan(now: at(0), waiting: 3) == .at(at(0, minutes: 30)))
    // Nothing asked during the quiet hours; a song arrives at 3 h.
    log.accepted(receivedAt: at(3), active: false, at: at(3))
    #expect(log.refreshStoppedAt == at(3))
    #expect(log.refreshPlan(now: at(3), waiting: 2) == .none)
    // Saved and reloaded, still stopped; more background songs don't lift it.
    var saved = try JSONDecoder().decode(WatchWakeLog.self, from: JSONEncoder().encode(log))
    saved.accepted(receivedAt: at(3, minutes: 5), active: false, at: at(3, minutes: 5))
    #expect(saved.refreshPlan(now: at(3, minutes: 5), waiting: 1) == .none)
    #expect(saved.summary(now: at(4)).contains("Scheduled refreshes stopped at "))
    // Opening the app clears it.
    saved.opened(at: at(5))
    #expect(saved.refreshStoppedAt == nil)
    #expect(saved.refreshPlan(now: at(5), waiting: 1) == .at(at(5, minutes: 30)))
    // So does leaving it, which counts as having been seen.
    var left = log
    left.sawPerson(at: at(6))
    #expect(left.refreshPlan(now: at(6), waiting: 1) == .at(at(6, minutes: 30)))
  }

  @Test func theStopIsRecordedWhenThePlanIsAskedLate() {
    var log = WatchWakeLog()
    log.opened(at: at(0))
    #expect(log.refreshPlan(now: at(2, minutes: 1), waiting: 1) == .none)
    #expect(log.refreshStoppedAt == at(2, minutes: 1))
    // The window can't come back by itself, even with the clock set back.
    #expect(log.refreshPlan(now: at(1), waiting: 1) == .none)
  }

  @Test func aCopyTheWatchAlreadyHadIsNotProgress() {
    var log = WatchWakeLog()
    log.opened(at: at(0))
    log.accepted(receivedAt: at(1), active: false, newSong: true, at: at(1))
    #expect(log.lastAccepted == at(1))
    // A repeated transfer at 2 h 30 doesn't move the window on.
    log.accepted(
      receivedAt: at(2, minutes: 30), active: false, newSong: false, at: at(2, minutes: 30))
    #expect(log.lastAccepted == at(1))
    #expect(log.refreshPlan(now: at(2, minutes: 30), waiting: 1) == .at(at(3)))
    #expect(log.refreshPlan(now: at(3), waiting: 1) == .none)
    #expect(log.events.last(where: { $0.kind == .accepted })?.newSong == false)
  }

  @Test func aSongTakenWhileOpenNeverStopsIt() {
    var log = WatchWakeLog()
    log.opened(at: at(0))
    // On screen for three hours: the person is there, so no stop is judged.
    log.accepted(receivedAt: at(3), active: true, at: at(3))
    #expect(log.refreshStoppedAt == nil)
    #expect(log.refreshPlan(now: at(3), waiting: 1) == .at(at(3, minutes: 30)))
  }

  /// D-124 as refined on 20 Nov: the stop guards stuck delivery, never
  /// fresh songs. The owner leaves the Watch app shut for days.
  @Test func quietWithNothingWaitingThenNewSongsAsksForARefresh() {
    var log = WatchWakeLog()
    log.opened(at: at(0))
    // Three hours with nothing waiting: no window runs, no stop.
    #expect(log.refreshPlan(now: at(3), waiting: 0) == .none)
    #expect(log.refreshStoppedAt == nil)
    log.songsListed(["new"], previous: [], at: at(72))
    #expect(log.refreshPlan(now: at(72), waiting: 4) == .at(at(72, minutes: 30)))
    // Still the 2-hour limit from there.
    #expect(log.refreshPlan(now: at(74), waiting: 4) == .none)
    #expect(log.refreshStoppedAt == at(74))
  }

  @Test func stuckThenADuplicateCopyStaysStopped() {
    var log = WatchWakeLog()
    log.songsListed(["a"], previous: [], at: at(0))
    #expect(log.refreshPlan(now: at(2), waiting: 3) == .none)
    #expect(log.refreshStoppedAt == at(2))
    log.accepted(receivedAt: at(2, minutes: 10), active: false, newSong: false, waiting: 3, at: at(2, minutes: 10))
    #expect(log.refreshPlan(now: at(2, minutes: 10), waiting: 3) == .none)
  }

  @Test func stoppedThenNewSongsListedReopens() {
    var log = WatchWakeLog()
    log.songsListed(["a"], previous: [], at: at(0))
    #expect(log.refreshPlan(now: at(3), waiting: 3) == .none)
    log.songsListed(["a", "b"], previous: ["a"], at: at(5))
    #expect(log.refreshStoppedAt == nil)
    #expect(log.refreshPlan(now: at(5), waiting: 4) == .at(at(5, minutes: 30)))
  }

  @Test func aBackgroundSongWithNothingWaitingBeforeRecordsNoStop() {
    var log = WatchWakeLog()
    log.opened(at: at(0))
    log.accepted(receivedAt: at(5), active: false, newSong: false, waiting: 0, at: at(5))
    #expect(log.refreshStoppedAt == nil)
  }

  /// Codex round 2 on batch G: A, B, A, B across expired windows reopened
  /// it every time, with no song taken and the app never opened.
  @Test func alternatingMembershipReopensAtMostOncePerSong() {
    var log = WatchWakeLog()
    log.opened(at: at(0))
    #expect(log.songsListed(["a"], previous: [], at: at(0)) == true)
    #expect(log.refreshPlan(now: at(3), waiting: 1) == .none)
    #expect(log.songsListed(["b"], previous: ["a"], at: at(3)) == true)
    #expect(log.refreshPlan(now: at(6), waiting: 1) == .none)
    // a and b again, and again: both were listed since the app was opened.
    #expect(log.songsListed(["a"], previous: ["b"], at: at(6)) == false)
    #expect(log.songsListed(["b"], previous: ["a"], at: at(9)) == false)
    #expect(log.refreshPlan(now: at(9), waiting: 1) == .none)
    #expect(log.refreshStoppedAt != nil)
    // A genuinely new album later still reopens it.
    #expect(log.songsListed(["b", "c", "d"], previous: ["b"], at: at(20)) == true)
    #expect(log.refreshPlan(now: at(20), waiting: 2) == .at(at(20, minutes: 30)))
  }

  @Test func churnCantKeepRefreshesGoingForEver() {
    var log = WatchWakeLog()
    log.opened(at: at(0))
    var reopened = 0
    var previous: [String] = []
    for step in 0..<20 {
      let ids = ["song\(step)"]
      if log.songsListed(ids, previous: previous, at: at(Double(step) * 3)) { reopened += 1 }
      previous = ids
    }
    #expect(reopened == WatchWakeLog.reopeningLimit)
    #expect(log.reopenings == WatchWakeLog.reopeningLimit)
    #expect(log.refreshPlan(now: at(60), waiting: 1) == .none)
    // Opening the app starts afresh.
    log.opened(at: at(61))
    #expect(log.reopenings == 0 && log.seenSinceOpened.isEmpty)
    #expect(log.songsListed(["song0"], previous: ["song19"], at: at(70)) == true)
  }

  @Test func aFullMemoryOfListedSongsReopensNothing() {
    var log = WatchWakeLog()
    let many = (0..<WatchWakeLog.listedCapacity).map { "id\($0)" }
    #expect(log.songsListed(many, previous: [], at: at(0)) == true)
    #expect(log.seenSinceOpened.count == WatchWakeLog.listedCapacity)
    // Full: a song never seen still counts as seen.
    #expect(log.songsListed(many + ["extra"], previous: many, at: at(3)) == false)
    #expect(log.seenSinceOpened.count == WatchWakeLog.listedCapacity)
  }

  @Test func reSendingTheSameSongsAfterAStopDoesntReopen() {
    var log = WatchWakeLog()
    log.opened(at: at(0))
    #expect(log.songsListed(["a", "b"], previous: [], at: at(0)) == true)
    #expect(log.refreshPlan(now: at(3), waiting: 2) == .none)
    // Removed, then sent again: the known residual, until the app is opened.
    #expect(log.songsListed([], previous: ["a", "b"], at: at(4)) == false)
    #expect(log.songsListed(["a", "b"], previous: [], at: at(5)) == false)
    #expect(log.refreshPlan(now: at(5), waiting: 2) == .none)
  }

  @Test func whatWasListedSurvivesARelaunch() throws {
    var log = WatchWakeLog()
    log.opened(at: at(0))
    log.songsListed(["a"], previous: [], at: at(0))
    log.songsListed(["b"], previous: ["a"], at: at(3))
    var saved = try JSONDecoder().decode(WatchWakeLog.self, from: JSONEncoder().encode(log))
    #expect(saved == log)
    #expect(saved.seenSinceOpened == ["a", "b"] && saved.reopenings == 2)
    #expect(saved.songsListed(["a"], previous: ["b"], at: at(6)) == false)
    // A log saved before these existed decodes with nothing remembered.
    var old = try JSONSerialization.jsonObject(with: JSONEncoder().encode(WatchWakeLog()))
      as! [String: Any]
    old.removeValue(forKey: "listedSeen")
    old.removeValue(forKey: "listedReopenings")
    let decoded = try JSONDecoder().decode(
      WatchWakeLog.self, from: JSONSerialization.data(withJSONObject: old))
    #expect(decoded.reopenings == 0 && decoded.seenSinceOpened.isEmpty)
  }

  @Test func survivesBeingSaved() throws {
    var log = WatchWakeLog()
    log.launched(background: true, at: at(0))
    log.opened(at: at(0, minutes: 5))
    log.refreshAsked(for: at(0, minutes: 35), at: at(0, minutes: 5))
    log.accepted(receivedAt: at(0), active: false, at: at(0, minutes: 6))
    let saved = try JSONDecoder().decode(WatchWakeLog.self, from: JSONEncoder().encode(log))
    #expect(saved == log)
    #expect(saved.pendingRefresh == at(0, minutes: 35))
    #expect(saved.lastOpened == at(0, minutes: 5))
    #expect(saved.lastAccepted == at(0, minutes: 6))
  }
}
