import Foundation
import PigDomain

public enum AudioSourceFailure: Error, LocalizedError, Equatable {
  case invalidURL, busy, storageLimit, tooLarge, unavailable, invalidBody, unauthorized
  public var errorDescription: String? {
    switch self {
    case .invalidURL: "The audio source is not a secure media URL."
    case .busy: "Another audio source is being prepared."
    case .storageLimit: "Release an earlier playback source before preparing another song."
    case .tooLarge: "This song exceeds the temporary playback storage limit."
    case .unavailable: "The audio source is unavailable. Try again or play a saved song."
    case .invalidBody: "The source did not return an audio file."
    case .unauthorized: "The playback link expired. Try again."
    }
  }
}

/// An encoded file lease, not a durable offline download or a guarantee of codec support.
public struct PreparedAudioSource: Sendable {
  public let id: UUID
  public let file: URL
  public let byteCount: Int
  /// The file's bytes while they are still arriving; nil for a complete file.
  public let bytes: (any AudioBytes)?
  public init(id: UUID = UUID(), file: URL, byteCount: Int, bytes: (any AudioBytes)? = nil) {
    self.id = id
    self.file = file
    self.byteCount = byteCount
    self.bytes = bytes
  }
  /// What the decoder reads: the arriving bytes, else the complete file.
  func audioBytes() throws -> any AudioBytes { try bytes ?? LocalAudioBytes(file: file) }
}

private final class NoMediaRedirects: NSObject, URLSessionTaskDelegate, Sendable {
  func urlSession(
    _ session: URLSession, task: URLSessionTask,
    willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest,
    completionHandler: @escaping @Sendable (URLRequest?) -> Void
  ) { completionHandler(nil) }
}

