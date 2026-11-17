import CryptoKit
import Foundation
import PigCompanion
import PigDomain
import Testing

/// #219: Mark's export could not say how long songs took to reach the Watch,
/// nor whether they moved while the iPhone app was closed.
struct WatchDeliveryLogTests {
  private let start = Date(timeIntervalSince1970: 1_800_000_000)

  @Test func timesEachStepAndCountsArrivalsWhileThePhoneWasAway() {
    var log = WatchDeliveryLog()
    log.phone(foreground: true, at: start)
    log.sent(["a", "b", "c"], at: start)
    log.ready("a", at: start + 30)
    log.handed("a", at: start + 40)
    log.ready("b", at: start + 60)
    log.handed("b", at: start + 70)
    log.phone(foreground: false, at: start + 100)
    log.arrived("a", at: start + 90)  // Before the phone left.
    log.arrived("b", at: start + 400)  // While it was in the background.
    // A second send or report changes nothing already noted.
    log.sent(["a"], at: start + 500)
    log.arrived("a", at: start + 600)
    #expect(log.entries["a"]?.sent == start)
    #expect(log.entries["a"]?.arrived == start + 90)
    #expect(log.entries["a"]?.phoneForeground == true)
    #expect(log.entries["b"]?.phoneForeground == false)
    let summary = log.summary()
    #expect(summary.contains("3 songs sent in the last 30 days, 2 arrived, 1 still on their way."))
    // Median of 90 s and 400 s is 245 s; the longest is 400 s.
    #expect(summary.contains("Send to arrival: median 4 min 5 s, longest 6 min 40 s."))
    #expect(summary.contains("Handed to arrival on Apple Watch: median 3 min 10 s"))
    #expect(summary.contains("Arrived while the iPhone app was in the background: 1 of 2."))
  }

  @Test func songsTakenOffTheWatchAreForgottenAndTheLogSurvivesSaving() throws {
    var log = WatchDeliveryLog()
    log.sent(["a", "b"], at: start)
    log.retain(["b"])
    #expect(Set(log.entries.keys) == ["b"])
    log.arrived("b", at: start + 5)
    #expect(log.entries["b"]?.phoneForeground == nil)  // No transition noted yet.
    #expect(log.summary().contains("(1 unknown)"))
    let saved = try JSONDecoder().decode(
      WatchDeliveryLog.self, from: JSONEncoder().encode(log))
    #expect(saved == log)
  }

  @Test func theWatchSaysWhenEachSongArrived() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let clock = start + 1000
    let archive = try CompanionArchive(directory: root, now: { clock })
    let context = CompanionContext(revision: 1, account: "one")
    let bytes = Data("arrives".utf8)
    let item = CompanionSong(
      track: Track(id: "song", title: "Song", artist: "Artist"),
      checksum: SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined(),
      bytes: Int64(bytes.count))
    let manifest = CompanionManifest(context: context, songs: [item])
    try await archive.apply(context)
    try await archive.apply(manifest)
    let file = root.appendingPathComponent("incoming")
    try bytes.write(to: file)
    try await archive.accept(
      file, ticket: CompanionFile(context: context, songId: item.id, checksum: item.checksum))
    // Kept across a restart, carried by the compact acknowledgement.
    let reopened = try CompanionArchive(directory: root)
    let receipt = await reopened.receipt()
    #expect(receipt.arrivals == [item.id: clock])
    let acknowledgement = CompanionAcknowledgement(
      receipt: receipt, manifest: manifest, now: clock + 60)
    let decoded = try JSONDecoder().decode(
      CompanionAcknowledgement.self, from: JSONEncoder().encode(acknowledgement))
    #expect(try decoded.receipt(for: manifest).arrivals == [item.id: clock])
    // Older arrivals are left out, so the acknowledgement stays small.
    let late = CompanionAcknowledgement(receipt: receipt, manifest: manifest, now: clock + 3 * 86400)
    #expect(late.arrivals?.isEmpty == true)
  }
}
