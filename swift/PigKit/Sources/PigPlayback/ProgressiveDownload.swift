import Foundation
import PigDomain

/// The bytes of one audio file, for the decoder. Reads may wait for bytes
/// still downloading, so call them only from a decoder's own queue.
public protocol AudioBytes: AnyObject, Sendable {
  /// The whole file's size in bytes.
  var length: Int { get }
  /// Copies the bytes at `offset` (fewer only at the end of the file), waiting
  /// until they have arrived. Throws when `stop` says the reader has gone, or
  /// when the bytes cannot arrive.
  func read(offset: Int, count: Int, into buffer: UnsafeMutableRawPointer, stop: ReadStop)
    throws -> Int
  /// Wakes readers waiting on this source, so they notice their `ReadStop`.
  func wake()
  /// Every byte has arrived: no read waits.
  var complete: Bool { get }
}
extension AudioBytes {
  public var complete: Bool { false }
}

/// Tells a waiting read its reader has gone (a skip, seek or stop).
public final class ReadStop: @unchecked Sendable {
  private let lock = NSLock()
  private var value = false
  public init() {}
  public var stopped: Bool { lock.withLock { value } }
  public func stop() { lock.withLock { value = true } }
}

/// A complete file on this device: reads never wait.
public final class LocalAudioBytes: AudioBytes, @unchecked Sendable {
  public let length: Int
  private let descriptor: Int32
  public init(file: URL) throws {
    guard file.isFileURL else { throw AudioSourceFailure.invalidURL }
    descriptor = open(file.path, O_RDONLY)
    guard descriptor >= 0 else { throw AudioSourceFailure.unavailable }
    var status = stat()
    guard fstat(descriptor, &status) == 0, status.st_size > 0 else {
      close(descriptor)
      throw AudioSourceFailure.unavailable
    }
    length = Int(status.st_size)
  }
  deinit { close(descriptor) }
  public func read(offset: Int, count: Int, into buffer: UnsafeMutableRawPointer, stop: ReadStop)
    throws -> Int
  {
    guard offset >= 0, count > 0, offset < length else { return 0 }
    let result = pread(descriptor, buffer, min(count, length - offset), off_t(offset))
    guard result >= 0 else { throw AudioSourceFailure.unavailable }
    return result
  }
  public func wake() {}
  public var complete: Bool { true }
}

/// Byte ranges held, sorted and merged.
struct ByteRanges {
  private(set) var ranges: [Range<Int>] = []
  mutating func insert(_ range: Range<Int>) {
    guard !range.isEmpty else { return }
    var merged = range
    var kept: [Range<Int>] = []
    kept.reserveCapacity(ranges.count + 1)
    for existing in ranges {
      if existing.upperBound < merged.lowerBound || existing.lowerBound > merged.upperBound {
        kept.append(existing)
      } else {
        merged =
          min(
            existing.lowerBound, merged.lowerBound)..<max(
            existing.upperBound, merged.upperBound)
      }
    }
    kept.append(merged)
    kept.sort { $0.lowerBound < $1.lowerBound }
    ranges = kept
  }
  func contains(_ range: Range<Int>) -> Bool {
    range.isEmpty
      || ranges.contains { $0.lowerBound <= range.lowerBound && $0.upperBound >= range.upperBound }
  }
  /// The first byte at or after `offset` (below `length`) not held yet.
  func gap(from offset: Int, length: Int) -> Int? {
    var position = max(0, offset)
    for range in ranges where range.upperBound > position {
      if range.lowerBound > position { break }
      position = range.upperBound
    }
    return position < length ? position : nil
  }
  /// Where the gap starting at `start` ends.
  func gapEnd(_ start: Int, length: Int) -> Int {
    ranges.first { $0.lowerBound > start }?.lowerBound ?? length
  }
}

