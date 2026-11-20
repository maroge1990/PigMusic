import Foundation
import PigCompanion
import Testing

/// D-125, #219: the watch-face complication, its redraws, and the iPhone's
/// rationed wake-ups for the Watch app.
struct WatchComplicationTests {
  let start = Date(timeIntervalSince1970: 1_800_000_000)
  func at(minutes: Double) -> Date { start.addingTimeInterval(minutes * 60) }
  private let gigabyte = WatchStorageFigures.gigabyte
  private func song(_ id: String, bytes: Int64 = 5_000_000) -> WatchSongState {
    WatchSongState(id: id, checksum: "sum-" + id, bytes: bytes, seconds: 200)
  }
  private func state(_ kind: WatchComplicationState.Kind, waiting: Int, title: String = "t")
    -> WatchComplicationState
  {
    WatchComplicationState(kind: kind, title: title, waiting: waiting, updated: start)
  }

  /// The complication says what the Watch's home says, with the same icons
  /// (D-120, D-122); songs on their way read as a count.
  @Test func showsTheHomesWordsAndIcons() {
    let songs = [song("a"), song("b"), song("c"), song("d", bytes: 300_000_000)]
    let ready = WatchComplicationState(
      status: WatchLibraryStatus(
        songs: songs, stored: ["a": "sum-a", "b": "sum-b", "c": "sum-c", "d": "sum-d"],
        usedBytes: 1, limitBytes: gigabyte),
      at: start)
    #expect(ready.kind == .ready)
    #expect(ready.words == "Ready to play")
    #expect(ready.symbol == "checkmark.circle.fill")
    #expect(ready.badge == nil)

    let arriving = WatchComplicationState(
      status: WatchLibraryStatus(
        songs: songs, stored: ["a": "sum-a"], usedBytes: 0, limitBytes: gigabyte,
        iPhoneReachable: true),
      at: start)
    #expect(arriving.kind == .arriving)
    #expect(arriving.words == "3 waiting")
    #expect(arriving.title == "Arriving from iPhone")
    #expect(arriving.symbol == "clock")
    #expect(arriving.badge == "3")

    let away = WatchComplicationState(
      status: WatchLibraryStatus(
        songs: songs, stored: ["a": "sum-a"], usedBytes: 0, limitBytes: gigabyte,
        iPhoneReachable: false),
      at: start)
    #expect(away.words == "Can't reach your iPhone")
    #expect(away.symbol == "iphone.slash")
    #expect(away.badge == "3")

    let full = WatchComplicationState(
      status: WatchLibraryStatus(
        songs: songs, stored: ["a": "sum-a"], usedBytes: gigabyte - 100_000_000,
        limitBytes: gigabyte),
      at: start)
    #expect(full.words == "Storage full")
    #expect(full.symbol == "exclamationmark.circle")

    let unsaved = WatchComplicationState(
      status: WatchLibraryStatus(
        songs: songs, stored: ["a": "sum-a"], usedBytes: 0, limitBytes: gigabyte,
        problems: ["b": .unreadable]),
      at: start)
    #expect(unsaved.words == "Couldn't save 1 song")
    #expect(unsaved.symbol == "exclamationmark.circle")

    // A new library announced and not here yet: nothing counted waiting.
    let announced = WatchComplicationState(
      status: WatchLibraryStatus(
        songs: [song("a")], stored: ["a": "sum-a"], usedBytes: 0, limitBytes: gigabyte,
        manifestApplied: false),
      at: start)
    #expect(announced.words == "Arriving from iPhone")
    #expect(announced.badge == nil)
    // The home's icons are the same table.
    #expect(WatchLibraryStatus.Headline.couldntSave(2).symbol == "exclamationmark.circle")
  }

