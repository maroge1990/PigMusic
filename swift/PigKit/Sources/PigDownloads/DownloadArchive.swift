import CryptoKit
import Foundation
import PigDomain

private final class NoDownloadRedirect: NSObject, URLSessionTaskDelegate, Sendable {
  func urlSession(
    _ session: URLSession, task: URLSessionTask,
    willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest,
    completionHandler: @escaping @Sendable (URLRequest?) -> Void
  ) { completionHandler(nil) }
}

/// Durable, account-bound foreground downloads. Incomplete transfers restart safely.
public actor DownloadArchive {
  private let directory: URL
  private let session: URLSession
  private var records: [DownloadRecord]
  private var invalidated: Set<String> = [] {
    didSet { revision += 1 }
  }
  /// Changes whenever a record or its usability changes, so a caller can
  /// reuse what it derived from the archive while this stays the same.
  public private(set) var revision = 0
  public init(directory: URL, configuration: URLSessionConfiguration = .ephemeral) throws {
    self.directory = directory
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    configuration.allowsCellularAccess = false
    configuration.httpShouldSetCookies = false
    configuration.urlCache = nil
    configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
    session = URLSession(
      configuration: configuration, delegate: NoDownloadRedirect(), delegateQueue: nil)
    let manifest = directory.appendingPathComponent("downloads.json")
    if FileManager.default.fileExists(atPath: manifest.path) {
      records =
        (try? LibraryCodec.decoder().decode(
          [DownloadRecord].self, from: Data(contentsOf: manifest))) ?? []
      for index in records.indices where records[index].state == "downloading" {
        records[index].state = "queued"
        records[index].bytes = 0
      }
    } else {
      records = []
    }
    let retainedNames = Set(
      records.filter { $0.state == "ready" }.compactMap(\.fileName)
        + records.compactMap { $0.retained?.fileName })
    for file in try FileManager.default.contentsOfDirectory(
      at: directory, includingPropertiesForKeys: nil)
    where file.pathExtension == "partial"
      || (file.lastPathComponent != "downloads.json"
        && !retainedNames.contains(file.lastPathComponent))
    { try? FileManager.default.removeItem(at: file) }
  }
  public func snapshot() -> [DownloadRecord] { records }
  public func usedBytes() -> Int64 {
    records.reduce(0) { $0 + ($1.state == "ready" ? $1.total : 0) + ($1.retained?.bytes ?? 0) }
  }
  private func persist() throws {
    revision += 1
    try LibraryCodec.encoder().encode(records).write(
      to: directory.appendingPathComponent("downloads.json"), options: .atomic)
  }
  public func enqueue(_ tracks: [Track], quality: String, origin: DownloadOrigin = .direct) throws {
    guard ["original", "aac"].contains(quality), tracks.count <= 10000 else {
      throw OfflineFailure.unsafeResponse
    }
    for track in tracks where track.serverState == nil || track.serverState == "in_library" {
      if let index = records.firstIndex(where: { $0.id == track.id }) {
        records[index].origins = Self.adding(origin, to: records[index].effectiveOrigins)
        if records[index].state == "ready" && records[index].quality == quality
          && records[index].track.sourceRevision == track.sourceRevision
        {
          continue
        }
        if records[index].state == "downloading" { continue }
        let old = records[index]
        var replacement = DownloadRecord(track: track, quality: quality)
        replacement.origins = old.origins
        if old.track.sourceRevision == track.sourceRevision {
          if old.state == "ready", let name = old.fileName {
            replacement.retained = RetainedDownload(
              quality: old.quality, bytes: old.total, fileName: name, checksum: old.checksum,
              sourceRevision: old.track.sourceRevision)
          } else {
            replacement.retained = old.retained
          }
        } else {
          try remove(id: track.id)
        }
        if let existing = records.firstIndex(where: { $0.id == track.id }) {
          records[existing] = replacement
        } else {
          records.append(replacement)
        }
      } else {
        var record = DownloadRecord(track: track, quality: quality)
        record.origins = [origin]
        records.append(record)
      }
      invalidated.remove(track.id)
    }
    try persist()
  }
  private static func adding(_ origin: DownloadOrigin, to origins: [DownloadOrigin])
    -> [DownloadOrigin]
  {
    var result = origins.filter { !$0.same(origin) }
    result.append(origin)
    return result
  }
  /// Removes one origin from these songs. A song's file is deleted only when no
  /// other album or playlist still needs it. Returns the IDs whose files went.
  @discardableResult
  public func release(ids: [String], origin: DownloadOrigin) throws -> [String] {
    var removed: [String] = []
    for id in ids {
      guard let index = records.firstIndex(where: { $0.id == id }) else { continue }
      let remaining = records[index].effectiveOrigins.filter { !$0.same(origin) }
      if remaining.isEmpty {
        try remove(id: id)
        removed.append(id)
      } else {
        records[index].origins = remaining
      }
    }
    try persist()
    return removed
  }
  /// Hides finished transfers from the activity list; their files stay. With
  /// `ids`, only those songs count as finished.
  public func clearFinished(ids: Set<String>? = nil) throws {
    for index in records.indices
    where records[index].state == "ready" && ids.map({ $0.contains(records[index].id) }) ?? true {
      records[index].cleared = true
    }
    try persist()
  }
  public func localFile(_ track: Track) -> URL? {
    guard let record = records.first(where: { $0.id == track.id }) else { return nil }
    return localFile(track, record: record)
  }
  /// `localFile` for many songs with one index instead of a search per song
  /// (the Watch manifest asks for every ready song).
  public func localFiles(_ tracks: [Track]) -> [String: URL] {
    let byId = Dictionary(records.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
    var files: [String: URL] = [:]
    for track in tracks {
      if let record = byId[track.id], let file = localFile(track, record: record) {
        files[track.id] = file
      }
    }
    return files
  }
  private func localFile(_ track: Track, record: DownloadRecord) -> URL? {
    guard !invalidated.contains(track.id), record.track.sourceRevision == track.sourceRevision
    else { return nil }
    if record.state == "ready", let name = record.fileName,
      validFile(name, id: track.id, quality: record.quality, bytes: record.total)
    {
      return directory.appendingPathComponent(name)
    }
    if let old = record.retained, old.sourceRevision == track.sourceRevision,
      validFile(old.fileName, id: track.id, quality: old.quality, bytes: old.bytes)
    {
      return directory.appendingPathComponent(old.fileName)
    }
    return nil
  }
  private func validFile(_ name: String, id: String, quality: String, bytes: Int64) -> Bool {
    guard
      name
        == safeName(
          id, quality: quality, extension: name.split(separator: ".").last.map(String.init) ?? ""),
      let size = try? directory.appendingPathComponent(name).resourceValues(forKeys: [.fileSizeKey])
        .fileSize
    else { return false }
    return Int64(size) == bytes
  }
  private func safeName(_ id: String, quality: String, extension ext: String) -> String {
    SHA256.hash(data: Data((id + "\n" + quality).utf8)).map { String(format: "%02x", $0) }.joined()
      + "." + ext
  }
  public func remove(id: String) throws {
    invalidated.insert(id)
    if let record = records.first(where: { $0.id == id }), let name = record.fileName {
      let ext = name.split(separator: ".").last.map(String.init) ?? ""
      if name == safeName(id, quality: record.quality, extension: ext) {
        try? FileManager.default.removeItem(at: directory.appendingPathComponent(name))
      }
    }
    if let old = records.first(where: { $0.id == id })?.retained,
      validFile(old.fileName, id: id, quality: old.quality, bytes: old.bytes)
    {
      try? FileManager.default.removeItem(at: directory.appendingPathComponent(old.fileName))
    }
    records.removeAll { $0.id == id }
    try persist()
  }
  public func reconcile(_ allowed: [String: String?]) throws {
    for record in records {
      guard let revision = allowed[record.id], revision == record.track.sourceRevision else {
        try remove(id: record.id)
        continue
      }
    }
  }
  public func clear() throws {
    invalidated.formUnion(records.map(\.id))
    records = []
    for file in try FileManager.default.contentsOfDirectory(
      at: directory, includingPropertiesForKeys: nil)
    { try? FileManager.default.removeItem(at: file) }
    try persist()
  }
  public func interruptRunning() throws {
    for index in records.indices where records[index].state == "downloading" {
      records[index].state = "queued"
      records[index].bytes = 0
    }
    try persist()
  }
  /// One transfer goes back to the queue (its network rules changed).
  public func requeue(id: String) throws {
    guard let index = records.firstIndex(where: { $0.id == id && $0.state == "downloading" })
    else { return }
    records[index].state = "queued"
    records[index].bytes = 0
    try persist()
  }
  public func fail(id: String, message: String) throws {
    guard let index = records.firstIndex(where: { $0.id == id }) else { return }
    records[index].state = "failed"
    records[index].error = message
    try persist()
  }
  public func prepareFileTransfer(
    _ record: DownloadRecord, manifest: DeviceCopy, budget: Int64, checkCapacity: Bool = true
  )
    throws
  {
    guard manifest.state == "ready", manifest.quality == record.quality,
      let checksum = manifest.sha256, checksum.count == 64,
      checksum.allSatisfy({ "0123456789abcdef".contains($0) }), manifest.bytes > 0,
      ["mp3", "m4a", "aac", "flac", "ogg", "opus", "wav", "aiff", "aif", "wma", "ape", "wv"]
        .contains(manifest.fileExtension),
      !invalidated.contains(record.id),
      let index = records.firstIndex(where: {
        $0.id == record.id && $0.quality == record.quality
          && $0.track.sourceRevision == record.track.sourceRevision
      })
    else { throw OfflineFailure.unsafeResponse }
    let reserved = records.filter { $0.id != record.id && $0.state == "downloading" }.reduce(
      Int64(0)
    ) { $0 + $1.total }
    guard usedBytes() + reserved + manifest.bytes <= budget else { throw OfflineFailure.quota }
    if checkCapacity, let capacity = try VolumeSpace.available(at: directory),
      capacity < manifest.bytes + 32 * 1024 * 1024
    {
      throw OfflineFailure.quota
    }
    records[index].state = "downloading"
    records[index].total = manifest.bytes
    records[index].error = nil
    try persist()
  }
  public func publishFile(_ file: URL, record: DownloadRecord, manifest: DeviceCopy, budget: Int64)
    throws
  {
    try prepareFileTransfer(record, manifest: manifest, budget: budget, checkCapacity: false)
    guard let expected = manifest.sha256,
      let size = try file.resourceValues(forKeys: [.fileSizeKey]).fileSize,
      Int64(size) == manifest.bytes
    else { throw OfflineFailure.integrity }
    let handle = try FileHandle(forReadingFrom: file)
    defer { try? handle.close() }
    var digest = SHA256()
    while let data = try handle.read(upToCount: 256 * 1024), !data.isEmpty {
      digest.update(data: data)
    }
    guard digest.finalize().map({ String(format: "%02x", $0) }).joined() == expected else {
      throw OfflineFailure.integrity
    }
    guard
      let index = records.firstIndex(where: {
        $0.id == record.id && $0.quality == record.quality
          && $0.track.sourceRevision == record.track.sourceRevision
      }), !invalidated.contains(record.id)
    else { throw OfflineFailure.cancelled }
    let name = safeName(record.id, quality: record.quality, extension: manifest.fileExtension)
    let output = directory.appendingPathComponent(name)
    if FileManager.default.fileExists(atPath: output.path) {
      _ = try FileManager.default.replaceItemAt(output, withItemAt: file)
    } else {
      try FileManager.default.moveItem(at: file, to: output)
    }
    let previous = records[index]
    let retained = previous.retained
    records[index].retained = nil
    records[index].state = "ready"
    records[index].bytes = manifest.bytes
    records[index].total = manifest.bytes
    records[index].fileName = name
    records[index].checksum = expected
    do { try persist() } catch {
      records[index] = previous
      try? FileManager.default.removeItem(at: output)
      throw error
    }
    if let retained, retained.fileName != name,
      validFile(retained.fileName, id: record.id, quality: retained.quality, bytes: retained.bytes)
    {
      try? FileManager.default.removeItem(at: directory.appendingPathComponent(retained.fileName))
    }
  }
  public func transfer(
    _ record: DownloadRecord, manifest: DeviceCopy, request: URLRequest, budget: Int64,
    onProgress: @Sendable (Int64, Int64) async -> Void = { _, _ in }
  ) async throws {
    let extensions = [
      "mp3", "m4a", "aac", "flac", "ogg", "opus", "wav", "aiff", "aif", "wma", "ape", "wv",
    ]
    guard request.url?.scheme == "https", manifest.state == "ready", let expected = manifest.sha256,
      expected.count == 64, expected.allSatisfy({ "0123456789abcdef".contains($0) }),
      manifest.quality == record.quality, extensions.contains(manifest.fileExtension),
      manifest.bytes > 0
    else { throw OfflineFailure.unsafeResponse }
    guard usedBytes() + manifest.bytes <= budget else { throw OfflineFailure.quota }
    guard let index = records.firstIndex(where: { $0.id == record.id }),
      !invalidated.contains(record.id)
    else { throw OfflineFailure.cancelled }
    records[index].state = "downloading"
    records[index].total = manifest.bytes
    records[index].error = nil
    try persist()
    await onProgress(0, manifest.bytes)
    let name = safeName(record.id, quality: record.quality, extension: manifest.fileExtension)
    let temporary = directory.appendingPathComponent(name + ".partial")
    let output = directory.appendingPathComponent(name)
    FileManager.default.createFile(atPath: temporary.path, contents: nil)
    let file = try FileHandle(forWritingTo: temporary)
    defer {
      try? file.close()
      try? FileManager.default.removeItem(at: temporary)
    }
    let (stream, response) = try await session.bytes(for: request)
    guard let http = response as? HTTPURLResponse else { throw OfflineFailure.unsafeResponse }
    if http.statusCode == 401 { throw OfflineFailure.authentication }
    guard http.statusCode == 200, response.url == request.url,
      response.mimeType != "application/json", response.mimeType != "text/html",
      response.expectedContentLength < 0 || response.expectedContentLength == manifest.bytes
    else { throw OfflineFailure.unsafeResponse }
    var digest = SHA256()
    var block = Data()
    var bytes: Int64 = 0
    var lastReport: Int64 = 0
    for try await byte in stream {
      block.append(byte)
      bytes += 1
      guard bytes <= manifest.bytes else { throw OfflineFailure.integrity }
      if block.count == 65536 {
        try Task.checkCancellation()
        guard !invalidated.contains(record.id), records.contains(where: { $0.id == record.id })
        else { throw OfflineFailure.cancelled }
        try file.write(contentsOf: block)
        digest.update(data: block)
        block.removeAll(keepingCapacity: true)
      }
      if bytes - lastReport >= 512 * 1024 {
        lastReport = bytes
        await onProgress(bytes, manifest.bytes)
      }
    }
    try Task.checkCancellation()
    guard !invalidated.contains(record.id),
      let index = records.firstIndex(where: { $0.id == record.id })
    else { throw OfflineFailure.cancelled }
    if !block.isEmpty {
      try file.write(contentsOf: block)
      digest.update(data: block)
    }
    guard bytes == manifest.bytes,
      digest.finalize().map({ String(format: "%02x", $0) }).joined() == expected
    else { throw OfflineFailure.integrity }
    try file.synchronize()
    try file.close()
    if FileManager.default.fileExists(atPath: output.path) {
      try FileManager.default.removeItem(at: output)
    }
    try FileManager.default.moveItem(at: temporary, to: output)
    let retained = records[index].retained
    records[index].retained = nil
    records[index].state = "ready"
    records[index].bytes = bytes
    records[index].total = bytes
    records[index].fileName = name
    records[index].checksum = expected
    do { try persist() } catch {
      records[index].retained = retained
      records[index].state = "failed"
      try? FileManager.default.removeItem(at: output)
      throw error
    }
    if let retained, retained.fileName != name,
      validFile(retained.fileName, id: record.id, quality: retained.quality, bytes: retained.bytes)
    {
      try? FileManager.default.removeItem(at: directory.appendingPathComponent(retained.fileName))
    }
    await onProgress(bytes, manifest.bytes)
  }
}