/// A remote audio file fetched into a sparse local file while it plays. The
/// decoder may read any part: missing bytes are fetched next, from where it
/// reads (a seek, or an index stored at the end of the file), then the rest.
/// A following song's file waits for this one beyond its opening bytes, so the
/// playing song gets the connection.
public final class ProgressiveDownload: AudioBytes, @unchecked Sendable {
  public enum Phase: Sendable { case loading, complete, failed, closed }
  /// Bytes fetched before deferring to the song ahead: enough to open the
  /// file and decode its first seconds, so a transition never waits.
  static let openingBytes = 512 * 1024
  /// How far ahead of the transfer a read may be before the transfer jumps.
  static let jumpWindow = 1024 * 1024
  public let file: URL
  public let mimeType: String
  /// Each range request starts on the current session, so a song resumed
  /// after the connections were renewed fetches on the new ones.
  private let sessions: RenewableSession
  private let resolve: @Sendable () async throws -> URL
  private let limit: Int
  private let readTimeout: TimeInterval
  private weak var predecessor: ProgressiveDownload?
  private let condition = NSCondition()
  private let descriptor: Int32
  private var held = ByteRanges()
  private var total = 0
  private var wanted = 0
  private var waiting = 0
  private var state = Phase.loading
  private var failure: Error?
  private var fetching: Task<Void, Never>?
  /// No reader holds this file (a song skipped past, kept for going back):
  /// it fetches only while a reader waits.
  private var idle = false
  /// When the response to the first request arrived.
  public private(set) var firstResponse: Duration?
  /// The first request's ID and the server's timing of it, for diagnostics.
  public private(set) var firstRequestID: String?
  public private(set) var firstServerTiming: String?

  private init(
    file: URL, mimeType: String, length: Int, sessions: RenewableSession,
    resolve: @escaping @Sendable () async throws -> URL, limit: Int, readTimeout: TimeInterval,
    predecessor: ProgressiveDownload?, complete: Bool = false
  ) throws {
    self.file = file
    self.mimeType = mimeType
    total = length
    self.sessions = sessions
    self.resolve = resolve
    self.limit = limit
    self.readTimeout = readTimeout
    self.predecessor = predecessor
    if complete {
      held.insert(0..<length)
      state = .complete
    } else if !FileManager.default.createFile(atPath: file.path, contents: nil) {
      throw AudioSourceFailure.storageLimit
    }
    descriptor = open(file.path, O_RDWR)
    guard descriptor >= 0 else {
      try? FileManager.default.removeItem(at: file)
      throw AudioSourceFailure.storageLimit
    }
  }
  deinit {
    fetching?.cancel()
    Darwin.close(descriptor)
    try? FileManager.default.removeItem(at: file)
  }

  public var length: Int { condition.withLock { total } }
  public var phase: Phase { condition.withLock { state } }
  /// Still fetching bytes for a reader (a following song's file waits for this).
  var busy: Bool { condition.withLock { state == .loading && (!idle || waiting > 0) } }
  /// Whether any reader holds this file; idle files stop fetching.
  public func setIdle(_ value: Bool) {
    condition.withLock {
      idle = value
      condition.broadcast()
    }
  }

  /// Starts fetching and returns once the first response shows a playable
  /// file. The body keeps arriving afterwards.
  static func start(
    url: URL, directory: URL, sessions: RenewableSession, limit: Int,
    readTimeout: TimeInterval = 30,
    predecessor: ProgressiveDownload?, resolve: @escaping @Sendable () async throws -> URL
  ) async throws -> ProgressiveDownload {
    let started = ContinuousClock.now
    let first = try await Request.open(sessions: sessions, url: url, from: 0, to: nil)
    do {
      let response = try first.validated(limit: limit, start: 0)
      if response.total < 0 {
        return try await whole(
          first, url: url, mime: response.mime, directory: directory, sessions: sessions,
          limit: limit, readTimeout: readTimeout, resolve: resolve, started: started)
      }
      let download = try ProgressiveDownload(
        file: AudioSourcePreparer.file(in: directory, mime: response.mime, url: url),
        mimeType: response.mime, length: response.total, sessions: sessions,
        resolve: resolve, limit: limit, readTimeout: readTimeout, predecessor: predecessor)
      download.firstResponse = ContinuousClock.now - started
      download.noteFirst(first)
      download.fetching = Task.detached { [weak download] in
        await download?.run(first: first, url: url)
      }
      return download
    } catch {
      first.cancel()
      throw error
    }
  }

