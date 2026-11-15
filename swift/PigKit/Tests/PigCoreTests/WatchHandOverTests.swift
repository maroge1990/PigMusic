import CryptoKit
import Foundation
import PigDomain
import PigDownloads
import Testing

/// #218: the iPhone kept a hidden AAC copy of every song sent to the Watch.
/// Once the Watch's applied confirmation lists a song with the same checksum,
/// that copy goes; a song the Watch later loses is fetched again.
@Suite struct WatchHandOverTests {
  private struct NoSource: DeviceCopySource {
    func deviceCopy(trackId: String, quality: String, create: Bool) async throws -> DeviceCopy {
      throw CancellationError()
    }
    func copyRequest(trackId: String, quality: String) async throws -> URLRequest {
      throw CancellationError()
    }
  }
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
  private func copy(_ id: String, quality: String) throws -> DeviceCopy {
    let json: [String: Any] = [
      "state": "ready", "quality": quality, "sha256": checksum(id), "source_sha256": checksum(id),
      "bytes": payload(id).count, "extension": "m4a", "key": id,
    ]
    return try LibraryCodec.decoder().decode(
      DeviceCopy.self, from: JSONSerialization.data(withJSONObject: json))
  }
  /// An archive holding a ready copy of each song, as the worker leaves it.
  private func archive(_ root: URL, _ ids: [String], quality: String = "aac") async throws
    -> DownloadArchive
  {
    let archive = try DownloadArchive(directory: root)
    try await archive.enqueue(try ids.map(track), quality: quality)
    for id in ids {
      let incoming = FileManager.default.temporaryDirectory.appendingPathComponent(
        UUID().uuidString)
      try payload(id).write(to: incoming)
      let record = try #require(await archive.snapshot().first { $0.id == id })
      try await archive.publishFile(
        incoming, record: record, manifest: try copy(id, quality: quality), budget: 1 << 30)
    }
    return archive
  }
  private func folder() -> URL {
    FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
  }
  private func audioFiles(_ root: URL) -> Set<String> {
    Set(
      ((try? FileManager.default.contentsOfDirectory(atPath: root.path)) ?? []).filter {
        $0.hasSuffix(".m4a")
      })
  }

  @Test func aConfirmedSongsCopyGoesAndAnUnconfirmedOneStays() async throws {
    let root = folder()
    defer { try? FileManager.default.removeItem(at: root) }
    let archive = try await archive(root, ["a", "b"])
    #expect(audioFiles(root).count == 2)
    let listed = ["a": checksum("a"), "b": checksum("b")]
    let plan = try await archive.settleHandOver(
      listed: listed, ready: ["a": checksum("a")], applied: true)
    #expect(plan.deliver == ["a": checksum("a")])
    #expect(plan.refetch.isEmpty)
    let records = await archive.snapshot()
    let a = try #require(records.first { $0.id == "a" })
    let b = try #require(records.first { $0.id == "b" })
    // a's record stays, ready, with its checksum and size, so the Watch keeps it
    // listed and its storage still counts; only the iPhone's file went.
    #expect(a.delivered && a.state == "ready" && a.checksum == checksum("a"))
    #expect(a.total == Int64(payload("a").count))
    #expect(!b.delivered)
    #expect(audioFiles(root).count == 1)
    #expect(await archive.localFile(try track("a")) == nil)
    #expect(await archive.localFile(try track("b")) != nil)
    #expect(await archive.usedBytes() == Int64(payload("a").count + payload("b").count))
  }

