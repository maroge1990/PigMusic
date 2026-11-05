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
}
