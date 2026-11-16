import CryptoKit
import Foundation
import PigCompanion
import PigDomain
import PigDownloads
import Testing

/// #218: the Watch deletes the audio of every song its manifest doesn't list.
/// A publish that raced two hand-overs listed 5 of 97 songs, and the Watch
/// deleted 92; the refetches that followed left those songs out again.
@Suite struct WatchListingTests {
  private func track(_ id: String) throws -> Track {
    try LibraryCodec.decoder().decode(
      Track.self,
      from: Data(#"{"id":"\#(id)","title":"Song","artist":"Artist","source_revision":"v1"}"#.utf8))
  }
  private func payload(_ id: String) -> Data {
    Data(repeating: UInt8(id.utf8.first ?? 1), count: 4096)
  }
  private func checksum(_ id: String) -> String {
    SHA256.hash(data: payload(id)).map { String(format: "%02x", $0) }.joined()
  }
  private func copy(_ id: String) throws -> DeviceCopy {
    let json: [String: Any] = [
      "state": "ready", "quality": "aac", "sha256": checksum(id), "source_sha256": checksum(id),
      "bytes": payload(id).count, "extension": "m4a", "key": id,
    ]
    return try LibraryCodec.decoder().decode(
      DeviceCopy.self, from: JSONSerialization.data(withJSONObject: json))
  }
  /// The iPhone's Watch archive holding a ready copy of each song.
  private func archive(_ root: URL, _ ids: [String]) async throws -> DownloadArchive {
    let archive = try DownloadArchive(directory: root)
    try await archive.enqueue(try ids.map(track), quality: "aac")
    for id in ids {
      let incoming = FileManager.default.temporaryDirectory.appendingPathComponent(
        UUID().uuidString)
      try payload(id).write(to: incoming)
      let record = try #require(await archive.snapshot().first { $0.id == id })
      try await archive.publishFile(incoming, record: record, manifest: try copy(id), budget: 1 << 30)
    }
    return archive
  }
  private func folder() -> URL {
    FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
  }
  /// The manifest songs a publish builds from one reading of the archive.
  private func listed(
    _ archive: DownloadArchive, earlier: [CompanionSong] = [], held: [String: String] = [:]
  ) async -> WatchListing.Result {
    let listing = await archive.listing()
    let files = listing.files ?? [:]
    return WatchListing.songs(
      records: listing.records, hasFile: { files[$0] != nil }, earlier: earlier, held: held)
  }

  @Test func aPublishRacingTwoHandOversKeepsEveryDeliveredSong() async throws {
    let root = folder()
    defer { try? FileManager.default.removeItem(at: root) }
    let archive = try await archive(root, ["a", "b", "c"])
    let published = await listed(archive).songs
    #expect(published.map(\.id) == ["a", "b", "c"])
    let all = ["a": checksum("a"), "b": checksum("b"), "c": checksum("c")]
    // The publish read the records (DownloadController's copy) here...
    let before = await archive.listing()
    let stale = before.records
    // ...then two confirmations settled before it read the files.
    try await archive.settleHandOver(listed: all, ready: ["a": checksum("a")], applied: true)
    let receipt = ["a": checksum("a"), "b": checksum("b")]
    try await archive.settleHandOver(listed: all, ready: receipt, applied: true)
    let after = await archive.listing(reusing: before.revision)
    // The settles changed the revision, so no cached file list survives them.
    let files = try #require(after.files)
    #expect(files.keys.sorted() == ["c"])
    // Even mixing stale records with the files left after the hand-overs, no
    // song the Watch was listed with leaves the manifest.
    let mixed = WatchListing.songs(
      records: stale, hasFile: { files[$0] != nil }, earlier: published, held: receipt)
    #expect(mixed.songs == published)
    #expect(mixed.held == ["a", "b"])
    // One consistent reading lists the delivered songs as delivered.
    let consistent = WatchListing.songs(
      records: after.records, hasFile: { files[$0] != nil }, earlier: [])
    #expect(consistent.songs == published)
    #expect(consistent.kept.isEmpty)
    #expect(await archive.listing(reusing: after.revision).files == nil)
  }

  @Test func aWatchThatLostItsSongsKeepsThemListedWhileTheyAreFetchedAgain() async throws {
    let root = folder()
    let watchRoot = folder()
    defer {
      try? FileManager.default.removeItem(at: root)
      try? FileManager.default.removeItem(at: watchRoot)
    }
    let archive = try await archive(root, ["a", "b"])
    let published = await listed(archive).songs
    let all = ["a": checksum("a"), "b": checksum("b")]
    try await archive.settleHandOver(listed: all, ready: all, applied: true)
    // The Watch app was reinstalled: its applied confirmation lists nothing.
    let plan = try await archive.settleHandOver(listed: all, ready: [:], applied: true)
    #expect(plan.refetch == all)
    #expect(await archive.snapshot().allSatisfy { $0.state == "queued" })
    // The next manifest still lists both, with the checksum and size the
    // Watch knows, so it waits for their audio instead of deleting it.
    let next = await listed(archive, earlier: published, held: [:])
    #expect(next.songs == published)
    #expect(next.kept == ["a", "b"])
    #expect(next.held.isEmpty)
    // The Watch applies it, lists neither as ready, and files audio fetched
    // again with the same checksum as soon as it arrives.
    let watch = try CompanionArchive(directory: watchRoot)
    let context = CompanionContext(revision: 2, account: "one")
    try await watch.apply(context)
    try await watch.apply(CompanionManifest(context: context, songs: next.songs))
    #expect(await watch.receipt().ready.isEmpty)
    let ticket = CompanionFile(context: context, songId: "a", checksum: checksum("a"))
    #expect(await watch.arrival(of: ticket) == .accept)
    let incoming = folder()
    try payload("a").write(to: incoming)
    try await watch.accept(incoming, ticket: ticket)
    #expect(await watch.receipt().ready == ["a": checksum("a")])
    // Only a real removal drops a song.
    try await archive.remove(id: "b")
    #expect(await listed(archive, earlier: published).songs.map(\.id) == ["a"])
  }

  @Test func theWatchKeepsTheAudioOfEverySongStillListed() async throws {
    let root = folder()
    let watchRoot = folder()
    defer {
      try? FileManager.default.removeItem(at: root)
      try? FileManager.default.removeItem(at: watchRoot)
    }
    let archive = try await archive(root, ["a", "b"])
    let published = await listed(archive).songs
    let watch = try CompanionArchive(directory: watchRoot)
    let first = CompanionContext(revision: 1, account: "one")
    try await watch.apply(first)
    try await watch.apply(CompanionManifest(context: first, songs: published))
    for song in published {
      let incoming = folder()
      try payload(song.id).write(to: incoming)
      try await watch.accept(
        incoming, ticket: CompanionFile(context: first, songId: song.id, checksum: song.checksum))
    }
    let all = ["a": checksum("a"), "b": checksum("b")]
    try await archive.settleHandOver(listed: all, ready: all, applied: true)
    // A confirmation that missed a (read mid-transfer) fetches it again.
    try await archive.settleHandOver(listed: all, ready: ["b": checksum("b")], applied: true)
    let next = await listed(archive, earlier: published, held: ["b": checksum("b")])
    let second = CompanionContext(revision: 2, account: "one")
    try await watch.apply(second)
    try await watch.apply(CompanionManifest(context: second, songs: next.songs))
    // Both files survive the Watch's clean-up.
    #expect(await watch.receipt().ready == all)
  }

  /// Codex on #218: a failed manifest write cleared the published manifest,
  /// and with it every earlier song, so the retry dropped a song being
  /// fetched again and the Watch deleted its audio.
  @Test func aFailedPublicationDoesNotDropASongBeingFetchedAgain() async throws {
    let root = folder()
    let watchRoot = folder()
    defer {
      try? FileManager.default.removeItem(at: root)
      try? FileManager.default.removeItem(at: watchRoot)
    }
    let archive = try await archive(root, ["a", "b"])
    let first = CompanionContext(revision: 1, account: "one")
    let applied = CompanionManifest(context: first, songs: await listed(archive).songs)
    let watch = try CompanionArchive(directory: watchRoot)
    try await watch.apply(first)
    try await watch.apply(applied)
    for song in applied.songs {
      let incoming = folder()
      try payload(song.id).write(to: incoming)
      try await watch.accept(
        incoming, ticket: CompanionFile(context: first, songId: song.id, checksum: song.checksum))
    }
    let all = ["a": checksum("a"), "b": checksum("b")]
    try await archive.settleHandOver(listed: all, ready: all, applied: true)
    // An applied confirmation that missed a queues it again...
    try await archive.settleHandOver(listed: all, ready: ["b": checksum("b")], applied: true)
    // ...and the next manifest's write fails: nothing is published (nil),
    // nothing new written. The retry runs before a's copy is ready.
    let earlier = WatchListing.earlier(account: "one", nil, nil, applied)
    let retry = await listed(archive, earlier: earlier)
    #expect(retry.songs.map(\.id) == ["a", "b"])
    let second = CompanionContext(revision: 3, account: "one")
    try await watch.apply(second)
    try await watch.apply(CompanionManifest(context: second, songs: retry.songs))
    #expect(await watch.receipt().ready == all)
    // Another account's manifests never count.
    #expect(WatchListing.earlier(account: "two", applied).isEmpty)
    #expect(WatchListing.earlier(account: nil, applied).isEmpty)
  }
}
