import Foundation
import PigCompanion
import PigDomain
import Testing

struct WatchPlanningTests {
  typealias Plan = WatchPlaylistPlan
  /// The context in use, delivered again, is only confirmed (25 Oct: each
  /// re-application drew another copy from the phone, hundreds a minute).
  @Test func aRepeatedContextIsConfirmedNotAppliedAgain() {
    let current = CompanionContext(revision: 24, account: "a")
    #expect(CompanionContext(revision: 24, account: "a").arrival(over: current) == .repeated)
    #expect(CompanionContext(revision: 25, account: "a").arrival(over: current) == .apply)
    #expect(CompanionContext(revision: 25, account: "b").arrival(over: current) == .apply)
    #expect(CompanionContext(revision: 23, account: "a").arrival(over: current) == .stale)
    #expect(CompanionContext(revision: 24, account: "b").arrival(over: current) == .stale)
    var future = CompanionContext(revision: 25, account: "a")
    future.version = 2
    #expect(future.arrival(over: current) == .stale)
  }
  /// A manifest read while a newer context was arriving is not admitted,
  /// even though the archive still reports its own context (RV25).
  @Test func aManifestIsNotAdmittedOnceANewerContextIsAnnounced() {
    var fence = CompanionContextFence()
    let a = CompanionContext(revision: 24, account: "a")
    fence.note(a)
    #expect(fence.admits(a))
    fence.note(CompanionContext(revision: 25, account: "b"))
    #expect(!fence.admits(a))
    #expect(fence.admits(CompanionContext(revision: 25, account: "b")))
    // An older or unknown-version context never moves the fence back or on.
    fence.note(CompanionContext(revision: 3, account: "a"))
    var future = CompanionContext(revision: 99, account: "a")
    future.version = 2
    fence.note(future)
    #expect(fence.newest == 25)
  }
  @Test func emptiedPlaylistStaysFollowedAndRefills() {
    // Sent while it had songs, then emptied: still followed, songs released.
    var plan = Plan.make(
      subscriptions: ["run": "Run"], exclusions: [:],
      sent: ["run": .init(name: "Run", songIds: ["a", "b"])],
      playlists: ["run": .init(name: "Run", songIds: [])], onDevice: ["a", "b"])
    #expect(plan.release == [.init(playlistId: "run", name: "Run", songIds: ["a", "b"])])
    #expect(plan.send.isEmpty)
    #expect(plan.subscriptions["run"] == "Run")
    // Songs added later are sent automatically.
    plan = Plan.make(
      subscriptions: plan.subscriptions, exclusions: [:], sent: [:],
      playlists: ["run": .init(name: "Run", songIds: ["c"])], onDevice: [])
    #expect(plan.send == [.init(playlistId: "run", name: "Run", songIds: ["c"])])
  }
  @Test func deletedPlaylistLeavesAndIsUnfollowedEvenWhenItWasTheLast() {
    let plan = Plan.make(
      subscriptions: ["run": "Run"], exclusions: ["run": ["x"]],
      sent: ["run": .init(name: "Run", songIds: ["a"])], playlists: [:], onDevice: ["a"])
    #expect(plan.release == [.init(playlistId: "run", name: "Run", songIds: ["a"])])
    #expect(plan.subscriptions.isEmpty)
    #expect(plan.exclusions.isEmpty)
  }
  @Test func removedSongsAreNotSentBackUntilTheyLeaveThePlaylist() {
    var plan = Plan.make(
      subscriptions: ["run": "Run"], exclusions: ["run": ["b"]],
      sent: ["run": .init(name: "Run", songIds: ["a"])],
      playlists: ["run": .init(name: "Run", songIds: ["a", "b"])], onDevice: ["a"])
    #expect(plan.send.isEmpty)
    #expect(plan.release.isEmpty)
    #expect(plan.exclusions["run"] == ["b"])
    // Once "b" leaves the playlist its exclusion ends.
    plan = Plan.make(
      subscriptions: ["run": "Run"], exclusions: plan.exclusions,
      sent: ["run": .init(name: "Run", songIds: ["a"])],
      playlists: ["run": .init(name: "Run", songIds: ["a"])], onDevice: ["a"])
    #expect(plan.exclusions.isEmpty)
  }
  @Test func renameResendsToCarryTheNameAndTheLimitHolds() {
    let plan = Plan.make(
      subscriptions: [:], exclusions: [:],
      sent: ["run": .init(name: "Run", songIds: ["a"])],
      playlists: ["run": .init(name: "Long run", songIds: ["a", "b", "c"])],
      onDevice: ["a"], limit: 2)
    // "a" is already on the Watch; only one new song fits the limit of two.
    #expect(plan.send == [.init(playlistId: "run", name: "Long run", songIds: ["a", "b"])])
    #expect(plan.subscriptions["run"] == "Long run")
  }
  @Test func unloadedPlaylistIsLeftAlone() {
    let plan = Plan.make(
      subscriptions: ["run": "Run"], exclusions: [:],
      sent: ["run": .init(name: "Run", songIds: ["a"])],
      playlists: ["run": .init(name: "Run", songIds: nil)], onDevice: ["a"])
    #expect(plan.release.isEmpty && plan.send.isEmpty)
  }
  @Test func queueKeepsPlayingOnlyWhenEveryQueuedSongIsUnchanged() {
    let context = CompanionContext(revision: 1, account: "one")
    func song(_ id: String, _ sum: String) -> CompanionSong {
      CompanionSong(
        track: Track(id: id, title: id, artist: "A"), checksum: String(repeating: sum, count: 64),
        bytes: 1)
    }
    let old = CompanionManifest(context: context, songs: [song("a", "1"), song("b", "2")])
    let added = CompanionManifest(
      context: context, songs: [song("a", "1"), song("b", "2"), song("c", "3")])
    let changed = CompanionManifest(context: context, songs: [song("a", "9"), song("b", "2")])
    let removed = CompanionManifest(context: context, songs: [song("b", "2")])
    #expect(added.keepsPlaying(["a", "b"], replacing: old))
    #expect(!changed.keepsPlaying(["a"], replacing: old))
    #expect(!removed.keepsPlaying(["a", "b"], replacing: old))
    #expect(removed.keepsPlaying([], replacing: old))
  }
  /// D-105 (#208): the Watch Downloads page says nothing unless Mark must act.
  @Test func theWatchPageSpeaksOnlyWhenMarkMustAct() {
    let now = Date(timeIntervalSince1970: 1_800_000_000)
    func attention(
      activated: Bool = true, error: String? = nil, missing: TimeInterval? = nil,
      waited: TimeInterval? = nil
    ) -> WatchAttention? {
      WatchAttention(
        activated: activated, error: error, missingSince: missing.map { now - $0 },
        oldestWait: waited.map { now - $0 }, now: now)
    }
    // All well, or settling: nothing.
    #expect(attention() == nil)
    #expect(attention(activated: false) == nil)
    #expect(attention(activated: false, missing: 600) == nil)
    #expect(attention(missing: 119) == nil)
    #expect(attention(waited: 29 * 60 + 59) == nil)
    // Each message once its threshold passes.
    #expect(attention(missing: 120) == .notInstalled)
    #expect(attention(waited: 30 * 60) == .openToFinish)
    #expect(attention(activated: false, error: "denied") == .unreachable)
    // Precedence: unreachable, then not installed, then open to finish.
    #expect(attention(error: "failed", missing: 600, waited: 3600) == .unreachable)
    #expect(attention(missing: 600, waited: 3600) == .notInstalled)
    #expect(attention(missing: 60, waited: 3600) == .openToFinish)
    #expect(WatchAttention.unreachable.text == "Can't reach your Watch")
    #expect(WatchAttention.notInstalled.text == "PigMusic isn't on your Watch")
    #expect(WatchAttention.openToFinish.text == "Open PigMusic on your Watch to finish")
  }
  @Test func theWatchAppCountsAsMissingOnlyOnceActivationSettled() {
    let start = Date(timeIntervalSince1970: 1_800_000_000)
    func missing(
      _ previous: Date?, activated: Bool = true, paired: Bool = true, installed: Bool = false,
      at offset: TimeInterval = 0
    ) -> Date? {
      WatchAttention.missing(
        since: previous, activated: activated, paired: paired, installed: installed,
        now: start + offset)
    }
    #expect(missing(nil, activated: false) == nil)
    #expect(missing(nil, paired: false) == nil)
    #expect(missing(nil) == start)
    // Kept while still missing, so the 2 minutes count from the first report.
    #expect(missing(start, at: 90) == start)
    // An update's moment of "not installed" ends: the clock starts again.
    #expect(missing(start, installed: true, at: 90) == nil)
    #expect(missing(start, activated: false, at: 90) == nil)
  }
  @Test func songsWaitFromTheirCopyBeingReady() {
    let start = Date(timeIntervalSince1970: 1_800_000_000)
    var log = WatchDeliveryLog()
    log.sent(["a", "b"], at: start)
    log.ready("a", at: start + 600)
    #expect(WatchAttention.oldestWait(["a"], log: log) == start + 600)
    #expect(WatchAttention.oldestWait(["a", "b"], log: log) == start)
    #expect(WatchAttention.oldestWait(["unknown"], log: log) == nil)
    #expect(WatchAttention.oldestWait([String](), log: log) == nil)
  }
  @Test func readinessCountsOnlyConfirmedSongs() {
    let summary = WatchReadinessSummary(
      ready: 3, waiting: 1, failed: 0, seconds: 3 * 3600 + 25 * 60, bytes: 10)
    #expect(!summary.complete)
    #expect(summary.listening == "3 h 25 min")
    #expect(WatchReadinessSummary(ready: 1, waiting: 0, failed: 0, seconds: 59, bytes: 1).complete)
  }
}

