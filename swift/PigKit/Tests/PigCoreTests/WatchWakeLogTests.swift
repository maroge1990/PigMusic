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
    #expect(text.contains("Scheduled refreshes asked for: 1; none pending."))
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