  /// Codex round 1 on batch J: "Can't reach your iPhone" stayed on the face
  /// for hours after the iPhone came back. It now gives way to the neutral
  /// "PigMusic"; statuses that only change when the app runs stay.
  @Test func onlyTheReachabilityStatusExpires() {
    let away = WatchComplicationState(
      kind: .iPhoneAway, title: "Can't reach your iPhone", waiting: 3, updated: start)
    #expect(away.expires == at(minutes: 30))
    #expect(away.shown(at: at(minutes: 29)) == away)
    #expect(away.shown(at: at(minutes: 30)) == nil)
    #expect(away.shown(at: at(minutes: 600)) == nil)
    // Worked out ahead of a clock since set back: not judged, not shown.
    #expect(away.shown(at: at(minutes: -10)) == nil)
    #expect(away.shown(at: at(minutes: -4)) == away)
    // The others are as true hours later as when written.
    for kind in [WatchComplicationState.Kind.ready, .arriving, .storageFull, .couldntSave] {
      let state = WatchComplicationState(kind: kind, title: "t", waiting: 1, updated: start)
      #expect(state.expires == nil)
      #expect(state.shown(at: at(minutes: 3 * 24 * 60)) == state)
      #expect(state.shown(at: at(minutes: -600)) == state)
    }
  }

  @Test func survivesTheAppGroupFile() throws {
    let saved = state(.arriving, waiting: 4, title: "Arriving from iPhone")
    #expect(WatchComplicationState.decode(try saved.encoded()) == saved)
    #expect(WatchComplicationState.decode(Data("{}".utf8)) == nil)
    #expect(WatchComplicationState(kind: .ready, title: "x", waiting: -2, updated: start).waiting == 0)
  }