/// One transfer at a time, with explicit leases for the current and upcoming songs.
/// The resolver owns authentication; media requests never receive a device bearer token.
public actor AudioSourcePreparer {
  private let directory: URL
  private let sessions: RenewableSession
  private let maximumFileBytes: Int
  private let maximumTotalBytes: Int
  private var leases: [UUID: PreparedAudioSource] = [:]
  private var transfer: Task<ProgressiveDownload, Error>?
  /// The latest song fetched: the next one waits for it beyond its opening.
  private weak var lastDownload: ProgressiveDownload?
  private var generation = 0

  public init(
    root: URL, maximumFileBytes: Int = 128 * 1024 * 1024,
    maximumTotalBytes: Int = 256 * 1024 * 1024,
    configuration: URLSessionConfiguration = .ephemeral
  ) throws {
    guard root.isFileURL, maximumFileBytes > 0, maximumTotalBytes >= maximumFileBytes else {
      throw AudioSourceFailure.storageLimit
    }
    self.maximumFileBytes = maximumFileBytes
    self.maximumTotalBytes = maximumTotalBytes
    directory = root.appendingPathComponent("playback-" + UUID().uuidString, isDirectory: true)
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    configuration.urlCache = nil
    configuration.urlCredentialStorage = nil
    configuration.httpAdditionalHeaders = nil
    configuration.httpCookieStorage = nil
    configuration.httpShouldSetCookies = false
    configuration.timeoutIntervalForRequest = 20
    configuration.timeoutIntervalForResource = 120
    let settings = configuration.copy() as! URLSessionConfiguration
    sessions = RenewableSession {
      URLSession(configuration: settings, delegate: NoMediaRedirects(), delegateQueue: nil)
    }
  }
  /// New connections for the next transfers, after the app was away; songs
  /// already arriving carry on.
  public nonisolated func renewConnections() { sessions.renew() }

  deinit {
    transfer?.cancel()
    for source in leases.values { (source.bytes as? ProgressiveDownload)?.close() }
    try? FileManager.default.removeItem(at: directory)
  }

  /// Starts fetching a song and returns once its first response shows a
  /// playable file; the rest arrives while it plays (`PreparedAudioSource.bytes`).
  public func prepare(
    resolve: @escaping @Sendable () async throws -> URL
  ) async throws -> PreparedAudioSource {
    try Task.checkCancellation()
    guard transfer == nil else { throw AudioSourceFailure.busy }
    let retained = leases.values.reduce(0) { $0 + $1.byteCount }
    guard retained <= maximumTotalBytes - maximumFileBytes else {
      throw AudioSourceFailure.storageLimit
    }
    let stamp = generation
    let directory = directory
    let session = sessions.session
    let limit = maximumFileBytes
    let predecessor = lastDownload?.busy == true ? lastDownload : nil
    let task = Task {
      // A signed link can expire between resolution and its first request. Refresh once only.
      for attempt in 0..<2 {
        try Task.checkCancellation()
        let url = try await resolve()
        guard url.scheme?.lowercased() == "https", url.host != nil,
          url.user == nil, url.password == nil, url.fragment == nil
        else {
          throw AudioSourceFailure.invalidURL
        }
        do {
          return try await ProgressiveDownload.start(
            url: url, directory: directory, session: session, limit: limit,
            predecessor: predecessor, resolve: resolve)
        } catch AudioSourceFailure.unauthorized where attempt == 0 {
          continue
        } catch AudioSourceFailure.unauthorized {
          throw AudioSourceFailure.unavailable
        }
      }
      throw AudioSourceFailure.unavailable
    }
    transfer = task
    defer { transfer = nil }
    let download = try await withTaskCancellationHandler {
      try await task.value
    } onCancel: {
      task.cancel()
    }
    guard !Task.isCancelled, generation == stamp else {
      download.close()
      throw CancellationError()
    }
    lastDownload = download
    if let delay = download.firstResponse {
      DiagnosticsLog.shared.record(
        "playback",
        "source answered in \(delay.milliseconds) ms"
          + " (\(download.mimeType), \(download.length / 1024) KB)")
    }
    let result = PreparedAudioSource(
      file: download.file, byteCount: download.length, bytes: download)
    leases[result.id] = result
    return result
  }

  /// The file name for a media type, so the decoder knows the format.
  static func file(in directory: URL, mime: String, url: URL) -> URL {
    let allowedExtensions = ["wav", "aiff", "aif", "m4a", "mp3", "flac", "ogg", "opus", "aac"]
    let mimeExtensions = [
      "audio/wav": "wav", "audio/x-wav": "wav", "audio/wave": "wav",
      "audio/aiff": "aiff", "audio/x-aiff": "aiff", "audio/mpeg": "mp3",
      "audio/mp4": "m4a", "audio/x-m4a": "m4a", "audio/aac": "aac",
      "audio/flac": "flac", "audio/x-flac": "flac", "audio/ogg": "ogg",
      "audio/opus": "opus",
    ]
    let suffix = mimeExtensions[mime] ?? url.pathExtension.lowercased()
    return directory.appendingPathComponent(UUID().uuidString)
      .appendingPathExtension(allowedExtensions.contains(suffix) ? suffix : "media")
  }

  /// Stage a trusted local file (for the synthetic demo or a future offline repository).
  /// Remote source resolution still goes through the HTTPS-only prepare operation.
  public func stageLocalFile(_ source: URL) throws -> PreparedAudioSource {
    try Task.checkCancellation()
    guard source.isFileURL else { throw AudioSourceFailure.invalidURL }
    guard transfer == nil else { throw AudioSourceFailure.busy }
    let retained = leases.values.reduce(0) { $0 + $1.byteCount }
    guard retained <= maximumTotalBytes - maximumFileBytes else {
      throw AudioSourceFailure.storageLimit
    }
    let input = try FileHandle(forReadingFrom: source)
    defer { try? input.close() }
    let id = UUID()
    let extensions = ["wav", "aiff", "aif", "m4a", "mp3", "flac", "ogg", "opus", "aac"]
    let suffix = source.pathExtension.lowercased()
    let destination = directory.appendingPathComponent(id.uuidString)
      .appendingPathExtension(extensions.contains(suffix) ? suffix : "media")
    let partial = destination.appendingPathExtension("partial")
    guard FileManager.default.createFile(atPath: partial.path, contents: nil) else {
      throw AudioSourceFailure.storageLimit
    }
    defer { try? FileManager.default.removeItem(at: partial) }
    let handle = try FileHandle(forWritingTo: partial)
    defer { try? handle.close() }
    var count = 0
    while let chunk = try input.read(upToCount: 65536), !chunk.isEmpty {
      try Task.checkCancellation()
      guard chunk.count <= maximumFileBytes - count else { throw AudioSourceFailure.tooLarge }
      count += chunk.count
      try handle.write(contentsOf: chunk)
    }
    guard count > 0 else { throw AudioSourceFailure.invalidBody }
    try Task.checkCancellation()
    try handle.close()
    try FileManager.default.moveItem(at: partial, to: destination)
    let result = PreparedAudioSource(id: id, file: destination, byteCount: count)
    leases[id] = result
    return result
  }

  public func release(_ source: PreparedAudioSource) {
    guard let owned = leases.removeValue(forKey: source.id) else { return }
    (owned.bytes as? ProgressiveDownload)?.close()
    try? FileManager.default.removeItem(at: owned.file)
  }

  /// Call after stopping the decoder on account/queue replacement. Pending work cannot publish.
  public func reset() {
    generation += 1
    transfer?.cancel()
    for source in leases.values {
      (source.bytes as? ProgressiveDownload)?.close()
      try? FileManager.default.removeItem(at: source.file)
    }
    leases.removeAll()
  }
}
