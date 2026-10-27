import CryptoKit
import Foundation
import PigDomain
import PigDownloads
import PigPlayback
import PigStore
import PigSync
import Testing

private final class CopyProtocol: URLProtocol, @unchecked Sendable {
  static let payload = Data(repeating: 42, count: 150000)
  override class func canInit(with request: URLRequest) -> Bool { true }
  override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
  override func startLoading() {
    client?.urlProtocol(
      self,
      didReceive: HTTPURLResponse(
        url: request.url!, statusCode: 200,
        httpVersion: "HTTP/1.1",
        headerFields: [
          "Content-Type": "application/octet-stream", "Content-Length": String(Self.payload.count),
        ])!, cacheStoragePolicy: .notAllowed)
    client?.urlProtocol(self, didLoad: Self.payload)
    client?.urlProtocolDidFinishLoading(self)
  }
  override func stopLoading() {}
}
/// Answers copies at once, except the first song, which is still being
/// prepared on its first ask.
private actor AheadSource: DeviceCopySource {
  var calls: [String] = []
  let ready: DeviceCopy
  let preparing: DeviceCopy
  init(ready: DeviceCopy, preparing: DeviceCopy) {
    self.ready = ready
    self.preparing = preparing
  }
  func deviceCopy(trackId: String, quality: String, create: Bool) async throws -> DeviceCopy {
    calls.append((create ? "create " : "poll ") + trackId)
    return trackId == "one" && create ? preparing : ready
  }
  func copyRequest(trackId: String, quality: String) async throws -> URLRequest {
    URLRequest(url: URL(string: "https://server.invalid/" + trackId)!)
  }
}
@Suite struct DownloadTests {
  func track() throws -> Track {
    try LibraryCodec.decoder().decode(
      Track.self,
      from: Data(#"{"id":"song","title":"Song","artist":"Artist","source_revision":"v1"}"#.utf8))
  }
  func copy(sha: String? = nil) throws -> DeviceCopy {
    let hash =
      sha ?? SHA256.hash(data: CopyProtocol.payload).map { String(format: "%02x", $0) }.joined()
    let json: [String: Any] = [
      "state": "ready", "quality": "original", "sha256": hash, "source_sha256": hash,
      "bytes": CopyProtocol.payload.count, "extension": "mp3", "key": "test",
    ]
    return try LibraryCodec.decoder().decode(
      DeviceCopy.self, from: JSONSerialization.data(withJSONObject: json))
  }
  func archive(_ root: URL) throws -> DownloadArchive {
    let config = URLSessionConfiguration.ephemeral
    config.protocolClasses = [CopyProtocol.self]
    return try DownloadArchive(directory: root, configuration: config)
  }
  @MainActor @Test func waitingCopiesAreAskedForBeforeTheFirstIsReady() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let archive = try archive(root)
    let songs = try ["one", "two", "three"].map { id in
      try LibraryCodec.decoder().decode(
        Track.self,
        from: Data(#"{"id":"\#(id)","title":"Song","artist":"Artist","source_revision":"v1"}"#.utf8)
      )
    }
    try await archive.enqueue(songs, quality: "original")
    var preparing =
      try JSONSerialization.jsonObject(
        with: LibraryCodec.encoder().encode(copy())) as! [String: Any]
    preparing["state"] = "preparing"
    let source = AheadSource(
      ready: try copy(),
      preparing: try LibraryCodec.decoder().decode(
        DeviceCopy.self, from: JSONSerialization.data(withJSONObject: preparing)))
    let controller = DownloadController(archive: archive, source: source)
    controller.resume()
    for _ in 0..<100 where await archive.snapshot().contains(where: { $0.state != "ready" }) {
      try await Task.sleep(for: .milliseconds(100))
    }
    let calls = await source.calls
    let poll = try #require(calls.firstIndex(of: "poll one"))
    // While the first copy was still being prepared, the server already had
    // the others to convert (WATCH1).
    #expect(calls.prefix(poll).contains("create two"))
    #expect(calls.prefix(poll).contains("create three"))
    #expect(await archive.snapshot().allSatisfy { $0.state == "ready" })
  }
  @Test func verifiedCopySurvivesRestartThenReconcilesOnlyAuthoritativeChanges() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let archive = try archive(root)
    let song = try track()
    try await archive.enqueue([song], quality: "original")
    let record = try #require(await archive.snapshot().first)
    try await archive.transfer(
      record, manifest: copy(),
      request: URLRequest(url: URL(string: "https://server.invalid/song")!), budget: 1_000_000)
    let file = try #require(await archive.localFile(song))
    #expect(try Data(contentsOf: file) == CopyProtocol.payload)
    let reopened = try self.archive(root)
    #expect(await reopened.localFile(song) != nil)
    try await reopened.reconcile(["song": "v1"])
    #expect(await reopened.localFile(song) != nil)
    try await reopened.reconcile(["song": "v2"])
    #expect(await reopened.localFile(song) == nil)
    #expect(await reopened.snapshot().isEmpty)
    #expect(!FileManager.default.fileExists(atPath: file.path))
  }
  @Test func songInAnAlbumAndAPlaylistStaysUntilBothAreRemoved() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let archive = try archive(root)
    let song = try track()
    let playlist = DownloadOrigin(playlistId: "p1", playlistName: "Road trip")
    try await archive.enqueue([song], quality: "original")
    let record = try #require(await archive.snapshot().first)
    try await archive.transfer(
      record, manifest: copy(),
      request: URLRequest(url: URL(string: "https://server.invalid/song")!), budget: 1_000_000)
    // Adding the same ready song through a playlist keeps one file with two origins.
    try await archive.enqueue([song], quality: "original", origin: playlist)
    #expect(await archive.snapshot().first?.effectiveOrigins == [.direct, playlist])
    try await archive.clearFinished()
    #expect(await archive.snapshot().first?.cleared == true)
    #expect(try await archive.release(ids: ["song"], origin: .direct).isEmpty)
    let file = try #require(await archive.localFile(song))
    #expect(await archive.snapshot().first?.effectiveOrigins == [playlist])
    // Reopening keeps the remaining origin; removing it deletes the file.
    let reopened = try self.archive(root)
    #expect(try await reopened.release(ids: ["song"], origin: playlist) == ["song"])
    #expect(await reopened.snapshot().isEmpty)
    #expect(!FileManager.default.fileExists(atPath: file.path))
  }
  @Test func corruptDownloadsAndQuotaCannotPublishAudio() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let archive = try archive(root)
    let song = try track()
    try await archive.enqueue([song], quality: "original")
    let record = try #require(await archive.snapshot().first)
    let request = URLRequest(url: URL(string: "https://server.invalid/song")!)
    await #expect(throws: OfflineFailure.self) {
      try await archive.transfer(record, manifest: copy(), request: request, budget: 1)
    }
    await #expect(throws: OfflineFailure.self) {
      try await archive.transfer(
        record, manifest: copy(sha: String(repeating: "0", count: 64)), request: request,
        budget: 1_000_000)
    }
    #expect(await archive.localFile(song) == nil)
    #expect(try FileManager.default.contentsOfDirectory(atPath: root.path) == ["downloads.json"])
  }
  @Test func qualityReplacementKeepsExistingCopyUntilVerifiedCommit() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let archive = try archive(root)
    let song = try track()
    let request = URLRequest(url: URL(string: "https://server.invalid/song")!)
    try await archive.enqueue([song], quality: "original")
    try await archive.transfer(
      try #require(await archive.snapshot().first), manifest: copy(), request: request,
      budget: 1_000_000)
    let original = try #require(await archive.localFile(song))
    try await archive.enqueue([song], quality: "aac")
    #expect(await archive.localFile(song) == original)
    #expect(await archive.usedBytes() == Int64(CopyProtocol.payload.count))
    let recovered = try self.archive(root)
    #expect(await recovered.localFile(song) == original)
    let hash = SHA256.hash(data: CopyProtocol.payload).map { String(format: "%02x", $0) }.joined()
    let manifest = try LibraryCodec.decoder().decode(
      DeviceCopy.self,
      from: JSONSerialization.data(withJSONObject: [
        "state": "ready", "quality": "aac", "sha256": hash, "source_sha256": hash,
        "bytes": CopyProtocol.payload.count, "extension": "m4a", "key": "test",
      ]))
    let replacement = try #require(await recovered.snapshot().first)
    await #expect(throws: OfflineFailure.self) {
      try await recovered.transfer(
        replacement, manifest: manifest, request: request, budget: Int64(CopyProtocol.payload.count)
      )
    }
    #expect(await recovered.localFile(song) == original)
    try await recovered.transfer(
      replacement, manifest: manifest, request: request, budget: 1_000_000)
    let aac = try #require(await recovered.localFile(song))
    #expect(aac.pathExtension == "m4a")
    #expect(!FileManager.default.fileExists(atPath: original.path))
    #expect(await recovered.usedBytes() == Int64(CopyProtocol.payload.count))
  }
  @Test func backgroundPublicationChecksHashSourceAndConcurrentReservations() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let archive = try archive(root)
    let song = try track()
    var second = song
    second.id = "second"
    try await archive.enqueue([song, second], quality: "original")
    let records = await archive.snapshot()
    let first = try #require(records.first(where: { $0.id == song.id }))
    let other = try #require(records.first(where: { $0.id == second.id }))
    let manifest = try copy()
    try await archive.prepareFileTransfer(first, manifest: manifest, budget: 200_000)
    await #expect(throws: OfflineFailure.self) {
      try await archive.prepareFileTransfer(other, manifest: manifest, budget: 200_000)
    }
    let incoming = root.appendingPathComponent("incoming")
    try Data(repeating: 0, count: CopyProtocol.payload.count).write(to: incoming)
    await #expect(throws: OfflineFailure.self) {
      try await archive.publishFile(incoming, record: first, manifest: manifest, budget: 200_000)
    }
    #expect(await archive.localFile(song) == nil)
    try CopyProtocol.payload.write(to: incoming)
    try await archive.publishFile(incoming, record: first, manifest: manifest, budget: 200_000)
    #expect(await archive.localFile(song) != nil)
    try await archive.reconcile([song.id: "v2"])
    try CopyProtocol.payload.write(to: incoming)
    await #expect(throws: OfflineFailure.self) {
      try await archive.publishFile(incoming, record: first, manifest: manifest, budget: 200_000)
    }
    #expect(await archive.localFile(song) == nil)
  }
  @Test func queueEditingPreservesRepeatedOccurrencesAndCurrentIdentity() throws {
    let song = try track()
    var queue = PlaybackQueue()
    queue.replace([song, song, song])
    let current = queue.current?.id
    let next = queue.items[1].id
    queue.moveUpcoming(next, by: 1)
    #expect(queue.current?.id == current)
    #expect(queue.items[2].id == next)
    queue.removeUpcoming(current!)
    #expect(queue.items.count == 3)
    queue.removeUpcoming(next)
    queue.append([song], next: true)
    let saved = PlaybackCheckpoint(queue: queue, elapsed: 17)
    let decoded = try LibraryCodec.decoder().decode(
      PlaybackCheckpoint.self, from: LibraryCodec.encoder().encode(saved))
    #expect(decoded.queue.items == queue.items)
    #expect(decoded.elapsed == 17)
  }
  @Test func listeningOutboxIsDurableIdempotentAndDoesNotLoseUnacknowledgedEvents() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: root) }
    let path = root.appendingPathComponent("library.sqlite3").path
    let store = try LibraryStore(path: path)
    let first = ListeningEvent(eventId: "one", trackId: "song", playedAt: 10, seconds: 30)
    let second = ListeningEvent(eventId: "two", trackId: "song", playedAt: 20, seconds: 30)
    try await store.recordListen(first)
    try await store.recordListen(first)
    try await store.recordListen(second)
    let reopened = try LibraryStore(path: path)
    #expect(try await reopened.pendingListens().count == 2)
    try await reopened.acknowledgeListens(["one"])
    #expect(try await reopened.pendingListens().map(\.id) == ["two"])
  }
  @Test func eventsParseBoundedFramesAndIgnoreKeepalives() throws {
    var parser = EventParser()
    var events: [LiveEvent] = []
    for byte in Data(
      ": ping\r\n\r\nevent: revision\r\ndata: {\"revision\":7}\r\n\r\nevent: revoked\n\n".utf8)
    {
      if let event = try parser.append(byte) { events.append(event) }
    }
    #expect(events == [LiveEvent("revision", data: #"{"revision":7}"#), LiveEvent("revoked")])
    #expect(throws: NativeFailure.self) {
      for _ in 0...2 * 1024 * 1024 + 1 { _ = try parser.append(65) }
    }
  }
}