  /// A burst of arriving songs redraws the count at most every five
  /// minutes; a change of state redraws at once.
  @Test func aBurstOfSongsDoesntRedrawPerSong() {
    var reload = WatchComplicationReload()
    #expect(reload.changed(to: state(.arriving, waiting: 10), now: at(minutes: 0)) == .now)
    // The same thing shown, at a later time: nothing to redraw.
    var same = state(.arriving, waiting: 10)
    same.updated = at(minutes: 1)
    #expect(reload.changed(to: same, now: at(minutes: 1)) == .none)
    // Nine songs arrive in the next four minutes: one deferred redraw.
    for left in (1...9).reversed() {
      #expect(
        reload.changed(to: state(.arriving, waiting: left), now: at(minutes: Double(10 - left) / 2))
          == .later(at(minutes: 5)))
    }
    #expect(reload.shown?.waiting == 10)
    // Asked again when that time comes: the count then is drawn.
    #expect(reload.changed(to: state(.arriving, waiting: 1), now: at(minutes: 5)) == .now)
    #expect(reload.shown?.waiting == 1)
    // The last song makes it ready: at once, however soon.
    #expect(
      reload.changed(to: state(.ready, waiting: 0, title: "Ready to play"), now: at(minutes: 5.5))
        == .now)
    // A problem shows at once too.
    #expect(
      reload.changed(to: state(.storageFull, waiting: 2, title: "Storage full"), now: at(minutes: 6))
        == .now)
  }

  @Test func aReloadFromAClockSetBackDoesntHoldTheNextOne() {
    var reload = WatchComplicationReload()
    #expect(reload.changed(to: state(.arriving, waiting: 5), now: at(minutes: 60)) == .now)
    // The clock went back an hour: the next count isn't held for an hour.
    #expect(reload.changed(to: state(.arriving, waiting: 4), now: at(minutes: 0)) == .now)
  }

  // MARK: The iPhone's pushes

  /// One push per burst of hand-offs, however many songs it hands over.
  @Test func oncePerBurst() {
    var pushes = WatchComplicationPushes()
    #expect(pushes.handed(now: at(minutes: 0), enabled: true, remaining: 50) == .send)
    pushes.sent(at: at(minutes: 0))
    // The rest of the burst: songs every few minutes for half an hour.
    for minute in stride(from: 3.0, through: 30, by: 3) {
      #expect(
        pushes.handed(now: at(minutes: minute), enabled: true, remaining: 49) == .alreadyThisBurst)
    }
    #expect(pushes.count(.send, now: at(minutes: 30)) == 1)
    // Nothing noted for those: the diagnostics count real skips only.
    #expect(pushes.events.count == 1)
    // A new burst after a quiet gap gets its own push.
    #expect(pushes.handed(now: at(minutes: 45), enabled: true, remaining: 49) == .send)
    pushes.sent(at: at(minutes: 45))
    #expect(pushes.lastSent == at(minutes: 45))
  }

  @Test func aNewBurstSoonAfterAPushWaitsForTheSpacing() {
    var pushes = WatchComplicationPushes()
    #expect(pushes.handed(now: at(minutes: 0), enabled: true, remaining: 50) == .send)
    pushes.sent(at: at(minutes: 0))
    // A new burst 12 minutes later: too soon after the last push.
    #expect(pushes.handed(now: at(minutes: 12), enabled: true, remaining: 49) == .spacing)
    // Later in that same burst, once the spacing has passed, it gets its push.
    #expect(pushes.handed(now: at(minutes: 21), enabled: true, remaining: 49) == .send)
    pushes.sent(at: at(minutes: 21))
    #expect(pushes.count(.spacing, now: at(minutes: 21)) == 1)
  }

  @Test func noPushWithoutTheComplicationOrBeyondTheReserve() {
    var pushes = WatchComplicationPushes()
    #expect(pushes.handed(now: at(minutes: 0), enabled: false, remaining: 50) == .disabled)
    #expect(pushes.enabled == false)
    // Still the same burst, now with the complication: it may push.
    #expect(pushes.handed(now: at(minutes: 1), enabled: true, remaining: 50) == .send)
    // A push that didn't go leaves the burst without one.
    #expect(pushes.handed(now: at(minutes: 2), enabled: true, remaining: 50) == .send)
    var low = WatchComplicationPushes()
    #expect(
      low.handed(now: at(minutes: 0), enabled: true, remaining: WatchComplicationPushes.reserve)
        == .reserve)
    #expect(
      low.handed(now: at(minutes: 1), enabled: true, remaining: WatchComplicationPushes.reserve + 1)
        == .send)
    #expect(low.count(.reserve, now: at(minutes: 1)) == 1)
  }

  /// At most one push per `spacing`, so never more than the allowance
  /// less the reserve, however busy the day.
  @Test func aBusyDayStaysInsideTheAllowance() {
    var pushes = WatchComplicationPushes()
    var remaining = 50
    for minute in stride(from: 0.0, to: 24 * 60, by: 11) {
      if pushes.handed(now: at(minutes: minute), enabled: true, remaining: remaining) == .send {
        pushes.sent(at: at(minutes: minute))
        remaining -= 1
      }
    }
    #expect(remaining == WatchComplicationPushes.reserve)
    #expect(pushes.count(.send, now: at(minutes: 24 * 60)) == 40)
  }

  @Test func aPushFromAClockSetBackIsIgnored() {
    var pushes = WatchComplicationPushes()
    pushes.sent(at: at(minutes: 120))
    // Two hours earlier by the clock, a new burst: not held by a push "in the future".
    #expect(pushes.handed(now: at(minutes: 0), enabled: true, remaining: 50) == .send)
    #expect(
      WatchComplicationPushes.decide(
        now: at(minutes: 0), enabled: true, remaining: 50, lastSent: at(minutes: 10),
        burstPushed: false) == .spacing)
  }

  /// With PigMusic open on Apple Watch the songs go straight to it: no push,
  /// and the burst keeps its push for when the app is closed.
  @Test func noPushWhileTheWatchAppIsOpen() {
    var pushes = WatchComplicationPushes()
    #expect(
      pushes.handed(now: at(minutes: 0), enabled: true, remaining: 50, watchAppOpen: true)
        == .watchAppOpen)
    #expect(pushes.handed(now: at(minutes: 3), enabled: true, remaining: 50) == .send)
    #expect(pushes.count(.watchAppOpen, now: at(minutes: 3)) == 1)
  }

  /// Many skips never hide the last push from the spacing rule.
  @Test func theLastPushOutlivesTheEventLog() {
    var pushes = WatchComplicationPushes()
    pushes.sent(at: at(minutes: 0))
    for step in 0..<(WatchComplicationPushes.capacity + 10) {
      _ = pushes.handed(
        now: at(minutes: 0.01 * Double(step)), enabled: true, remaining: 50, watchAppOpen: true)
    }
    #expect(pushes.count(.send, now: at(minutes: 6)) == 0)
    #expect(pushes.lastSent == at(minutes: 0))
    #expect(pushes.handed(now: at(minutes: 15), enabled: true, remaining: 50) == .spacing)
  }

  /// Codex round 1 on batch J: the same song retried every 30 minutes
  /// pushed every time. A retry of songs already announced is no new burst.
  @Test func retriesOfAnnouncedSongsDontPush() {
    var pushes = WatchComplicationPushes()
    #expect(pushes.handed(now: at(minutes: 0), songs: ["x"], enabled: true, remaining: 50) == .send)
    pushes.sent(at: at(minutes: 0), songs: ["x"])
    for step in 1...8 {
      #expect(
        pushes.handed(
          now: at(minutes: Double(step) * 30), songs: ["x"], enabled: true, remaining: 49)
          == .retry)
    }
    #expect(pushes.count(.send, now: at(minutes: 300)) == 1)
    #expect(pushes.count(.retry, now: at(minutes: 300)) == 8)
    // A retry starts no burst: a new song right after still pushes.
    #expect(
      pushes.handed(now: at(minutes: 301), songs: ["x", "y"], enabled: true, remaining: 49)
        == .send)
    // Announced songs are forgotten after a week.
    var later = WatchComplicationPushes()
    later.sent(at: at(minutes: 0), songs: ["x"])
    #expect(
      later.handed(now: at(minutes: 8 * 24 * 60), songs: ["x"], enabled: true, remaining: 50)
        == .send)
  }

  @Test func theAnnouncedSongsStayBounded() {
    var pushes = WatchComplicationPushes()
    let many = (0..<(WatchComplicationPushes.pushedCapacity + 50)).map { "s\($0)" }
    pushes.sent(at: at(minutes: 0), songs: Array(many.prefix(100)))
    pushes.sent(at: at(minutes: 1), songs: Array(many.dropFirst(100)))
    #expect(pushes.pushedSongs.count == WatchComplicationPushes.pushedCapacity)
    // The oldest went first.
    #expect(pushes.pushedSongs["s0"] == nil && pushes.pushedSongs["s2049"] != nil)
  }

  @Test func theWakeCarriesTheNewSongs() throws {
    let wake = CompanionWake(sentAt: start, songs: (0..<600).map { "s\($0)" })
    #expect(wake.songs?.count == CompanionWake.maxSongs)
    let decoded = CompanionWake.decode(try JSONEncoder().encode(wake))
    #expect(decoded == wake)
    // From a build before the songs were carried: no evidence.
    let old = Data(#"{"kind":"wake","sentAt":0}"#.utf8)
    #expect(CompanionWake.decode(old)?.songs == nil)
  }

  @Test func pushesAreSavedAndSummarised() throws {
    var pushes = WatchComplicationPushes()
    _ = pushes.handed(now: at(minutes: 0), enabled: false, remaining: 50)
    _ = pushes.handed(now: at(minutes: 30), enabled: true, remaining: 48)
    pushes.sent(at: at(minutes: 30))
    let saved = try JSONDecoder().decode(
      WatchComplicationPushes.self, from: JSONEncoder().encode(pushes))
    #expect(saved == pushes)
    let text = saved.summary(now: at(minutes: 40), enabled: true, remaining: 47)
    #expect(text.contains("PigMusic complication on the active Apple Watch face: yes."))
    #expect(text.contains("Pushes that wake PigMusic on Apple Watch: 1 in the last 7 days; last at "))
    #expect(text.contains("Pushes left today: 47 (10 are always kept)."))
    #expect(text.contains("Hand-offs with no push: 1 with no complication on the face, 0 to keep"))
    #expect(
      text.contains("0 with PigMusic open on Apple Watch, 0 retrying songs already announced."))
    // With no link, what WatchConnectivity last said.
    #expect(saved.summary(now: at(minutes: 40), enabled: nil, remaining: nil).contains("left today: 48"))
    #expect(
      WatchComplicationPushes().summary(enabled: nil, remaining: nil)
        .contains("active Apple Watch face: not known."))
  }

  @Test func theWakeMessageIsToldApartFromOthers() throws {
    let data = try JSONEncoder().encode(CompanionWake(sentAt: start))
    #expect(CompanionWake.decode(data)?.sentAt == start)
    #expect(CompanionWake.decode(Data(#"{"kind":"listen","sentAt":0}"#.utf8)) == nil)
    #expect(CompanionWake.decode(try JSONEncoder().encode(["message": "hi"])) == nil)
  }
}
