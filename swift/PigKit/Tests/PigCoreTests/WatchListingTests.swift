import CryptoKit
import Foundation
import PigCompanion
import PigDomain
import PigDownloads
import Testing

/// The server's batch answer: these copies, any other song unanswered.
private struct AnsweringCopies: DeviceCopySource {
  let answers: [String: DeviceCopy]
  let failing: Bool
  init(_ answers: [String: DeviceCopy], failing: Bool) {
    self.answers = answers
    self.failing = failing
  }
  func deviceCopy(trackId: String, quality: String, create: Bool) async throws -> DeviceCopy {
    throw URLError(.badServerResponse)
  }
  func deviceCopies(trackIds: [String], quality: String) async throws -> [String: DeviceCopy] {
    if failing { throw URLError(.notConnectedToInternet) }
    return answers.filter { trackIds.contains($0.key) }
  }
  func copyRequest(trackId: String, quality: String) async throws -> URLRequest {
    URLRequest(url: URL(string: "https://server.invalid/" + trackId)!)
  }
}

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

  /// Codex confirming 213c33f: the earlier manifests live outside the
  /// records. With the applied one unsaved, a failed publication and a
  /// relaunch, nothing remembered a song being fetched again, and the next
  /// manifest dropped it. The record now keeps it, saved with the records.
  @Test func aRefetchStaysListedAcrossARelaunchWithNoEarlierManifest() async throws {
    let root = folder()
    let watchRoot = folder()
    defer {
      try? FileManager.default.removeItem(at: root)
      try? FileManager.default.removeItem(at: watchRoot)
    }
    let archive = try await archive(root, ["a", "b"])
    let first = CompanionContext(revision: 1, account: "one")
    let songs = await listed(archive).songs
    let watch = try CompanionArchive(directory: watchRoot)
    try await watch.apply(first)
    try await watch.apply(CompanionManifest(context: first, songs: songs))
    for song in songs {
      let incoming = folder()
      try payload(song.id).write(to: incoming)
      try await watch.accept(
        incoming, ticket: CompanionFile(context: first, songId: song.id, checksum: song.checksum))
    }
    let all = ["a": checksum("a"), "b": checksum("b")]
    try await archive.settleHandOver(listed: all, ready: all, applied: true)
    try await archive.settleHandOver(listed: all, ready: ["b": checksum("b")], applied: true)
    // Relaunch: only downloads.json survives; no manifest was saved.
    let reopened = try DownloadArchive(directory: root)
    let refetching = try #require(await reopened.snapshot().first { $0.id == "a" })
    #expect(refetching.state == "queued")
    #expect(refetching.watchChecksum == checksum("a"))
    #expect(refetching.watchBytes == Int64(payload("a").count))
    let next = await listed(reopened, earlier: WatchListing.earlier(account: "one", nil, nil, nil))
    #expect(next.songs == songs)
    #expect(next.kept == ["a"])
    let second = CompanionContext(revision: 5, account: "one")
    try await watch.apply(second)
    try await watch.apply(CompanionManifest(context: second, songs: next.songs))
    #expect(await watch.receipt().ready == all)
    // The new copy takes over; the Watch's listing fields go with it.
    let incoming = FileManager.default.temporaryDirectory.appendingPathComponent(
      UUID().uuidString)
    try payload("a").write(to: incoming)
    try await reopened.publishFile(
      incoming, record: refetching, manifest: try copy("a"), budget: 1 << 30)
    let ready = try #require(await reopened.snapshot().first { $0.id == "a" })
    #expect(ready.state == "ready" && ready.watchChecksum == nil && ready.watchBytes == nil)
    #expect(await listed(reopened).songs == songs)
    // Removing it still drops it.
    try await reopened.remove(id: "a")
    #expect(await listed(reopened).songs.map(\.id) == ["b"])
  }

  /// D-109: pausing songs waiting for the Watch holds back their audio only.
  /// The manifest is the same paused or not, so the Watch deletes nothing:
  /// neither a song it already holds, confirmed or not, nor one on its way.
  @Test func pausingASongWaitingForTheWatchNeverDropsItFromTheManifest() async throws {
    let root = folder()
    let watchRoot = folder()
    defer {
      try? FileManager.default.removeItem(at: root)
      try? FileManager.default.removeItem(at: watchRoot)
    }
    let archive = try await archive(root, ["a", "b", "c"])
    let published = await listed(archive).songs
    #expect(published.map(\.id) == ["a", "b", "c"])
    // The Watch applies the manifest and receives a and b; the iPhone has
    // settled a's hand-over (its copy went) but not yet b's.
    let watch = try CompanionArchive(directory: watchRoot)
    let first = CompanionContext(revision: 1, account: "one")
    try await watch.apply(first)
    try await watch.apply(CompanionManifest(context: first, songs: published))
    for id in ["a", "b"] {
      let ticket = CompanionFile(context: first, songId: id, checksum: checksum(id))
      let incoming = folder()
      try payload(id).write(to: incoming)
      try await watch.accept(incoming, ticket: ticket)
    }
    let all = ["a": checksum("a"), "b": checksum("b"), "c": checksum("c")]
    try await archive.settleHandOver(
      listed: all, ready: ["a": checksum("a")], applied: true)
    // Mark pauses the whole album.
    try await archive.pauseForWatch(ids: ["a", "b", "c"])
    let records = await archive.snapshot()
    func record(_ id: String) throws -> DownloadRecord {
      try #require(records.first { $0.id == id })
    }
    #expect(try record("a").delivered && record("a").held == nil)
    #expect(try record("b").isHeld && record("c").isHeld)
    // The manifest is unchanged, and the Watch keeps everything it holds.
    let paused = await listed(archive, earlier: published)
    #expect(paused.songs == published)
    let second = CompanionContext(revision: 2, account: "one")
    try await watch.apply(second)
    try await watch.apply(CompanionManifest(context: second, songs: paused.songs))
    #expect(await watch.receipt().ready == ["a": checksum("a"), "b": checksum("b")])
    // Only c's audio was still to go, and it is held back.
    let held = Set(records.filter(\.isHeld).map(\.id))
    let plan = WatchDelivery.audio(
      songs: paused.songs, receipt: await watch.receipt(), inFlight: [], paused: held,
      hasFile: { _ in true }, due: { _ in true })
    #expect(plan.send.isEmpty && plan.ahead.isEmpty)
    // The Watch confirms b: its hand-over goes ahead, and the hold ends.
    try await archive.settleHandOver(
      listed: all, ready: ["a": checksum("a"), "b": checksum("b")], applied: true)
    let b = try #require(await archive.snapshot().first { $0.id == "b" })
    #expect(b.delivered && b.held == nil)
    // Resumed, c goes out as normal; the listing never changed.
    try await archive.resumeForWatch(ids: ["c"])
    #expect(await archive.snapshot().allSatisfy { !$0.isHeld })
    #expect(await listed(archive, earlier: published).songs == published)
    let resumed = WatchDelivery.audio(
      songs: published, receipt: await watch.receipt(), inFlight: [], hasFile: { _ in true },
      due: { _ in true })
    #expect(resumed.send.map(\.id) == ["c"])
    // Added again in another quality while held, its new copy stays paused.
    try await archive.pauseForWatch(ids: ["c"])
    try await archive.enqueue([try track("c")], quality: "original")
    let readded = try #require(await archive.snapshot().first { $0.id == "c" })
    #expect(readded.isPaused && readded.held == true)
    #expect(await listed(archive, earlier: published).songs == published)
  }

  /// Codex round 1 on #228: the Pause chose its path from a copy of the
  /// records, so a copy finishing just before the Pause (refused as ready) or
  /// just after it (finishing cleared the pause) went to the Watch anyway.
  /// The archive now records the Pause in one step and keeps it as a hold.
  @Test func aWatchPauseRacingTheCopyFinishingStillHoldsTheSong() async throws {
    let root = folder()
    defer { try? FileManager.default.removeItem(at: root) }
    let archive = try DownloadArchive(directory: root)
    try await archive.enqueue(try ["before", "after", "phone"].map(track), quality: "aac")
    func finish(_ id: String) async throws {
      let incoming = folder()
      try payload(id).write(to: incoming)
      let record = try #require(await archive.snapshot().first { $0.id == id })
      try await archive.publishFile(incoming, record: record, manifest: try copy(id), budget: 1 << 30)
    }
    func record(_ id: String) async throws -> DownloadRecord {
      try #require(await archive.snapshot().first { $0.id == id })
    }
    // Finished just before the Pause reached the archive.
    try await finish("before")
    #expect(try await archive.pauseForWatch(ids: ["before"]).isEmpty)
    // Paused while preparing, then finished anyway.
    #expect(try await archive.pauseForWatch(ids: ["after"]) == ["after"])
    #expect(try await record("after").isPaused)
    try await finish("after")
    #expect(try await record("after").isHeld)
    #expect(try await record("after").paused == nil)
    // The iPhone's own pause keeps #127: a finished file is no longer paused.
    try await archive.setPaused(id: "phone", true)
    try await finish("phone")
    #expect(try await record("phone").isHeld == false)
    // Neither paused song's audio goes to the Watch until Resume; both stay listed.
    let listing = await listed(archive)
    #expect(listing.songs.map(\.id) == ["after", "before", "phone"])
    let receipt = CompanionReceipt(
      context: CompanionContext(revision: 1, account: "one"), ready: [:], usedBytes: 0,
      budgetBytes: 1 << 30)
    func sent() async -> [String] {
      let paused = Set(await archive.snapshot().filter(\.isHeld).map(\.id))
      return WatchDelivery.audio(
        songs: listing.songs, receipt: receipt, inFlight: [], paused: paused,
        hasFile: { _ in true }, due: { _ in true }
      ).send.map(\.id)
    }
    #expect(await sent() == ["phone"])
    try await archive.resumeForWatch(ids: ["before", "after"])
    #expect(await sent() == ["after", "before", "phone"])
  }

  /// Mark's device round (#232): a song that failed while paused showed
  /// "Paused" and hid its error. A failed song now shows its failure and retry
  /// mark; the pause is kept, so an automatic re-add (a followed playlist
  /// refreshed) stays paused (Codex round 1), and only Retry starts it.
  @Test func aSongThatFailsWhilePausedShowsItsFailure() async throws {
    let root = folder()
    defer { try? FileManager.default.removeItem(at: root) }
    let archive = try DownloadArchive(directory: root)
    try await archive.enqueue(try ["watch", "phone"].map(track), quality: "aac")
    func record(_ id: String) async throws -> DownloadRecord {
      try #require(await archive.snapshot().first { $0.id == id })
    }
    try await archive.pauseForWatch(ids: ["watch"])
    try await archive.setPaused(id: "phone", true)
    try await archive.fail(id: "watch", message: "The server could not prepare this song.")
    try await archive.fail(id: "phone", message: "The server could not prepare this song.")
    let watch = try await record("watch")
    let phone = try await record("phone")
    #expect(!watch.isPaused && !watch.isHeld && !phone.isPaused)
    #expect(
      TransferControl.watch(watch, onWatch: false, progress: nil)
        == TransferControl(.failed, tap: .retry))
    #expect(TransferControl.phone(phone, progress: nil) == TransferControl(.failed, tap: .retry))
    // An automatic re-add keeps the person's pause.
    try await archive.enqueue([try track("watch"), try track("phone")], quality: "aac")
    let readded = try await record("watch")
    #expect(readded.isPaused && readded.held == true)
    #expect(try await record("phone").isPaused)
    // Their Retry ends it and starts the song.
    try await archive.fail(id: "watch", message: "The server could not prepare this song.")
    try await archive.clearPause(ids: ["watch"])
    try await archive.enqueue([try track("watch")], quality: "aac")
    let retried = try await record("watch")
    #expect(retried.state == "queued" && retried.paused == nil && retried.held == nil)
    // Retry acts only on failed songs: a queued pause stays.
    try await archive.clearPause(ids: ["phone"])
    #expect(try await record("phone").isPaused)
  }

  /// D-113: the background hand-off (copies asked for in one go, ready ones
  /// queued for the background downloader, one the server no longer answers
  /// for left queued, a pass whose request fails) never takes a song off the
  /// Watch's list, so the Watch deletes nothing because of it.
  @Test func aBackgroundHandOffNeverShrinksTheWatchList() async throws {
    let root = folder()
    defer { try? FileManager.default.removeItem(at: root) }
    let archive = try await archive(root, ["a", "b", "c"])
    try await archive.enqueue(try ["d", "e", "f"].map(track), quality: "aac")
    let before = await listed(archive)
    #expect(before.songs.map(\.id) == ["a", "b", "c"])
    let answers = ["d": try copy("d"), "e": try preparing("e")]
    for failing in [false, true] {
      let source = AnsweringCopies(answers, failing: failing)
      let outcome = await BackgroundHandOff.run(
        await archive.snapshot(), source: source, limit: BackgroundHandOff.askLimit,
        isCancelled: { false }
      ) { record, copy, _ in
        // What the background session does when a copy is handed to it.
        try await archive.prepareFileTransfer(record, manifest: copy, budget: 1 << 30)
      }
      #expect(outcome.handed == (failing ? [] : ["d"]))
      let after = await listed(archive, earlier: before.songs)
      #expect(Set(before.songs).isSubset(of: Set(after.songs)))
      #expect(await archive.snapshot().count == 6)
    }
  }
  private func preparing(_ id: String) throws -> DeviceCopy {
    var json =
      try JSONSerialization.jsonObject(with: LibraryCodec.encoder().encode(copy(id)))
      as! [String: Any]
    json["state"] = "preparing"
    return try LibraryCodec.decoder().decode(
      DeviceCopy.self, from: JSONSerialization.data(withJSONObject: json))
  }

  @Test func recordsSavedBeforeTheWatchFieldsStillLoad() throws {
    let json = #"{"track":{"id":"a","title":"Song","artist":"Artist"},"quality":"aac","state":"queued","bytes":0,"total":0}"#
    let record = try LibraryCodec.decoder().decode(DownloadRecord.self, from: Data(json.utf8))
    #expect(record.watchChecksum == nil && record.watchBytes == nil)
  }
}