  /// Without a size the file cannot be read before it has all arrived, so it
  /// is fetched whole first.
  private static func whole(
    _ first: Request, url: URL, mime: String, directory: URL, sessions: RenewableSession,
    limit: Int,
    readTimeout: TimeInterval, resolve: @escaping @Sendable () async throws -> URL,
    started: ContinuousClock.Instant
  ) async throws -> ProgressiveDownload {
    let file = AudioSourcePreparer.file(in: directory, mime: mime, url: url)
    let partial = file.appendingPathExtension("partial")
    guard FileManager.default.createFile(atPath: partial.path, contents: nil) else {
      throw AudioSourceFailure.storageLimit
    }
    defer { try? FileManager.default.removeItem(at: partial) }
    let handle = try FileHandle(forWritingTo: partial)
    defer { try? handle.close() }
    var count = 0
    for try await chunk in first.chunks {
      try Task.checkCancellation()
      guard chunk.count <= limit - count else { throw AudioSourceFailure.tooLarge }
      count += chunk.count
      try handle.write(contentsOf: chunk)
    }
    try Task.checkCancellation()
    guard count > 0 else { throw AudioSourceFailure.invalidBody }
    try handle.close()
    try FileManager.default.moveItem(at: partial, to: file)
    let download = try ProgressiveDownload(
      file: file, mimeType: mime, length: count, sessions: sessions, resolve: resolve,
      limit: limit, readTimeout: readTimeout, predecessor: nil, complete: true)
    download.firstResponse = ContinuousClock.now - started
    download.noteFirst(first)
    return download
  }
  private func noteFirst(_ request: Request) {
    firstRequestID = request.requestID
    firstServerTiming = request.response.value(forHTTPHeaderField: "Server-Timing")
  }

  public func read(offset: Int, count: Int, into buffer: UnsafeMutableRawPointer, stop: ReadStop)
    throws -> Int
  {
    condition.lock()
    let end = min(offset + count, total)
    guard offset >= 0, count > 0, offset < end else {
      condition.unlock()
      return 0
    }
    let deadline = Date(timeIntervalSinceNow: readTimeout)
    while !held.contains(offset..<end) {
      if stop.stopped || state == .closed {
        condition.unlock()
        throw CancellationError()
      }
      if let failure {
        condition.unlock()
        throw failure
      }
      wanted = offset
      waiting += 1
      let woke = condition.wait(until: deadline)
      waiting -= 1
      if !woke, !held.contains(offset..<end) {
        condition.unlock()
        throw AudioPipelineFailure.stalled
      }
    }
    condition.unlock()
    let result = pread(descriptor, buffer, end - offset, off_t(offset))
    guard result >= 0 else { throw AudioSourceFailure.unavailable }
    return result
  }
  public func wake() { condition.withLock { condition.broadcast() } }
  public var complete: Bool { condition.withLock { held.contains(0..<total) } }

  /// Stops fetching and deletes the file once nothing reads it.
  public func close() {
    condition.withLock {
      if state == .loading { state = .closed }
      condition.broadcast()
    }
    fetching?.cancel()
  }

  // MARK: Fetching

  private enum Next {
    case fetch(Int, Int)
    case wait
    case done
  }
  private func next() -> Next {
    condition.withLock {
      guard state == .loading else { return .done }
      let start = held.gap(from: wanted, length: total) ?? held.gap(from: 0, length: total)
      guard let start else {
        state = .complete
        condition.broadcast()
        return .done
      }
      if idle, waiting == 0 { return .wait }
      // Beyond its opening, a following song's file waits for the one ahead,
      // unless its own reader is already waiting.
      if start >= Self.openingBytes, waiting == 0, predecessor?.busy == true { return .wait }
      return .fetch(start, held.gapEnd(start, length: total))
    }
  }
  /// Whether the transfer at `position` should give way: the reader waits for
  /// bytes elsewhere, or the song ahead should have the connection.
  private func shouldYield(from start: Int, at position: Int) -> Bool {
    condition.withLock {
      if state != .loading { return true }
      if waiting > 0, !held.contains(wanted..<min(wanted + 1, total)),
        wanted < start || wanted > position + Self.jumpWindow
      {
        return true
      }
      if waiting == 0, idle { return true }
      return position >= Self.openingBytes && waiting == 0 && predecessor?.busy == true
    }
  }
  private func store(_ data: Data, at position: Int) -> Bool {
    let written = data.withUnsafeBytes { raw in
      pwrite(descriptor, raw.baseAddress, data.count, off_t(position))
    }
    guard written == data.count else { return false }
    condition.withLock {
      held.insert(position..<position + data.count)
      if state == .loading, held.contains(0..<total) { state = .complete }
      condition.broadcast()
    }
    return true
  }
  private var heldBytes: Int {
    condition.withLock { held.ranges.reduce(0) { $0 + $1.count } }
  }
  private func fail(_ error: Error) {
    condition.withLock {
      if state == .loading {
        state = .failed
        failure = error
      }
      condition.broadcast()
    }
  }