private actor Latch {
  var open = false
  var waiting = false
  func wait() async {
    waiting = true
    while !open { await Task.yield() }
  }
  func release() { open = true }
}

/// Codex round 2 on batch 13 (#209): Remove all while a playlist
/// reconciliation is suspended reading a song must not be undone by it.
@MainActor @Test func aReconciliationOvertakenByRemoveAllRestoresNothing() async {
  let plan = WatchPlaylistPlan.make(
    subscriptions: ["p": "Run"], exclusions: [:], sent: [:],
    playlists: ["p": .init(name: "Run", songIds: ["a"])], onDevice: [])
  #expect(plan.send == [.init(playlistId: "p", name: "Run", songIds: ["a"])])
  let latch = Latch()
  var generation = 0
  var enqueued: [[String]] = []
  let run = Task { @MainActor in
    let started = generation
    return await plan.apply(
      isCurrent: { generation == started },
      release: { _, _ in },
      lookup: { id in
        await latch.wait()
        return Track(id: id, title: id, artist: "A")
      },
      enqueue: { tracks, _ in enqueued.append(tracks.map(\.id)) })
  }
  while !(await latch.waiting) { await Task.yield() }
  generation += 1  // Remove all.
  await latch.release()
  let finished = await run.value
  #expect(!finished)  // The caller keeps its subscriptions as Remove all left them.
  #expect(enqueued.isEmpty)
  // Undisturbed, the plan is applied.
  let done = await plan.apply(
    isCurrent: { true }, release: { _, _ in },
    lookup: { Track(id: $0, title: $0, artist: "A") },
    enqueue: { tracks, _ in enqueued.append(tracks.map(\.id)) })
  #expect(done && enqueued == [["a"]])
}
