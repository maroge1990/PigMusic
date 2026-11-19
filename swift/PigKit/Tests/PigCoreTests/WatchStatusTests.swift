import Foundation
import PigCompanion
import Testing

/// The Watch's Storage & sync and home status (D-120, #252).
struct WatchStatusTests {
  private let gigabyte = WatchStorageFigures.gigabyte
  private func song(_ id: String, bytes: Int64 = 5_000_000, seconds: Double = 200) -> WatchSongState
  {
    WatchSongState(id: id, checksum: "sum-" + id, bytes: bytes, seconds: seconds)
  }

  /// One definition of ready: a file stored under another checksum (the
  /// iPhone changed the song) is not ready, though the receipt names it.
  @Test func readyMeansTheListedFileIsStored() {
    let songs = [song("a"), song("b"), song("c")]
    let stored = ["a": "sum-a", "b": "old-b"]
    let status = WatchLibraryStatus(
      songs: songs, stored: stored, usedBytes: 10_000_000, limitBytes: gigabyte)
    #expect(status.ready == 1)
    #expect(status.waiting == 2)
    #expect(status.readySeconds == 200)
    #expect(status.count == "1 of 3 songs on Apple Watch")
    #expect(!WatchSongState.isReady(id: "b", checksum: "sum-b", stored: stored))
    #expect(!WatchSongState.isReady(id: "a", checksum: "sum-a", stored: nil))
  }