  private func run(first: Request, url initial: URL) async {
    var url = initial
    var pending: Request? = first
    var refreshed = false
    var failures = 0
    while !Task.isCancelled {
      let request: Request
      let start: Int
      if let first = pending {
        pending = nil
        request = first
        start = 0
      } else {
        switch next() {
        case .done: return
        case .wait:
          try? await Task.sleep(for: .milliseconds(250))
          continue
        case .fetch(let from, let to):
          do {
            request = try await Request.open(sessions: sessions, url: url, from: from, to: to)
            _ = try request.validated(limit: limit, start: from, expected: total)
          } catch AudioSourceFailure.unauthorized where !refreshed {
            refreshed = true
            if let renewed = try? await resolve() { url = renewed }
            continue
          } catch is CancellationError {
            return
          } catch {
            failures += 1
            if failures > 3 { return fail(error) }
            try? await Task.sleep(for: .milliseconds(500 * failures))
            continue
          }
          start = request.offset ?? from
        }
      }
      // A server ignoring the range resends the file from its start.
      var position = request.offset ?? 0
      let before = heldBytes
      do {
        // However this transfer ends, its HTTP request ends with it.
        defer { request.cancel() }
        for try await chunk in request.chunks {
          guard store(chunk, at: position) else { throw AudioSourceFailure.storageLimit }
          position += chunk.count
          if position > total { throw AudioSourceFailure.invalidBody }
          if shouldYield(from: start, at: position) {
            request.cancel()
            break
          }
        }
        // A body that ends early brings nothing new: give up after a few.
        if heldBytes == before, phase == .loading {
          failures += 1
          if failures > 3 { return fail(AudioSourceFailure.invalidBody) }
        } else {
          failures = 0
        }
      } catch is CancellationError where Task.isCancelled {
        return
      } catch AudioSourceFailure.storageLimit {
        return fail(AudioSourceFailure.storageLimit)
      } catch {
        failures += 1
        if failures > 3 { return fail(error) }
        try? await Task.sleep(for: .milliseconds(500 * failures))
      }
    }
  }
}

/// One HTTP request whose body arrives as chunks. The request lives only as
/// long as its reader: a cancelled or abandoned reader, or the last
/// reference going, cancels the data task.
final class Request: @unchecked Sendable {
  let response: HTTPURLResponse
  /// Sent as `X-Request-ID`; the server's log names the request by it.
  let requestID: String
  let chunks: AsyncThrowingStream<Data, Error>
  private let task: URLSessionDataTask
  private let continuation: AsyncThrowingStream<Data, Error>.Continuation

  fileprivate init(
    response: HTTPURLResponse, requestID: String, task: URLSessionDataTask,
    chunks: AsyncThrowingStream<Data, Error>,
    continuation: AsyncThrowingStream<Data, Error>.Continuation
  ) {
    self.response = response
    self.requestID = requestID
    self.task = task
    self.chunks = chunks
    self.continuation = continuation
    continuation.onTermination = { [task] _ in task.cancel() }
  }
  deinit { task.cancel() }
  func cancel() {
    task.cancel()
    continuation.finish()
  }

  /// Where the body starts in the file: the range's start, or nil for a
  /// whole-file answer.
  var offset: Int? {
    guard response.statusCode == 206, let range = contentRange else { return nil }
    return range.start
  }
  private var contentRange: (start: Int, total: Int)? {
    guard let value = response.value(forHTTPHeaderField: "Content-Range"),
      let match = value.wholeMatch(of: /bytes (\d+)-(\d+)\/(\d+)/),
      let start = Int(match.1), let total = Int(match.3)
    else { return nil }
    return (start, total)
  }

  /// Checks the answer is a playable file and returns its media type and size.
  func validated(limit: Int, start: Int, expected: Int? = nil) throws -> (
    mime: String, total: Int
  ) {
    if [401, 403].contains(response.statusCode) { throw AudioSourceFailure.unauthorized }
    let mime = response.mimeType?.lowercased() ?? ""
    let total: Int
    switch response.statusCode {
    case 206:
      guard let range = contentRange, range.start == start else {
        throw AudioSourceFailure.invalidBody
      }
      total = range.total
    case 200:
      total = Int(response.expectedContentLength)
    default:
      throw AudioSourceFailure.unavailable
    }
    guard mime.hasPrefix("audio/") || mime == "application/octet-stream" else {
      throw AudioSourceFailure.invalidBody
    }
    // Without a size (-1) the file is fetched whole before it plays.
    guard total > 0 || (response.statusCode == 200 && total == -1 && expected == nil) else {
      throw AudioSourceFailure.invalidBody
    }
    guard total <= limit else { throw AudioSourceFailure.tooLarge }
    if let expected, total != expected { throw AudioSourceFailure.invalidBody }
    return (mime, total)
  }

