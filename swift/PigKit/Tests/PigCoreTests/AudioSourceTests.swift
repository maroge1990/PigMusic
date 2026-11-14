import AVFoundation
import Foundation
import Testing

@testable import PigPlayback
import PigProbes

private final class MediaResponses: @unchecked Sendable {
  static let shared = MediaResponses()
  private let lock = NSLock()
  private var statuses: [Int] = []
  private var headers: [String: String] = [:]
  private var data = Data()
  private var requests: [URLRequest] = []
  private var stall = false
  func set(
    _ statuses: [Int] = [200], headers: [String: String] = ["Content-Type": "audio/mpeg"],
    data: Data = Data([1, 2, 3]), stall: Bool = false
  ) {
    lock.withLock {
      self.statuses = statuses
      self.headers = headers
      self.data = data
      self.stall = stall
      requests = []
    }
  }
  func take(_ request: URLRequest) -> (Int, [String: String], Data, Bool) {
    lock.withLock {
      requests.append(request)
      return (statuses.isEmpty ? 500 : statuses.removeFirst(), headers, data, stall)
    }
  }
  func captured() -> [URLRequest] { lock.withLock { requests } }
}
private final class MediaProtocol: URLProtocol, @unchecked Sendable {
  override class func canInit(with request: URLRequest) -> Bool { true }
  override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
  override func startLoading() {
    let (status, headers, data, stall) = MediaResponses.shared.take(request)
    client?.urlProtocol(
      self,
      didReceive: HTTPURLResponse(
        url: request.url!, statusCode: status, httpVersion: "HTTP/1.1", headerFields: headers)!,
      cacheStoragePolicy: .notAllowed)
    client?.urlProtocol(self, didLoad: data)
    if !stall { client?.urlProtocolDidFinishLoading(self) }
  }
  override func stopLoading() {}
}
private actor SourceGate {
  var entered = false
  var continuation: CheckedContinuation<Void, Never>?
  func wait() async {
    entered = true
    await withCheckedContinuation { continuation = $0 }
  }
  func open() {
    continuation?.resume()
    continuation = nil
  }
}

/// Every byte of a source, read the way the decoder reads (waiting for them).
func readAll(_ source: PreparedAudioSource) async throws -> Data {
  let bytes = try source.audioBytes()
  return try await Task.detached {
    var data = Data(count: bytes.length)
    let stop = ReadStop()
    var offset = 0
    while offset < bytes.length {
      let read = try data.withUnsafeMutableBytes { raw in
        try bytes.read(
          offset: offset, count: min(65536, bytes.length - offset),
          into: raw.baseAddress! + offset, stop: stop)
      }
      guard read > 0 else { throw AudioSourceFailure.invalidBody }
      offset += read
    }
    return data
  }.value
}

