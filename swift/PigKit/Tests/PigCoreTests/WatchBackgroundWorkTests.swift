import Foundation
import PigCompanion
import PigDomain
import Testing

/// D-113 (#219): Watch copies may be prepared while the iPhone is away, but
/// the work must stop on its own once the Watch has received nothing for two
/// hours, and start nothing more until the app is opened again.
struct WatchBackgroundWorkTests {
  let start = Date(timeIntervalSince1970: 1_800_000_000)
  func at(_ hours: Double, minutes: Double = 0) -> Date {
    start.addingTimeInterval(hours * 3600 + minutes * 60)
  }

  @Test func theLimitIsTwoHours() {
    #expect(WatchBackgroundWork.quietLimit == 2 * 60 * 60)
  }

  @Test func workGoesOnUntilTwoHoursPassWithoutANewSong() {
    var work = WatchBackgroundWork()
    work.left(at: at(0))
    do { let allowed = work.mayStart(at: at(0)); #expect(allowed) }
    do { let allowed = work.mayStart(at: at(1, minutes: 59)); #expect(allowed) }
    #expect(!work.isStopped)
    do { let allowed = work.mayStart(at: at(2)); #expect(!allowed) }
    #expect(work.isStopped)
    #expect(work.stoppedAt == at(2))
  }

  @Test func aNewSongOnTheWatchRestartsTheClock() {
    var work = WatchBackgroundWork()
    work.left(at: at(0))
    work.received(3, at: at(1, minutes: 30))
    do { let allowed = work.mayStart(at: at(3, minutes: 29)); #expect(allowed) }
    do { let allowed = work.mayStart(at: at(3, minutes: 30)); #expect(!allowed) }
    // A confirmation of nothing new is not a song received.
    var idle = WatchBackgroundWork()
    idle.left(at: at(0))
    idle.received(0, at: at(1, minutes: 30))
    do { let allowed = idle.mayStart(at: at(2)); #expect(!allowed) }
  }

  /// Codex round 1 on batch 24: iOS kept the phone suspended for three hours,
  /// so nothing asked and no stop was recorded; the Watch's confirmation that
  /// came in then restarted the clock and work went on.
  @Test func aLateConfirmationAfterTheLimitNeverRestartsWork() throws {
    var work = WatchBackgroundWork()
    work.left(at: at(0))
    work.received(1, at: at(3))
    #expect(work.isStopped)
    do { let allowed = work.mayStart(at: at(3)); #expect(!allowed) }
    var saved = try JSONDecoder().decode(
      WatchBackgroundWork.self, from: JSONEncoder().encode(work))
    do { let allowed = saved.mayStart(at: at(3, minutes: 1)); #expect(!allowed) }
    // Restored from saved state with nothing asked yet: the same.
    var restored = WatchBackgroundWork()
    restored.left(at: at(0))
    var reloaded = try JSONDecoder().decode(
      WatchBackgroundWork.self, from: JSONEncoder().encode(restored))
    reloaded.received(2, at: at(2, minutes: 30))
    do { let allowed = reloaded.mayStart(at: at(2, minutes: 31)); #expect(!allowed) }
    // A launch in the background with nothing recorded: a confirmation
    // doesn't open a new two hours either.
    var fresh = WatchBackgroundWork()
    fresh.received(1, at: at(5), away: true)
    do { let allowed = fresh.mayStart(at: at(5)); #expect(!allowed) }
    // Within the limit, a confirmation still restarts the clock.
    var live = WatchBackgroundWork()
    live.left(at: at(0))
    live.received(1, at: at(1, minutes: 59))
    do { let allowed = live.mayStart(at: at(3, minutes: 58)); #expect(allowed) }
    // In the foreground nothing is judged.
    var open = WatchBackgroundWork()
    open.received(1, at: at(5), away: false)
    #expect(!open.isStopped)
  }

  @Test func aStopHoldsUntilTheAppIsOpenedAgain() {
    var work = WatchBackgroundWork()
    work.left(at: at(0))
    do { let allowed = work.mayStart(at: at(2, minutes: 1)); #expect(!allowed) }
    // A song the Watch confirms later (one already on its way) doesn't lift it.
    work.received(1, at: at(2, minutes: 5))
    do { let allowed = work.mayStart(at: at(2, minutes: 6)); #expect(!allowed) }
    do { let allowed = work.mayStart(at: at(30)); #expect(!allowed) }
    // A launch for a background refresh doesn't either: the saved state comes back.
    let data = try! JSONEncoder().encode(work)
    var relaunched = try! JSONDecoder().decode(WatchBackgroundWork.self, from: data)
    do { let allowed = relaunched.mayStart(at: at(30, minutes: 1)); #expect(!allowed) }
    // Opening the app lifts it; leaving again starts a new two hours.
    relaunched.entered(at: at(31))
    #expect(!relaunched.isStopped)
    relaunched.left(at: at(32))
    do { let allowed = relaunched.mayStart(at: at(33, minutes: 59)); #expect(allowed) }
    do { let allowed = relaunched.mayStart(at: at(34)); #expect(!allowed) }
  }

  /// The Watch repeats its confirmation of what it holds; only songs whose
  /// arrival is new count, once each, so repeats can't keep work going.
  @Test func onlyNewArrivalsCountAsReceived() {
    var log = WatchDeliveryLog()
    log.sent(["a", "b", "c"], at: at(0))
    #expect(log.newArrivals(["a", "b", "old"]) == 2)
    log.arrived("a", at: at(1))
    log.arrived("b", at: at(1))
    #expect(log.newArrivals(["a", "b", "old"]) == 0)
    #expect(log.newArrivals(["a", "c", "c"]) == 1)
  }

  @Test func nothingRecordedOrAClockSetBackStops() {
    var fresh = WatchBackgroundWork()
    do { let allowed = fresh.mayStart(at: at(0)); #expect(!allowed) }
    var back = WatchBackgroundWork()
    back.left(at: at(10))
    do { let allowed = back.mayStart(at: at(10, minutes: -4)); #expect(allowed) }
    do { let allowed = back.mayStart(at: at(10, minutes: -6)); #expect(!allowed) }
  }

  @Test func theStopAndItsEventsAreInTheExport() {
    var work = WatchBackgroundWork()
    work.left(at: at(0))
    work.note(.queued, count: 12, waiting: 3, at: at(0, minutes: 1))
    work.note(.finished, count: 12, at: at(0, minutes: 20))
    work.received(12, at: at(0, minutes: 40))
    _ = work.mayStart(at: at(2, minutes: 41))
    let text = work.summary(now: at(3))
    #expect(text.contains("stops after 2 h without a new song on the Watch"))
    #expect(text.contains("12 copies queued for background download, 3 still being prepared"))
    #expect(text.contains("Watch confirmed 12 new songs"))
    #expect(text.contains("stopped by the 2-hour limit"))
    #expect(text.contains("nothing more starts in the background until PigMusic is opened"))
    #expect(work.events.map(\.kind) == [.left, .queued, .finished, .received, .stopped])
    for _ in 0..<(WatchBackgroundWork.eventCapacity + 5) { work.note(.refreshRan, at: at(4)) }
    #expect(work.events.count == WatchBackgroundWork.eventCapacity)
  }
}
