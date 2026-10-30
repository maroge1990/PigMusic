import CryptoKit
import Foundation
import PigCompanion
import PigDomain
import Testing

struct CompanionArchiveTests {
  private func folder() throws -> URL {
    let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
    return url
  }
  private func song(_ id: String, bytes: Data) -> CompanionSong {
    CompanionSong(
      track: Track(id: id, title: id, artist: "Artist"),
      checksum: SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined(),
      bytes: Int64(bytes.count))
  }
  @Test func independentDownloadsSurviveRestartAndRejectCorruption() async throws {
    let root = try folder()
    defer { try? FileManager.default.removeItem(at: root) }
    let archive = try CompanionArchive(directory: root.appendingPathComponent("Music"))
    let context = CompanionContext(revision: 1, account: "one")
    let bytes = Data("verified AAC fixture".utf8)
    let item = song("track", bytes: bytes)
    try await archive.apply(context)
    try await archive.apply(CompanionManifest(context: context, songs: [item]))
    let file = root.appendingPathComponent("incoming")
    try bytes.write(to: file)
    try await archive.accept(
      file, ticket: CompanionFile(context: context, songId: item.id, checksum: item.checksum))
    let recovered = try CompanionArchive(directory: root.appendingPathComponent("Music"))
    #expect(await recovered.receipt().ready[item.id] == item.checksum)
    let saved = try #require(await recovered.localFile(id: item.id))
    #expect(try Data(contentsOf: saved) == bytes)
    try Data(repeating: 0, count: bytes.count).write(to: file)
    await #expect(throws: (any Error).self) {
      try await recovered.accept(
        file, ticket: CompanionFile(context: context, songId: item.id, checksum: item.checksum))
    }
  }
  @Test func lateFilesCannotRestoreRemovedMusicOrAnOldAccount() async throws {
    let root = try folder()
    defer { try? FileManager.default.removeItem(at: root) }
    let archive = try CompanionArchive(directory: root.appendingPathComponent("Music"))
    let first = CompanionContext(revision: 1, account: "one")
    let next = CompanionContext(revision: 2, account: "one")
    let bytes = Data("audio".utf8)
    let item = song("track", bytes: bytes)
    try await archive.apply(first)
    try await archive.apply(CompanionManifest(context: first, songs: [item]))
    let file = root.appendingPathComponent("incoming")
    try bytes.write(to: file)
    try await archive.accept(
      file, ticket: CompanionFile(context: first, songId: item.id, checksum: item.checksum))
    try await archive.apply(next)
    // The committed library keeps playing until the new revision's manifest arrives.
    #expect(await archive.localFile(id: item.id) != nil)
    #expect(await archive.receipt().manifestApplied == false)
    try await archive.apply(CompanionManifest(context: next, songs: []))
    #expect(await archive.localFile(id: item.id) == nil)
    try bytes.write(to: file)
    await #expect(throws: (any Error).self) {
      try await archive.accept(
        file, ticket: CompanionFile(context: first, songId: item.id, checksum: item.checksum))
    }
    let other = CompanionContext(revision: 3, account: "two")
    try await archive.apply(other)
    try await archive.apply(CompanionManifest(context: other, songs: [item]))
    await #expect(throws: (any Error).self) {
      try await archive.accept(
        file, ticket: CompanionFile(context: first, songId: item.id, checksum: item.checksum))
    }
    await #expect(throws: (any Error).self) { try await archive.apply(first) }
  }
  @Test func committedLibraryStaysPlayableThroughAnInterruptedSync() async throws {
    let root = try folder()
    defer { try? FileManager.default.removeItem(at: root) }
    let music = root.appendingPathComponent("Music")
    let archive = try CompanionArchive(directory: music)
    let first = CompanionContext(revision: 1, account: "one")
    let bytes = Data("audio".utf8)
    let item = song("track", bytes: bytes)
    try await archive.apply(first)
    try await archive.apply(CompanionManifest(context: first, songs: [item]))
    let file = root.appendingPathComponent("incoming")
    try bytes.write(to: file)
    try await archive.accept(
      file, ticket: CompanionFile(context: first, songId: item.id, checksum: item.checksum))
    // A new revision's context arrives, then the phone disconnects and the Watch relaunches.
    try await archive.apply(CompanionContext(revision: 2, account: "one"))
    let relaunched = try CompanionArchive(directory: music)
    #expect(await relaunched.snapshot().songs.map(\.id) == [item.id])
    #expect(await relaunched.localFile(id: item.id) != nil)
    #expect(await relaunched.receipt().ready[item.id] == item.checksum)
    #expect(await relaunched.receipt().manifestApplied == false)
    // Another account revokes the library immediately, before any manifest.
    try await relaunched.apply(CompanionContext(revision: 3, account: "two"))
    #expect(await relaunched.localFile(id: item.id) == nil)
    #expect(await relaunched.snapshot().songs.isEmpty)
    let revoked = try CompanionArchive(directory: music)
    #expect(await revoked.localFile(id: item.id) == nil)
  }
  @Test func damagedSavedLibraryStartsEmptyWithoutCrashing() async throws {
    let root = try folder()
    defer { try? FileManager.default.removeItem(at: root) }
    let music = root.appendingPathComponent("Music")
    let archive = try CompanionArchive(directory: music)
    let context = CompanionContext(revision: 1, account: "one")
    try await archive.apply(context)
    try await archive.apply(CompanionManifest(context: context, songs: [song("a", bytes: Data("x".utf8))]))
    // A half-written or corrupted manifest after a crash or full storage.
    try Data("{\"songs\": [".utf8).write(to: music.appendingPathComponent("manifest.json"))
    try Data("not a number".utf8).write(to: music.appendingPathComponent("applied.json"))
    let recovered = try CompanionArchive(directory: music)
    #expect(await recovered.snapshot().songs.isEmpty)
    #expect(await recovered.localFile(id: "a") == nil)
    #expect(await recovered.receipt().manifestApplied == false)
    // The phone's next manifest restores it.
    try await recovered.apply(
      CompanionManifest(context: context, songs: [song("a", bytes: Data("x".utf8))]))
    #expect(await recovered.receipt().manifestApplied)
  }
  @Test func fullBudgetRejectsMoreAudioAndKeepsWhatIsThere() async throws {
    let root = try folder()
    defer { try? FileManager.default.removeItem(at: root) }
    let archive = try CompanionArchive(
      directory: root.appendingPathComponent("Music"), budgetBytes: 8)
    let context = CompanionContext(revision: 1, account: "one")
    let first = Data("12345".utf8)
    let second = Data("67890".utf8)
    let a = song("a", bytes: first)
    let b = song("b", bytes: second)
    try await archive.apply(context)
    try await archive.apply(CompanionManifest(context: context, songs: [a, b]))
    let file = root.appendingPathComponent("incoming")
    try first.write(to: file)
    try await archive.accept(
      file, ticket: CompanionFile(context: context, songId: a.id, checksum: a.checksum))
    try second.write(to: file)
    await #expect(throws: CompanionFailure.quota) {
      try await archive.accept(
        file, ticket: CompanionFile(context: context, songId: b.id, checksum: b.checksum))
    }
    #expect(await archive.localFile(id: a.id) != nil)
    #expect(await archive.receipt().ready.keys.sorted() == [a.id])
  }
  @Test func interruptedSaveLeavesNoPartialFiles() async throws {
    let root = try folder()
    defer { try? FileManager.default.removeItem(at: root) }
    let music = root.appendingPathComponent("Music")
    _ = try CompanionArchive(directory: music)
    let partial = music.appendingPathComponent(String(repeating: "a", count: 64) + ".partial")
    try Data("half".utf8).write(to: partial)
    _ = try CompanionArchive(directory: music)
    #expect(!FileManager.default.fileExists(atPath: partial.path))
  }
  @Test func retainedSongsAcceptEarlierTransfersButRevocationClearsFiles() async throws {
    let root = try folder()
    defer { try? FileManager.default.removeItem(at: root) }
    let archive = try CompanionArchive(directory: root.appendingPathComponent("Music"))
    let first = CompanionContext(revision: 4, account: "one")
    let next = CompanionContext(revision: 5, account: "one")
    let bytes = Data("audio".utf8)
    let item = song("track", bytes: bytes)
    try await archive.apply(first)
    try await archive.apply(next)
    try await archive.apply(CompanionManifest(context: next, songs: [item]))
    let file = root.appendingPathComponent("incoming")
    try bytes.write(to: file)
    try await archive.accept(
      file, ticket: CompanionFile(context: first, songId: item.id, checksum: item.checksum))
    let saved = try #require(await archive.localFile(id: item.id))
    try await archive.apply(CompanionContext(revision: 6, account: nil))
    #expect(!FileManager.default.fileExists(atPath: saved.path))
    #expect(await archive.receipt().ready.isEmpty)
    let recovered = try CompanionArchive(directory: root.appendingPathComponent("Music"))
    #expect(await recovered.activeContext().account == nil)
    #expect(await recovered.receipt().ready.isEmpty)
  }
  @Test func localRemovalSurvivesRestartAndLateManifestAndTransfer() async throws {
    let root = try folder()
    defer { try? FileManager.default.removeItem(at: root) }
    let archive = try CompanionArchive(directory: root.appendingPathComponent("Music"))
    let context = CompanionContext(revision: 1, account: "one")
    let bytes = Data("audio".utf8)
    let item = song("track", bytes: bytes)
    let manifest = CompanionManifest(context: context, songs: [item])
    try await archive.apply(context)
    try await archive.apply(manifest)
    let file = root.appendingPathComponent("incoming")
    try bytes.write(to: file)
    try await archive.accept(
      file, ticket: CompanionFile(context: context, songId: item.id, checksum: item.checksum))
    // Re-delivery repairs an existing file rather than trusting its size alone.
    let stored = try #require(await archive.localFile(id: item.id))
    try Data(repeating: 0, count: bytes.count).write(to: stored)
    try bytes.write(to: file)
    try await archive.accept(
      file, ticket: CompanionFile(context: context, songId: item.id, checksum: item.checksum))
    #expect(try Data(contentsOf: stored) == bytes)
    try await archive.removeLocal(id: item.id)
    let restored = try CompanionArchive(directory: root.appendingPathComponent("Music"))
    try await restored.apply(manifest)
    #expect(await restored.localFile(id: item.id) == nil)
    try bytes.write(to: file)
    await #expect(throws: (any Error).self) {
      try await restored.accept(
        file, ticket: CompanionFile(context: context, songId: item.id, checksum: item.checksum))
    }
  }
  @Test func watchListensRemainDurableUntilAcknowledgedForTheSameAccount() async throws {
    let root = try folder()
    defer { try? FileManager.default.removeItem(at: root) }
    let archive = try CompanionArchive(directory: root)
    try await archive.apply(CompanionContext(revision: 1, account: "one"))
    let event = ListeningEvent(
      eventId: UUID().uuidString, trackId: "song", playedAt: Int(Date().timeIntervalSince1970),
      seconds: 30)
    try await archive.recordListen(event)
    try await archive.recordListen(event)
    let restored = try CompanionArchive(directory: root)
    #expect(await restored.pendingListens().count == 1)
    await #expect(throws: (any Error).self) {
      try await restored.acknowledgeListen(event.id, account: "two")
    }
    #expect(await restored.pendingListens().count == 1)
    try await restored.acknowledgeListen(event.id, account: "one")
    #expect(await restored.pendingListens().isEmpty)
    try await restored.recordListen(event)
    try await restored.apply(CompanionContext(revision: 2, account: "two"))
    #expect(await restored.pendingListens().isEmpty)
  }
  @Test func watchDownloadIntentsRetryUntilManifestConfirmsAndClearOnAccountChange() async throws {
    let root = try folder()
    defer { try? FileManager.default.removeItem(at: root) }
    let archive = try CompanionArchive(directory: root)
    let first = CompanionContext(revision: 1, account: "one")
    try await archive.apply(first)
    try await archive.apply(CompanionManifest(context: first, songs: []))
    try await archive.request("download", ids: ["track"])
    let restored = try CompanionArchive(directory: root)
    #expect(await restored.pendingRequests()["track"] == "download")
    let next = CompanionContext(revision: 2, account: "one")
    let item = song("track", bytes: Data("audio".utf8))
    try await restored.apply(next)
    try await restored.apply(CompanionManifest(context: next, songs: [item]))
    #expect(await restored.pendingRequests().isEmpty)
    try await restored.request("remove", ids: [item.id])
    try await restored.removeLocal(id: item.id)
    try await restored.apply(CompanionManifest(context: next, songs: [item]))
    #expect(await restored.pendingRequests()[item.id] == "remove")
    try await restored.request("download", ids: [item.id])
    let file = root.appendingPathComponent("incoming")
    try Data("audio".utf8).write(to: file)
    try await restored.accept(
      file, ticket: CompanionFile(context: next, songId: item.id, checksum: item.checksum))
    #expect(await restored.localFile(id: item.id) != nil)
    try await restored.apply(CompanionContext(revision: 3, account: nil))
    #expect(await restored.pendingRequests().isEmpty)
  }
  @Test func invalidManifestAndStorageBudgetAreRejected() async throws {
    let root = try folder()
    defer { try? FileManager.default.removeItem(at: root) }
    let archive = try CompanionArchive(directory: root, budgetBytes: 2)
    let context = CompanionContext(revision: 1, account: "one")
    let bytes = Data("audio".utf8)
    let item = song("track", bytes: bytes)
    try await archive.apply(context)
    await #expect(throws: (any Error).self) {
      try await archive.apply(CompanionManifest(context: context, songs: [item, item]))
    }
    var malicious = item
    malicious.checksum = "../escaped"
    await #expect(throws: (any Error).self) {
      try await archive.apply(CompanionManifest(context: context, songs: [malicious]))
    }
    try await archive.apply(CompanionManifest(context: context, songs: [item]))
    let file = root.appendingPathComponent("incoming")
    try bytes.write(to: file)
    await #expect(throws: (any Error).self) {
      try await archive.accept(
        file, ticket: CompanionFile(context: context, songId: item.id, checksum: item.checksum))
    }
    #expect(await archive.receipt().ready.isEmpty)
  }
  /// #64: the phone announces a new revision before the songs already on
  /// their way and sends its manifest after them. Those songs must not be
  /// thrown away while the new revision is staged, or they cross again.
  @Test func songsOnTheirWayArriveWhileANewRevisionIsStaged() async throws {
    let root = try folder()
    defer { try? FileManager.default.removeItem(at: root) }
    let archive = try CompanionArchive(directory: root.appendingPathComponent("Music"))
    let first = CompanionContext(revision: 1, account: "one")
    let next = CompanionContext(revision: 2, account: "one")
    let old = Data("already listed".utf8)
    let new = Data("added in the next revision".utf8)
    let listed = song("listed", bytes: old)
    let added = song("added", bytes: new)
    try await archive.apply(first)
    try await archive.apply(CompanionManifest(context: first, songs: [listed]))
    try await archive.apply(next)  // Announced; its manifest follows the songs.
    let listedTicket = CompanionFile(context: first, songId: listed.id, checksum: listed.checksum)
    let addedTicket = CompanionFile(context: next, songId: added.id, checksum: added.checksum)
    #expect(await archive.arrival(of: listedTicket) == .accept)
    #expect(await archive.arrival(of: addedTicket) == .wait)
    let listedFile = root.appendingPathComponent("incoming-listed")
    try old.write(to: listedFile)
    try await archive.accept(listedFile, ticket: listedTicket)
    #expect(await archive.localFile(id: listed.id) != nil)
    try await archive.apply(CompanionManifest(context: next, songs: [listed, added]))
    #expect(await archive.arrival(of: addedTicket) == .accept)
    let addedFile = root.appendingPathComponent("incoming-added")
    try new.write(to: addedFile)
    try await archive.accept(addedFile, ticket: addedTicket)
    #expect(await archive.receipt().ready == [listed.id: listed.checksum, added.id: added.checksum])
    // Still refused: another account, a newer revision than announced, a
    // removed song, and a song the applied library no longer lists.
    let other = CompanionFile(
      context: CompanionContext(revision: 2, account: "two"), songId: listed.id,
      checksum: listed.checksum)
    let ahead = CompanionFile(
      context: CompanionContext(revision: 3, account: "one"), songId: listed.id,
      checksum: listed.checksum)
    #expect(await archive.arrival(of: other) == .stale)
    #expect(await archive.arrival(of: ahead) == .stale)
    try await archive.removeLocal(id: listed.id)
    #expect(await archive.arrival(of: listedTicket) == .stale)
    try await archive.apply(CompanionContext(revision: 4, account: "one"))
    try await archive.apply(
      CompanionManifest(context: CompanionContext(revision: 4, account: "one"), songs: []))
    #expect(await archive.arrival(of: addedTicket) == .stale)
  }
}