  /// Sends the request and returns once its response has arrived.
  static func open(sessions: RenewableSession, url: URL, from start: Int, to end: Int?)
    async throws -> Request
  {
    var request = URLRequest(url: url)
    request.setValue("audio/*,application/octet-stream", forHTTPHeaderField: "Accept")
    request.setValue(
      "bytes=\(start)-" + (end.map { String($0 - 1) } ?? ""), forHTTPHeaderField: "Range")
    let requestID = ServerTiming.newRequestID()
    request.setValue(requestID, forHTTPHeaderField: "X-Request-ID")
    let receiver = Receiver(requestID: requestID)
    return try await withTaskCancellationHandler {
      try await withCheckedThrowingContinuation { (opened: CheckedContinuation<Request, Error>) in
        // A request cancelled before it starts is never sent.
        guard receiver.setUp(opened: opened) else {
          return opened.resume(throwing: CancellationError())
        }
        receiver.started(sessions.dataTask(with: request, delegate: receiver))
      }
    } onCancel: {
      receiver.cancel()
    }
  }
}

/// Delivers one task's callbacks: the response opens the request, then data.
private final class Receiver: NSObject, URLSessionDataDelegate, @unchecked Sendable {
  private let lock = NSLock()
  private var opened: CheckedContinuation<Request, Error>?
  private var continuation: AsyncThrowingStream<Data, Error>.Continuation?
  private weak var task: URLSessionDataTask?
  private var cancelled = false
  private let requestID: String
  init(requestID: String) { self.requestID = requestID }

  /// Whether to start: false once cancelled.
  func setUp(opened: CheckedContinuation<Request, Error>) -> Bool {
    lock.withLock {
      guard !cancelled else { return false }
      self.opened = opened
      return true
    }
  }
  /// The task now running; one cancelled before it started stops at once.
  func started(_ task: URLSessionDataTask) {
    let stop = lock.withLock {
      self.task = task
      return cancelled
    }
    if stop { task.cancel() }
  }
  func cancel() {
    let running = lock.withLock {
      cancelled = true
      return task
    }
    running?.cancel()
  }
  func urlSession(
    _ session: URLSession, task: URLSessionTask,
    willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest,
    completionHandler: @escaping @Sendable (URLRequest?) -> Void
  ) { completionHandler(nil) }
  func urlSession(
    _ session: URLSession, dataTask: URLSessionDataTask, didReceive response: URLResponse,
    completionHandler: @escaping @Sendable (URLSession.ResponseDisposition) -> Void
  ) {
    guard let http = response as? HTTPURLResponse else {
      finish(AudioSourceFailure.unavailable)
      completionHandler(.cancel)
      return
    }
    let (stream, continuation) = AsyncThrowingStream<Data, Error>.makeStream(
      bufferingPolicy: .unbounded)
    let request = Request(
      response: http, requestID: requestID, task: dataTask, chunks: stream,
      continuation: continuation)
    let waiting = lock.withLock {
      self.continuation = continuation
      defer { opened = nil }
      return opened
    }
    waiting?.resume(returning: request)
    completionHandler(.allow)
  }
  func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
    _ = lock.withLock { continuation }?.yield(data)
  }
  func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
    finish(error)
  }
  private func finish(_ error: Error?) {
    let (waiting, stream) = lock.withLock {
      defer {
        opened = nil
        continuation = nil
      }
      return (opened, continuation)
    }
    let failure: Error? =
      (error as? URLError)?.code == .cancelled ? CancellationError() : error
    waiting?.resume(throwing: failure ?? AudioSourceFailure.unavailable)
    if let failure { stream?.finish(throwing: failure) } else { stream?.finish() }
  }
}

extension Duration {
  var milliseconds: Int {
    let parts = components
    return Int(parts.seconds) * 1000 + Int(parts.attoseconds / 1_000_000_000_000_000)
  }
}