  @Test func aReceiptNotAppliedOrWithAnotherChecksumDeletesNothing() async throws {
    let root = folder()
    defer { try? FileManager.default.removeItem(at: root) }
    let archive = try await archive(root, ["a", "b"])
    let listed = ["a": checksum("a"), "b": checksum("b")]
    let ready = ["a": checksum("a"), "b": checksum("b")]
    // The Watch hasn't applied this version: its list is the one before.
    #expect(
      try await archive.settleHandOver(listed: listed, ready: ready, applied: false).isEmpty)
    // The Watch holds other audio of these songs.
    #expect(
      try await archive.settleHandOver(
        listed: ["a": "other", "b": checksum("b")], ready: ["a": "other", "b": "stale"],
        applied: true
      ).isEmpty)
    #expect(await archive.snapshot().allSatisfy { !$0.delivered })
    #expect(audioFiles(root).count == 2)
  }

  @MainActor @Test func aDeliveredSongTheWatchLosesIsFetchedAgain() async throws {
    let root = folder()
    defer { try? FileManager.default.removeItem(at: root) }
    let archive = try await archive(root, ["a", "b"])
    let controller = DownloadController(archive: archive, source: NoSource())
    // No fetching in this test: the worker stays stopped.
    await controller.suspendPreparation()
    let listed = ["a": checksum("a"), "b": checksum("b")]
    await controller.settleHandOver(listed: listed, ready: listed, applied: true)
    #expect(controller.records.allSatisfy { $0.delivered })
    // A version that doesn't name a song says nothing about it.
    #expect(
      await controller.settleHandOver(listed: ["b": checksum("b")], ready: [:], applied: true)
        .refetch == ["b": checksum("b")])
    // A receipt not applied doesn't count; a version naming a is applied without it.
    #expect(
      await controller.settleHandOver(listed: listed, ready: ["b": checksum("b")], applied: false)
        .isEmpty)
    let plan = await controller.settleHandOver(listed: listed, ready: [:], applied: true)
    #expect(plan.refetch == ["a": checksum("a")])
    let a = try #require(controller.records.first { $0.id == "a" })
    #expect(a.state == "queued" && a.checksum == nil && !a.delivered)
    #expect(a.effectiveOrigins == [.direct])
  }

  @Test func theDeliveredStateSurvivesAReload() async throws {
    let root = folder()
    defer { try? FileManager.default.removeItem(at: root) }
    let archive = try await archive(root, ["a", "b"])
    try await archive.settleHandOver(
      listed: ["a": checksum("a"), "b": checksum("b")], ready: ["a": checksum("a")],
      applied: true)
    let reopened = try DownloadArchive(directory: root)
    let records = await reopened.snapshot()
    #expect(records.first { $0.id == "a" }?.delivered == true)
    #expect(records.first { $0.id == "b" }?.delivered == false)
    // b's file survived the launch's clean-up; a's is still gone.
    #expect(await reopened.localFile(try track("b")) != nil)
    #expect(audioFiles(root).count == 1)
    // Adding the song again doesn't fetch it again.
    try await reopened.enqueue([try track("a")], quality: "aac")
    #expect(await reopened.snapshot().first { $0.id == "a" }?.delivered == true)
  }

  @Test func removingADeliveredSongOrEverythingStillWorks() async throws {
    let root = folder()
    defer { try? FileManager.default.removeItem(at: root) }
    let archive = try await archive(root, ["a", "b", "c"])
    let all = ["a": checksum("a"), "b": checksum("b"), "c": checksum("c")]
    try await archive.settleHandOver(listed: all, ready: all, applied: true)
    try await archive.remove(id: "a")
    #expect(try await archive.release(ids: ["b"], origin: .direct) == ["b"])
    #expect(await archive.snapshot().map(\.id) == ["c"])
    try await archive.clear()
    #expect(await archive.snapshot().isEmpty)
  }

  @Test func thePersonsOwnDownloadsAreUntouched() async throws {
    let watchRoot = folder()
    let phoneRoot = folder()
    defer {
      try? FileManager.default.removeItem(at: watchRoot)
      try? FileManager.default.removeItem(at: phoneRoot)
    }
    let watch = try await archive(watchRoot, ["a"])
    let phone = try await archive(phoneRoot, ["a"])
    let before = await phone.snapshot()
    try await watch.settleHandOver(
      listed: ["a": checksum("a")], ready: ["a": checksum("a")], applied: true)
    #expect(await watch.snapshot().first?.delivered == true)
    #expect(await phone.snapshot() == before)
    #expect(await phone.localFile(try track("a")) != nil)
    #expect(audioFiles(phoneRoot).count == 1)
  }
}
