import Foundation
import PigDomain
import PigDownloads
import Testing

/// Answers each song by a fixed state; "down" fails like a lost network.
private actor ServerCopies: DeviceCopySource {
  var calls: [String] = []
  let states: [String: String]
  init(_ states: [String: String]) { self.states = states }
  func deviceCopy(trackId: String, quality: String, create: Bool) async throws -> DeviceCopy {
    calls.append((create ? "create " : "poll ") + trackId)
    guard let state = states[trackId], state != "down" else { throw URLError(.notConnectedToInternet) }
    let json: [String: Any] = [
      "state": state, "quality": quality, "sha256": String(repeating: "a", count: 64),
      "source_sha256": String(repeating: "b", count: 64), "bytes": 10, "extension": "m4a",
      "key": trackId,
    ]
    return try LibraryCodec.decoder().decode(
      DeviceCopy.self, from: JSONSerialization.data(withJSONObject: json))
  }
  func copyRequest(trackId: String, quality: String) async throws -> URLRequest {
    URLRequest(url: URL(string: "https://server.invalid/" + trackId)!)
  }
}

/// Holds every request until the test opens it, as a slow network would.
private actor HeldCopies: DeviceCopySource {
  let inner: ServerCopies
  var gate: CheckedContinuation<Void, Never>?
  var asked = 0
  init(_ inner: ServerCopies) { self.inner = inner }
  func open() {
    gate?.resume()
    gate = nil
  }
  var waiting: Bool { gate != nil }
  func deviceCopy(trackId: String, quality: String, create: Bool) async throws -> DeviceCopy {
    asked += 1
    await withCheckedContinuation { gate = $0 }
    return try await inner.deviceCopy(trackId: trackId, quality: quality, create: create)
  }
  func copyRequest(trackId: String, quality: String) async throws -> URLRequest {
    try await inner.copyRequest(trackId: trackId, quality: quality)
  }
}
@MainActor private final class Switch {
  var on = false
  var handed: [String] = []
}

/// #219: Watch copies were prepared only by the foreground worker, so a
/// locked phone stopped sending songs to the Watch.
@MainActor struct BackgroundHandOffTests {
  private func record(_ id: String, state: String = "queued", paused: Bool = false) throws
    -> DownloadRecord
  {
    let track = try LibraryCodec.decoder().decode(
      Track.self, from: Data(#"{"id":"\#(id)","title":"\#(id)","artist":"Artist"}"#.utf8))
    var record = DownloadRecord(track: track, quality: "aac")
    record.state = state
    record.paused = paused ? true : nil
    return record
  }

  @Test func readyCopiesGoToTheBackgroundAndPreparingOnesWait() async throws {
    let source = ServerCopies(["a": "ready", "b": "preparing", "c": "ready", "d": "ready"])
    let records = [
      try record("a"), try record("b"), try record("c", paused: true),
      try record("d", state: "downloading"), try record("e", state: "ready"),
    ]
    var handed: [String] = []
    let outcome = await BackgroundHandOff.run(
      records, source: source, limit: 10, isCancelled: { false }
    ) { record, copy, request in
      handed.append(record.id)
      #expect(request.url?.lastPathComponent == record.id)
      #expect(copy.state == "ready")
    }
    #expect(handed == ["a"])
    #expect(outcome.handed == ["a"])
    #expect(outcome.waiting == ["b"])  // Asked for, so the server prepares it meanwhile.
    #expect(!outcome.stopped)
    // The paused song and those already moving are never asked for.
    #expect(await source.calls == ["create a", "create b"])
  }

  /// Codex round 1 on batch 14: cancellation was checked only before each
  /// request, so a Pause, the foreground or an account change while one was
  /// out still started a background download when it came back.
  @Test func nothingIsEnqueuedWhenCancelledWhileARequestIsOut() async throws {
    let source = HeldCopies(ServerCopies(["a": "ready", "b": "ready"]))
    let cancelled = Switch()
    let records = [try record("a"), try record("b")]
    let pass = Task { @MainActor in
      await BackgroundHandOff.run(
        records, source: source, limit: 10, isCancelled: { cancelled.on }
      ) { record, _, _ in cancelled.handed.append(record.id) }
    }
    while await !source.waiting { await Task.yield() }
    cancelled.on = true  // The person's Pause, say, while "a" is being asked for.
    await source.open()
    let outcome = await pass.value
    #expect(cancelled.handed.isEmpty)
    #expect(outcome.handed.isEmpty)
    #expect(outcome.waiting == ["a", "b"])
    #expect(outcome.stopped)
    #expect(await source.asked == 1)
  }

  @Test func aFailureOrTheLimitLeavesTheRestQueued() async throws {
    let source = ServerCopies(["a": "ready", "b": "down", "c": "ready"])
    let records = [try record("a"), try record("b"), try record("c")]
    let failed = await BackgroundHandOff.run(
      records, source: source, limit: 10, isCancelled: { false }
    ) { _, _, _ in }
    #expect(failed.handed == ["a"])
    #expect(failed.waiting == ["b", "c"])
    #expect(failed.stopped)
    let limited = await BackgroundHandOff.run(
      [try record("a"), try record("c")], source: ServerCopies(["a": "ready", "c": "ready"]),
      limit: 1, isCancelled: { false }
    ) { _, _, _ in }
    #expect(limited.handed == ["a"])
    #expect(limited.waiting == ["c"])
    #expect(!limited.stopped)
    // Back in the foreground: the worker takes over before anything is asked.
    let cancelled = await BackgroundHandOff.run(
      [try record("a")], source: source, limit: 10, isCancelled: { true }
    ) { _, _, _ in }
    #expect(cancelled.handed.isEmpty && cancelled.waiting == ["a"] && cancelled.stopped)
  }
}
