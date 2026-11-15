import CryptoKit
import Foundation
import PigCompanion
import PigDomain
import Testing

/// #221: before the Watch applies a new manifest its acknowledgement counts
/// positions in the manifest before, while the phone reads them in the new one.
struct WatchReceiptTests {
  private func song(_ id: String) -> (CompanionSong, Data) {
    let bytes = Data("audio \(id)".utf8)
    let item = CompanionSong(
      track: Track(id: id, title: id, artist: "Artist"),
      checksum: SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined(),
      bytes: Int64(bytes.count))
    return (item, bytes)
  }

  @Test func anAcknowledgementOfTheManifestBeforeMarksNoNewSongReady() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let archive = try CompanionArchive(directory: root)
    let (a, aBytes) = song("a")
    let (b, _) = song("b")
    let (c, _) = song("c")
    let first = CompanionContext(revision: 1, account: "one")
    let old = CompanionManifest(context: first, songs: [a, b])
    try await archive.apply(first)
    try await archive.apply(old)
    let file = root.appendingPathComponent("incoming")
    try aBytes.write(to: file)
    try await archive.accept(
      file, ticket: CompanionFile(context: first, songId: a.id, checksum: a.checksum))
    let applied = try CompanionAcknowledgement(
      receipt: await archive.receipt(), manifest: await archive.snapshot()
    ).receipt(for: old)
    #expect(applied.manifestApplied)
    #expect(applied.ready == [a.id: a.checksum])

    // The phone sends a new library, in another order; the Watch has its
    // context but not yet its manifest, and answers against the old one.
    let second = CompanionContext(revision: 2, account: "one")
    let new = CompanionManifest(context: second, songs: [c, b, a])
    try await archive.apply(second)
    let early = CompanionAcknowledgement(
      receipt: await archive.receipt(), manifest: await archive.snapshot())
    #expect(!early.manifestApplied)
    #expect(early.positions == [0])  // Song a, in the old manifest.

    // Read against the new manifest, position 0 would be song c.
    let shown = try early.receipt(for: new, lastApplied: applied)
    #expect(shown.ready[c.id] == nil)
    #expect(!shown.manifestApplied)
    // The songs shown ready stay those the Watch last confirmed applying.
    #expect(shown.ready == [a.id: a.checksum])
    #expect(shown.arrivals == nil)
    // Without one, nothing is ready.
    #expect(try early.receipt(for: new).ready.isEmpty)
    // Another account's confirmation is never shown.
    var stranger = applied
    stranger.context = CompanionContext(revision: 1, account: "two")
    #expect(try early.receipt(for: new, lastApplied: stranger).ready.isEmpty)

    // Once applied, its positions count the new manifest.
    try await archive.apply(new)
    let later = try CompanionAcknowledgement(
      receipt: await archive.receipt(), manifest: await archive.snapshot()
    ).receipt(for: new, lastApplied: applied)
    #expect(later.manifestApplied)
    #expect(later.ready == [a.id: a.checksum])
  }
}
