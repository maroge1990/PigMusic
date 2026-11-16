import Foundation
import PigCompanion
import PigDomain
import Testing

struct WatchDeliveryTests {
  private let context = CompanionContext(revision: 12, account: "a")
  private func song(_ id: String, bytes: Int64 = 10) -> CompanionSong {
    CompanionSong(track: Track(id: id, title: id, artist: "Artist"), checksum: "c-" + id, bytes: bytes)
  }

  @Test func sendsWhatTheWatchLacksWithinItsBudget() {
    let songs = [song("a"), song("b", bytes: 50), song("c"), song("d")]
    let receipt = CompanionReceipt(
      context: context, ready: ["a": "c-a"], usedBytes: 70, budgetBytes: 100)
    let plan = WatchDelivery.audio(
      songs: songs, receipt: receipt, inFlight: [], hasFile: { _ in true }, due: { _ in true })
    // "a" is there; "b" does not fit the 30 bytes left; "c" and "d" do.
    #expect(plan.send.map(\.id) == ["c", "d"])
    #expect(plan.budgetFull)
  }

  @Test func aSongAlreadyOnItsWayOrSentRecentlyWaitsButKeepsItsBudget() {
    let songs = [song("a"), song("b"), song("c"), song("d")]
    let receipt = CompanionReceipt(context: context, ready: [:], usedBytes: 0, budgetBytes: 30)
    let plan = WatchDelivery.audio(
      songs: songs, receipt: receipt, inFlight: ["a:c-a"], hasFile: { $0 != "c" },
      due: { $0 != "audio:b:c-b" })
    // a in flight, b not due, c without a file: none go, and each still
    // counts against the budget, so d does not fit.
    #expect(plan.send.isEmpty)
    #expect(plan.budgetFull)
  }

  @Test func aChangedSongIsSentAgain() {
    let receipt = CompanionReceipt(
      context: context, ready: ["a": "old"], usedBytes: 0, budgetBytes: 100)
    let plan = WatchDelivery.audio(
      songs: [song("a")], receipt: receipt, inFlight: [], hasFile: { _ in true },
      due: { _ in true })
    #expect(plan.send.map(\.id) == ["a"])
    #expect(!plan.budgetFull)
  }

  /// #219: audio waited for the Watch to confirm each manifest, a round trip
  /// that needed both apps running. Now it goes at once: songs the Watch can
  /// file on arrival all go; songs it must hold for their manifest, from the
  /// published one or ahead of it, go a bounded number at a time.
  @Test func audioGoesWithoutWaitingForTheWatchToConfirmItsManifest() {
    let committed = (0..<30).map { song("c\($0)") }
    let fresh = (0..<30).map { song("n\($0)") }
    let later = [song("x"), song("y")]
    // The Watch's last receipt is for an older manifest; it holds c0.
    let receipt = CompanionReceipt(
      context: CompanionContext(revision: 3, account: "a"), ready: ["c0": "c-c0"],
      usedBytes: 10, budgetBytes: 10_000)
    let plan = WatchDelivery.audio(
      songs: committed + fresh, ahead: later, receipt: receipt,
      committed: Set(committed.map(WatchDelivery.identity)), inFlight: ["n0:c-n0"],
      hasFile: { _ in true }, due: { $0 != "audio:n1:c-n1" })
    // Every committed song the Watch lacks goes, however many.
    #expect(plan.send.filter { $0.id.hasPrefix("c") }.count == 29)
    // n0 (in flight) and n1 (sent recently) may wait on the Watch already:
    // 22 more fresh songs make the 24 it may hold, and none ahead fit.
    #expect(plan.send.filter { $0.id.hasPrefix("n") }.map(\.id) == (2..<24).map { "n\($0)" })
    #expect(plan.ahead.isEmpty)
    #expect(!plan.budgetFull)
    // Once the Watch confirmed the fresh songs too, those ahead go for the next version.
    let confirmed = WatchDelivery.audio(
      songs: committed + fresh, ahead: later, receipt: receipt,
      committed: Set((committed + fresh).map(WatchDelivery.identity)), inFlight: [],
      hasFile: { _ in true }, due: { _ in true })
    #expect(confirmed.send.count == 59)
    #expect(confirmed.ahead.map(\.id) == ["x", "y"])
  }

