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
  @Test func connectionStatusNeverGuessesNotInstalledBeforeActivation() {
    func status(
      _ activated: Bool, error: String? = nil, paired: Bool = false, installed: Bool = false,
      reachable: Bool = false
    ) -> WatchConnectionStatus {
      .init(
        supported: true, activated: activated, error: error, paired: paired, installed: installed,
        reachable: reachable)
    }
    #expect(status(false) == .checking)
    #expect(status(false, error: "denied") == .failed("denied"))
    #expect(status(true) == .notPaired)
    #expect(status(true, paired: true) == .notInstalled)
    #expect(status(true, paired: true, installed: true) == .away)
    #expect(status(true, paired: true, installed: true, reachable: true) == .nearby)
    #expect(
      WatchConnectionStatus(
        supported: false, activated: true, error: nil, paired: true, installed: true,
        reachable: true) == .unsupported)
  }
  @Test func readinessCountsOnlyConfirmedSongs() {
    let summary = WatchReadinessSummary(
      ready: 3, waiting: 1, failed: 0, seconds: 3 * 3600 + 25 * 60, bytes: 10)
    #expect(!summary.complete)
    #expect(summary.listening == "3 h 25 min")
    #expect(WatchReadinessSummary(ready: 1, waiting: 0, failed: 0, seconds: 59, bytes: 1).complete)
  }
}