  @Test func everySongThereIsReadyForARun() {
    let status = WatchLibraryStatus(
      songs: [song("a"), song("b")], stored: ["a": "sum-a", "b": "sum-b"], usedBytes: 1,
      limitBytes: gigabyte, iPhoneReachable: false)
    #expect(status.headline == .ready)
    #expect(status.title == "Ready for your run")
    #expect(status.count == "2 songs")
    #expect(status.advice == nil)
    // A newer library is announced and not here yet.
    #expect(
      WatchLibraryStatus(
        songs: [song("a")], stored: ["a": "sum-a"], usedBytes: 1, limitBytes: gigabyte,
        manifestApplied: false
      ).headline == .arriving)
  }

  /// "Almost ready" is split into what is really happening (#252).
  @Test func notReadyNamesItsCause() {
    let songs = [song("a"), song("b", bytes: 300_000_000)]
    let stored = ["a": "sum-a"]
    // Room left, iPhone nearby: on the way.
    #expect(
      WatchLibraryStatus(
        songs: songs, stored: stored, usedBytes: 0, limitBytes: gigabyte, iPhoneReachable: true
      ).headline == .arriving)
    // Not known whether the iPhone is near: still only "on the way".
    #expect(
      WatchLibraryStatus(songs: songs, stored: stored, usedBytes: 0, limitBytes: gigabyte)
        .headline == .arriving)
    // iPhone out of reach.
    let away = WatchLibraryStatus(
      songs: songs, stored: stored, usedBytes: 0, limitBytes: gigabyte, iPhoneReachable: false)
    #expect(away.headline == .iPhoneAway)
    #expect(away.title == "Can't reach your iPhone")
    // The waiting song doesn't fit under the Storage limit.
    let full = WatchLibraryStatus(
      songs: songs, stored: stored, usedBytes: gigabyte - 100_000_000, limitBytes: gigabyte,
      iPhoneReachable: false)
    #expect(full.headline == .storageFull)
    #expect(full.title == "Storage full")
    #expect(full.advice?.contains("Storage limit in Storage") == true)
    #expect(full.storageAdvice?.contains("Storage limit below") == true)
    #expect(away.storageAdvice == away.advice)
    #expect(full.marks == ["b": .storageFull])
    // Apple Watch itself is full: a larger limit won't help.
    let device = WatchLibraryStatus(
      songs: songs, stored: stored, usedBytes: 0, limitBytes: gigabyte,
      problems: ["b": .deviceFull])
    #expect(device.headline == .storageFull)
    #expect(device.storageAdvice?.contains("Storage limit") == false)
  }

  /// Codex round 1: a song turned away for the Storage limit stayed "Storage
  /// full" after a larger limit or a removal made room for it.
  @Test func overTheLimitClearsOnceTheSongFits() {
    let songs = [song("a"), song("b", bytes: 300_000_000)]
    let stored = ["a": "sum-a"]
    let tight = WatchLibraryStatus(
      songs: songs, stored: stored, usedBytes: gigabyte - 100_000_000, limitBytes: gigabyte,
      problems: ["b": .overLimit], iPhoneReachable: true)
    #expect(tight.headline == .storageFull)
    let roomy = WatchLibraryStatus(
      songs: songs, stored: stored, usedBytes: gigabyte - 100_000_000, limitBytes: 2 * gigabyte,
      problems: ["b": .overLimit], iPhoneReachable: true)
    #expect(roomy.headline == .arriving)
    #expect(roomy.marks.isEmpty)
  }

  /// No hard-coded "failed: 0": songs the Watch couldn't save are counted.
  @Test func songsTheWatchCouldntSaveAreCounted() {
    let status = WatchLibraryStatus(
      songs: [song("a"), song("b"), song("c")], stored: ["a": "sum-a"], usedBytes: 0,
      limitBytes: gigabyte, problems: ["b": .unreadable, "c": .unreadable, "a": .unreadable],
      iPhoneReachable: true)
    // "a" was saved since, so it no longer counts.
    #expect(status.couldntSave == 2)
    #expect(status.headline == .couldntSave(2))
    #expect(status.title == "Couldn't save 2 songs")
  }

  /// Songs whose removal is on its way to the iPhone are in no count.
  @Test func songsBeingRemovedAreLeftOut() {
    let status = WatchLibraryStatus(
      songs: [song("a"), song("b")], stored: ["a": "sum-a"], usedBytes: 0, limitBytes: gigabyte,
      removing: ["b"])
    #expect(status.total == 1)
    #expect(status.headline == .ready)
  }

  /// "1.2 GB of 2 GB": counted in 1,024s, so the limit reads as the picker
  /// sets it (a 2 GB limit is not "2.15 GB").
  @Test func storageFiguresReadAsTheLimitIsSet() {
    let figures = WatchStorageFigures(
      usedBytes: Int64(1.2 * Double(gigabyte)), limitBytes: 2 * gigabyte)
    #expect(figures.used == "1.2 GB")
    #expect(figures.limit == "of 2 GB")
    #expect(figures.percent == "60%")
    #expect(figures.limitGigabytes == 2)
    #expect(WatchStorageFigures(usedBytes: 4_718_592, limitBytes: gigabyte).used == "5 MB")
    #expect(WatchStorageFigures(usedBytes: 0, limitBytes: gigabyte).percent == "0%")
    #expect(WatchStorageFigures(usedBytes: 3 * gigabyte, limitBytes: gigabyte).fraction == 1)
    #expect(WatchStorageFigures.size(gigabyte) == "1 GB")
  }

  @Test func controlsShowTimeGoneAndTimeLeft() {
    #expect(WatchClock.elapsed(102) == "1:42")
    #expect(WatchClock.remaining(elapsed: 102, duration: 271) == "\u{2212}2:49")
    #expect(WatchClock.elapsed(.nan) == "0:00")
    #expect(WatchClock.remaining(elapsed: 300, duration: 271) == "\u{2212}0:00")
    #expect(WatchClock.elapsed(3_725) == "1:02:05")
  }

  /// Removing a song that isn't playing leaves playback and the queue
  /// alone (#252: every removal stopped playback and cleared the queue).
  @Test func removingAnotherSongKeepsPlaying() {
    let queue = ["a", "b", "c", "d"]
    #expect(WatchQueueRemoval.plan(queue: queue, current: 1, removing: ["x"]) == .untouched)
    #expect(
      WatchQueueRemoval.plan(queue: queue, current: 1, removing: ["d"])
        == .upcoming(keep: [0, 1, 2]))
    // An earlier song goes: the same song plays on, one place up.
    #expect(
      WatchQueueRemoval.plan(queue: queue, current: 2, removing: ["a"])
        == .moved(keep: [1, 2, 3], index: 1))
  }

  /// Removing the playing song moves on to the next.
  @Test func removingThePlayingSongPlaysTheNext() {
    let queue = ["a", "b", "c", "b"]
    #expect(
      WatchQueueRemoval.plan(queue: queue, current: 1, removing: ["b"])
        == .advance(keep: [0, 2], index: 1))
    #expect(WatchQueueRemoval.plan(queue: queue, current: 3, removing: ["b"]) == .stop)
    #expect(
      WatchQueueRemoval.plan(queue: queue, current: 3, removing: ["b"], repeatAll: true)
        == .advance(keep: [0, 2], index: 0))
    #expect(WatchQueueRemoval.plan(queue: ["a"], current: 0, removing: ["a"]) == .stop)
    #expect(WatchQueueRemoval.plan(queue: [], current: 0, removing: ["a"]) == .untouched)
  }

  /// Codex round 1: a removal committed while play() was still building its
  /// queue was missed, and the removed song was queued anyway.
  @Test func aQueueBeingBuiltDropsCommittedRemovals() {
    let queue = ["a", "b", "c", "d"]
    let kept = WatchQueueRemoval.reconcile(queue, start: 2, removed: ["b"])
    #expect(kept?.keep == [0, 2, 3])
    #expect(kept?.start == 1)
    #expect(kept?.startKept == true)
    // The chosen song went: the next one, from its start.
    let next = WatchQueueRemoval.reconcile(queue, start: 1, removed: ["b"])
    #expect(next?.start == 1)
    #expect(next?.startKept == false)
    #expect(WatchQueueRemoval.reconcile(queue, start: 3, removed: ["d"])?.start == 2)
    #expect(WatchQueueRemoval.reconcile(["a"], start: 0, removed: ["a"]) == nil)
    #expect(WatchQueueRemoval.reconcile(queue, start: 0, removed: [])?.keep == [0, 1, 2, 3])
  }

  /// A damaged saved library says so until the iPhone's copy arrives.
  @Test func aDamagedLibrarySaysItIsBeingFetchedAgain() {
    #expect(WatchRecovery(damagedFiles: []).text == nil)
    var recovery = WatchRecovery(damagedFiles: ["manifest.json"])
    #expect(recovery.text == "Couldn't read your saved music. Getting it again from iPhone.")
    recovery.libraryArrived()
    #expect(recovery.text == nil)
  }

  /// Albums file like artists: "abbey road" before "Tusk", "The Wall" under W.
  @Test func listsIgnoreCaseAndALeadingThe() {
    let names = ["Tusk", "abbey road", "The Wall", "Blue", "the Bends"]
    let sorted = names.sorted { WatchListOrder.precedes(($0, "", $0), ($1, "", $1)) }
    #expect(sorted == ["abbey road", "the Bends", "Blue", "Tusk", "The Wall"])
  }
}