@Suite(.serialized) struct AudioSourceTests {
  let url = URL(string: "https://media.invalid/song.mp3?signature=synthetic")!
  private func preparer(_ root: URL, limit: Int = 100000, total: Int = 200000) throws
    -> AudioSourcePreparer
  {
    let configuration = URLSessionConfiguration.ephemeral
    configuration.protocolClasses = [MediaProtocol.self]
    configuration.httpAdditionalHeaders = [
      "Authorization": "Bearer must-not-escape", "Cookie": "must-not-escape",
    ]
    return try AudioSourcePreparer(
      root: root, maximumFileBytes: limit, maximumTotalBytes: total, configuration: configuration)
  }
  private func directory() throws -> URL {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    return root
  }
  private func files(_ root: URL) -> [URL] {
    (FileManager.default.enumerator(at: root, includingPropertiesForKeys: nil)?.allObjects as? [URL]
      ?? []).filter { !$0.hasDirectoryPath }
  }
  @Test func leasesStreamToDiskAndReleaseWithoutCredentials() async throws {
    let root = try directory()
    defer { try? FileManager.default.removeItem(at: root) }
    let bytes = Data(repeating: 7, count: 70000)
    MediaResponses.shared.set(
      headers: ["Content-Type": "audio/mpeg", "Content-Length": "70000"], data: bytes)
    let owner = try preparer(root)
    let source = try await owner.prepare { url }
    #expect(source.byteCount == bytes.count)
    #expect(try await readAll(source) == bytes)
    #expect(source.file.pathExtension == "mp3")
    let request = try #require(MediaResponses.shared.captured().first)
    #expect(request.value(forHTTPHeaderField: "Authorization") == nil)
    #expect(request.value(forHTTPHeaderField: "Cookie") == nil)
    await owner.release(source)
    #expect(files(root).isEmpty)
  }
  @Test func refreshesOnceForExpiredLinkAndStopsOnPersistentFailure() async throws {
    let root = try directory()
    defer { try? FileManager.default.removeItem(at: root) }
    let owner = try preparer(root)
    MediaResponses.shared.set([403, 200])
    let source = try await owner.prepare { url }
    #expect(MediaResponses.shared.captured().count == 2)
    await owner.release(source)
    MediaResponses.shared.set([401, 403, 200])
    await #expect(throws: AudioSourceFailure.unavailable) { try await owner.prepare { url } }
    #expect(MediaResponses.shared.captured().count == 2)
    #expect(files(root).isEmpty)
  }
  @Test func rejectsOversizeAndPartialResponsesAndCleansFiles() async throws {
    let root = try directory()
    defer { try? FileManager.default.removeItem(at: root) }
    let owner = try preparer(root, limit: 10, total: 20)
    for headers in [
      ["Content-Type": "audio/mpeg"], ["Content-Type": "audio/mpeg", "Content-Length": "11"],
    ] {
      MediaResponses.shared.set(headers: headers, data: Data(repeating: 1, count: 11))
      await #expect(throws: AudioSourceFailure.tooLarge) { try await owner.prepare { url } }
      #expect(files(root).isEmpty)
    }
    // A body shorter than its size starts playing, then fails when the
    // missing byte never comes.
    MediaResponses.shared.set(
      [200, 200, 200, 200, 200], headers: ["Content-Type": "audio/mpeg", "Content-Length": "4"])
    let short = try await owner.prepare { url }
    await #expect(throws: AudioSourceFailure.invalidBody) { try await readAll(short) }
    await owner.release(short)
    MediaResponses.shared.set(data: Data())
    await #expect(throws: AudioSourceFailure.invalidBody) { try await owner.prepare { url } }
    #expect(files(root).isEmpty)
  }
  @Test func refusesInsecureSourcesRedirectsAndErrorPages() async throws {
    let root = try directory()
    defer { try? FileManager.default.removeItem(at: root) }
    let owner = try preparer(root)
    for invalid in [
      "http://media.invalid/song", "https://user:password@media.invalid/song", "file:///tmp/song",
      "https://media.invalid/song#fragment",
    ] {
      MediaResponses.shared.set()
      await #expect(throws: AudioSourceFailure.invalidURL) {
        try await owner.prepare { URL(string: invalid)! }
      }
      #expect(MediaResponses.shared.captured().isEmpty)
    }
    MediaResponses.shared.set(
      [302], headers: ["Location": "https://other.invalid/song", "Content-Type": "audio/mpeg"])
    await #expect(throws: AudioSourceFailure.unavailable) { try await owner.prepare { url } }
    #expect(MediaResponses.shared.captured().count == 1)
    MediaResponses.shared.set(headers: ["Content-Type": "text/html"])
    await #expect(throws: AudioSourceFailure.invalidBody) { try await owner.prepare { url } }
  }
  @Test func storageReservationProtectsLiveLeaseAndResetRemovesIt() async throws {
    let root = try directory()
    defer { try? FileManager.default.removeItem(at: root) }
    let owner = try preparer(root, limit: 10, total: 10)
    MediaResponses.shared.set()
    let source = try await owner.prepare { url }
    await #expect(throws: AudioSourceFailure.storageLimit) { try await owner.prepare { url } }
    #expect(FileManager.default.fileExists(atPath: source.file.path))
    await owner.reset()
    #expect(files(root).isEmpty)
    MediaResponses.shared.set()
    let replacement = try await owner.prepare { url }
    await owner.release(replacement)
  }
  @Test func preparedFilesFeedBoundedEngineWithoutRetainingWholeSongs() async throws {
    let root = try directory()
    defer { try? FileManager.default.removeItem(at: root) }
    let fixture = root.appendingPathComponent("fixture.wav")
    let format = AVAudioFormat(standardFormatWithSampleRate: 48000, channels: 2)!
    let pcm = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 4800)!
    pcm.frameLength = 4800
    for channel in 0..<2 { pcm.floatChannelData![channel].update(repeating: 0.25, count: 4800) }
    do {
      let file = try AVAudioFile(forWriting: fixture, settings: format.settings)
      try file.write(from: pcm)
    }
    let encoded = try Data(contentsOf: fixture)
    let owner = try preparer(root)
    let waveURL = URL(
      string: "https://media.invalid/api/v1/playback/files/track-1?signature=synthetic")!
    MediaResponses.shared.set(headers: ["Content-Type": "audio/wav"], data: encoded)
    let first = try await owner.prepare { waveURL }
    MediaResponses.shared.set(headers: ["Content-Type": "audio/wav"], data: encoded)
    let second = try await owner.prepare { waveURL }
    #expect(first.file.pathExtension == "wav")
    let result = try await BoundedAudioEngineProbe.render(
      first: first.file, second: second.file,
      output: root.appendingPathComponent("capture.wav"), crossfadeSeconds: 0.025)
    #expect(result.frames == 8400)
    #expect(result.overlapFrames == 1200)
    #expect(result.peakScheduledFrames <= 8192)
    await owner.release(first)
    await owner.release(second)
    #expect(!FileManager.default.fileExists(atPath: first.file.path))
    #expect(!FileManager.default.fileExists(atPath: second.file.path))
  }
  @Test func cancellationDuringBodyTransferRemovesPartialFile() async throws {
    let root = try directory()
    defer { try? FileManager.default.removeItem(at: root) }
    let owner = try preparer(root)
    MediaResponses.shared.set(data: Data(repeating: 1, count: 65536), stall: true)
    let task = Task { try await owner.prepare { url } }
    let deadline = ContinuousClock.now + .seconds(5)
    while !files(root).contains(where: { $0.pathExtension == "partial" }) {
      guard ContinuousClock.now < deadline else {
        task.cancel()
        throw AudioSourceFailure.unavailable
      }
      try await Task.sleep(for: .milliseconds(10))
    }
    task.cancel()
    switch await task.result {
    case .success: Issue.record("Cancelled body transfer published a source")
    case .failure(let error):
      #expect(error is CancellationError || (error as? URLError)?.code == .cancelled)
    }
    #expect(files(root).isEmpty)
  }
  @Test func cancellationAndResetPreventStalePublication() async throws {
    let root = try directory()
    defer { try? FileManager.default.removeItem(at: root) }
    let owner = try preparer(root)
    for reset in [false, true] {
      MediaResponses.shared.set()
      let gate = SourceGate()
      let task = Task {
        try await owner.prepare {
          await gate.wait()
          return url
        }
      }
      while !(await gate.entered) { await Task.yield() }
      await #expect(throws: AudioSourceFailure.busy) { try await owner.prepare { url } }
      if reset { await owner.reset() } else { task.cancel() }
      await gate.open()
      await #expect(throws: CancellationError.self) { try await task.value }
      #expect(MediaResponses.shared.captured().isEmpty)
      #expect(files(root).isEmpty)
    }
  }
}