  @Test func heldSongsAreBoundedBySizeToo() {
    let big = (0..<4).map { song("b\($0)", bytes: 100 * 1024 * 1024) }
    let receipt = CompanionReceipt(
      context: context, ready: [:], usedBytes: 0, budgetBytes: 8 * 1024 * 1024 * 1024)
    let plan = WatchDelivery.audio(
      songs: [], ahead: big, receipt: receipt, committed: [], inFlight: [],
      hasFile: { _ in true }, due: { _ in true })
    #expect(plan.ahead.map(\.id) == ["b0", "b1"])
  }

  @Test func unclaimedInboxFilesExpire() {
    let inbox = URL(fileURLWithPath: "/tmp/inbox")
    let now = Date(timeIntervalSince1970: 1_800_000_000)
    let old = inbox.appendingPathComponent("old.transfer")
    let fresh = inbox.appendingPathComponent("fresh.transfer")
    let orphan = inbox.appendingPathComponent("orphan.json")
    let files = [
      old, inbox.appendingPathComponent("old.json"), fresh,
      inbox.appendingPathComponent("fresh.json"), orphan,
    ]
    let created: (URL) -> Date? = { file in
      file.lastPathComponent.hasPrefix("fresh") ? now - 3600 : now - 3 * 86400
    }
    let gone = CompanionInbox.expired(files, now: now, created: created)
    #expect(Set(gone) == [old, inbox.appendingPathComponent("old.json"), orphan])
  }

  @Test func oldManifestsGoUnlessATransferStillNeedsThem() {
    let folder = URL(fileURLWithPath: "/tmp/watch")
    func manifest(_ revision: Int) -> URL {
      folder.appendingPathComponent("watch-manifest-\(revision).json")
    }
    let current = manifest(12)
    let files = [
      manifest(5), manifest(7), manifest(8), manifest(9), manifest(11), current,
      folder.appendingPathComponent("watch-manifest.json"),
      folder.appendingPathComponent("watch-receipt.json"),
    ]
    let stale = WatchDelivery.staleManifests(
      files, current: current, revision: 12, retained: [7])
    #expect(stale == [manifest(5), manifest(8)])
  }

  /// D-109 (TestFlight 1012): a song waiting for the Watch couldn't be
  /// paused. Paused, its audio isn't handed to WatchConnectivity, from the
  /// published manifest or ahead of it, and leaves the budget to the others;
  /// a queued transfer of it is cancelled. Resumed, it goes as normal.
  @Test func aPausedSongsAudioIsHeldBackUntilResumed() {
    let songs = [song("a"), song("b"), song("c", bytes: 30)]
    let receipt = CompanionReceipt(context: context, ready: [:], usedBytes: 0, budgetBytes: 40)
    let paused = WatchDelivery.audio(
      songs: songs, ahead: [song("d")], receipt: receipt, inFlight: [], paused: ["c", "d"],
      hasFile: { _ in true }, due: { _ in true })
    #expect(paused.send.map(\.id) == ["a", "b"])
    #expect(paused.ahead.isEmpty)
    #expect(!paused.budgetFull)
    let resumed = WatchDelivery.audio(
      songs: songs, ahead: [song("d")], receipt: receipt, inFlight: [], paused: ["d"],
      hasFile: { _ in true }, due: { _ in true })
    #expect(resumed.send.map(\.id) == ["a", "b"])
    // c no longer fits beside a and b: it waits for room, as any song does.
    #expect(resumed.budgetFull)
    let room = CompanionReceipt(context: context, ready: [:], usedBytes: 0, budgetBytes: 100)
    let all = WatchDelivery.audio(
      songs: songs, ahead: [song("d")], receipt: room, inFlight: [], hasFile: { _ in true },
      due: { _ in true })
    #expect(all.send.map(\.id) == ["a", "b", "c"])
    #expect(all.ahead.map(\.id) == ["d"])
    // Only a paused song's audio is cancelled; its manifest transfer stays.
    func audio(_ id: String) -> CompanionEnvelope {
      CompanionEnvelope(
        kind: "audio", context: context,
        file: CompanionFile(context: context, songId: id, checksum: "c-" + id))
    }
    #expect(WatchDelivery.cancels(audio("c"), paused: ["c"]))
    #expect(!WatchDelivery.cancels(audio("a"), paused: ["c"]))
    #expect(!WatchDelivery.cancels(CompanionEnvelope(kind: "manifest", context: context), paused: ["c"]))
    // Resuming forgets the cancelled send, so the audio goes at once.
    var log = CompanionSendLog()
    log.mark("audio:c:c-c")
    #expect(!log.due("audio:c:c-c"))
    log.forget("audio:c:c-c")
    #expect(log.due("audio:c:c-c"))
  }
}
