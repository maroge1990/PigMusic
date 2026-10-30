#if os(iOS)
  import CryptoKit
  import Foundation
  import PigDomain

  private struct BackgroundCopyJob: Codable, Sendable {
    var id: String
    var record: DownloadRecord
    var manifest: DeviceCopy
    var endpoint: String
    var credentialFingerprint: String
    var allowsCellular: Bool
  }
  private struct BackgroundCopyResponse: Codable, Sendable {
    var endpoint: String?
    var status: Int
    var mime: String?
  }
  @MainActor public enum BackgroundTransferHub {
    /// Followed by the account namespace, like the phone's download identifier.
    public static let watchIdentifierPrefix = "au.com.pig.music.watchaac."
    private static var managers: [String: BackgroundDownloadTransport] = [:]
    private static var completions: [String: () -> Void] = [:]
    public static func archive(for identifier: String) -> DownloadArchive? {
      managers[identifier]?.archive
    }
    static func transport(identifier: String, directory: URL, archive: DownloadArchive) throws
      -> BackgroundDownloadTransport
    {
      if let manager = managers[identifier] { return manager }
      let manager = try BackgroundDownloadTransport(
        identifier: identifier, directory: directory, archive: archive)
      managers[identifier] = manager
      return manager
    }
    /// Reconnect the session before SwiftUI/account restoration completes on a background relaunch.
    public static func handleEvents(identifier: String, completion: @escaping () -> Void) {
      completions[identifier] = completion
      guard managers[identifier] == nil else { return }
      // Phone downloads, and AAC copies being prepared for Apple Watch.
      let layouts = [
        (
          prefix: "au.com.pig.music.downloads.", archive: "Downloads",
          transfers: "BackgroundTransfers"
        ),
        (
          prefix: watchIdentifierPrefix, archive: "WatchAAC", transfers: "WatchBackgroundTransfers"
        ),
      ]
      guard let layout = layouts.first(where: { identifier.hasPrefix($0.prefix) }) else {
        finish(identifier)
        return
      }
      let namespace = String(identifier.dropFirst(layout.prefix.count))
      guard namespace.count == 64, namespace.allSatisfy({ "0123456789abcdef".contains($0) }) else {
        finish(identifier)
        return
      }
      do {
        let base = try FileManager.default.url(
          for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true
        ).appendingPathComponent("PigMusic").appendingPathComponent(namespace)
        let archive = try DownloadArchive(directory: base.appendingPathComponent(layout.archive))
        _ = try transport(
          identifier: identifier, directory: base.appendingPathComponent(layout.transfers),
          archive: archive)
      } catch { finish(identifier) }
    }
    static func finish(_ identifier: String) { completions.removeValue(forKey: identifier)?() }
  }

  /// The OS owns active file transfers; durable tickets contain no bearer token.
  @MainActor final class BackgroundDownloadTransport: NSObject, URLSessionDownloadDelegate {
    let archive: DownloadArchive
    private let identifier: String
    nonisolated let directory: URL
    private let vault: BackgroundResumeVault
    private var jobs: [String: BackgroundCopyJob]
    private var tasks: [String: URLSessionDownloadTask] = [:]
    private var processing: [String: Task<Void, Never>] = [:]
    private var suspending: Set<String> = []
    private var session: URLSession!
    private(set) var ready = false
    var budgetBytes: Int64 = 5 * 1024 * 1024 * 1024 { didSet { try? persistOptions() } }
    var allowsCellular = false { didSet { try? persistOptions() } }
    var userPaused = false { didSet { try? persistOptions() } }
    var onUpdate: (@MainActor (String?, OfflineFailure?, String?) async -> Void)?
    var onProgress: (@MainActor (String, Int64, Int64) async -> Void)?
    var activeCount: Int { tasks.count }
    init(identifier: String, directory: URL, archive: DownloadArchive) throws {
      self.identifier = identifier
      self.directory = directory
      self.archive = archive
      vault = BackgroundResumeVault(account: identifier)
      try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
      let loaded =
        (try? LibraryCodec.decoder().decode(
          [String: BackgroundCopyJob].self,
          from: Data(contentsOf: directory.appendingPathComponent("jobs.json")))) ?? [:]
      jobs = loaded.filter { UUID(uuidString: $0.key) != nil && $0.key == $0.value.id }
      let options =
        (try? JSONDecoder().decode(
          [String: Int64].self,
          from: Data(contentsOf: directory.appendingPathComponent("options.json")))) ?? [:]
      budgetBytes = options["budget"] ?? (5 * 1024 * 1024 * 1024)
      allowsCellular = options["cellular"] == 1
      userPaused = PersistedPause.restored(options["paused"])
      super.init()
      let configuration = URLSessionConfiguration.background(withIdentifier: identifier)
      configuration.httpShouldSetCookies = false
      configuration.urlCache = nil
      configuration.isDiscretionary = false
      configuration.sessionSendsLaunchEvents = true
      configuration.httpMaximumConnectionsPerHost = 2
      configuration.timeoutIntervalForResource = 7 * 86400
      session = URLSession(configuration: configuration, delegate: self, delegateQueue: nil)
      Task {
        let existing = await session.allTasks
        for task in existing {
          if let id = task.taskDescription, let job = jobs[id],
            let download = task as? URLSessionDownloadTask
          {
            tasks[id] = download
            try? await archive.prepareFileTransfer(
              job.record, manifest: job.manifest, budget: budgetBytes)
          } else {
            task.cancel()
          }
        }
        await recoverFiles()
        await applyCellularSetting()
        ready = true
        await onUpdate?(nil, nil, nil)
      }
    }
    private func persistOptions() throws {
      try JSONEncoder().encode([
        "budget": budgetBytes, "cellular": allowsCellular ? 1 : 0, "paused": PersistedPause.saved(userPaused),
      ]).write(
        to: directory.appendingPathComponent("options.json"), options: .atomic)
    }
    private func persist() throws {
      try LibraryCodec.encoder().encode(jobs).write(
        to: directory.appendingPathComponent("jobs.json"),
        options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
    }
    private func path(_ id: String, _ suffix: String) -> URL {
      directory.appendingPathComponent(id + suffix)
    }
    func enqueue(record: DownloadRecord, manifest: DeviceCopy, request: URLRequest) async throws {
      guard ready, request.url?.scheme == "https", request.url?.user == nil,
        request.url?.password == nil, let endpoint = request.url?.absoluteString
      else { throw OfflineFailure.unsafeResponse }
      let fingerprint = SHA256.hash(
        data: Data((request.value(forHTTPHeaderField: "Authorization") ?? "").utf8)
      ).map { String(format: "%02x", $0) }.joined()
      let existing = jobs.values.first { $0.record.id == record.id }
      if let existing, let live = tasks[existing.id] {
        if existing.allowsCellular == allowsCellular { return }
        // Started under the other cellular setting: start again under this one.
        live.cancel()
        removeJob(existing.id)
      }
      let matches =
        existing.map {
          $0.record.quality == record.quality
            && $0.record.track.sourceRevision == record.track.sourceRevision
            && $0.manifest.sha256 == manifest.sha256 && $0.endpoint == endpoint
            && $0.credentialFingerprint == fingerprint && $0.allowsCellular == allowsCellular
        } ?? false
      if let existing, !matches { removeJob(existing.id) }
      let job =
        matches
        ? existing!
        : BackgroundCopyJob(
          id: UUID().uuidString.lowercased(), record: record, manifest: manifest,
          endpoint: endpoint, credentialFingerprint: fingerprint, allowsCellular: allowsCellular)
      try await archive.prepareFileTransfer(record, manifest: manifest, budget: budgetBytes)
      jobs[job.id] = job
      try persist()
      var configured = request
      configured.allowsCellularAccess = allowsCellular
      configured.allowsExpensiveNetworkAccess = allowsCellular
      configured.allowsConstrainedNetworkAccess = false
      let task: URLSessionDownloadTask
      if matches, let resumed = vault.open(path(job.id, ".resume")) {
        task = session.downloadTask(withResumeData: resumed)
      } else {
        task = session.downloadTask(with: configured)
      }
      task.taskDescription = job.id
      tasks[job.id] = task
      task.resume()
    }
    /// A transfer keeps the network rules it started with: one started while
    /// cellular was off would wait for Wi-Fi after cellular is allowed (the
    /// system resumes such transfers after a relaunch). Requeue those under the
    /// current setting.
    func applyCellularSetting() async {
      for (id, task) in tasks {
        guard let job = jobs[id], job.allowsCellular != allowsCellular else { continue }
        task.cancel()
        removeJob(id)
        try? await archive.requeue(id: job.record.id)
      }
    }
    func pause() async {
      let active = tasks
      suspending.formUnion(active.keys)
      for (id, task) in active {
        let resume = await withCheckedContinuation { continuation in
          task.cancel(byProducingResumeData: { continuation.resume(returning: $0) })
        }
        if let resume, jobs[id] != nil { try? vault.seal(resume, at: path(id, ".resume")) }
        tasks[id] = nil
      }
      suspending.subtract(active.keys)
    }
    func reconcile(_ allowed: Set<String>, revisions: [String: String?]? = nil) async {
      for job in Array(jobs.values)
      where !allowed.contains(job.record.id)
        || (revisions != nil
          && (revisions?[job.record.id] ?? nil) != job.record.track.sourceRevision)
      {
        tasks[job.id]?.cancel()
        removeJob(job.id)
      }
    }
    func clear() async {
      for task in tasks.values { task.cancel() }
      tasks = [:]
      for task in processing.values { task.cancel() }
      for task in processing.values { await task.value }
      processing = [:]
      for id in Array(jobs.keys) { removeJob(id) }
      vault.clear()
    }
    private func removeJob(_ id: String) {
      jobs[id] = nil
      tasks[id] = nil
      for suffix in [".resume", ".incoming", ".response"] {
        try? FileManager.default.removeItem(at: path(id, suffix))
      }
      try? persist()
    }
    private func recoverFiles() async {
      for id in Array(jobs.keys)
      where FileManager.default.fileExists(atPath: path(id, ".incoming").path) {
        await finishFile(id)
      }
    }
    private func finishFile(_ id: String) async {
      guard let job = jobs[id] else { return }
      let file = path(id, ".incoming")
      do {
        let response = try JSONDecoder().decode(
          BackgroundCopyResponse.self, from: Data(contentsOf: path(id, ".response")))
        if response.status == 401 { throw OfflineFailure.authentication }
        guard [200, 206].contains(response.status), response.endpoint == job.endpoint,
          response.mime != "text/html", response.mime != "application/json"
        else { throw OfflineFailure.unsafeResponse }
        try Task.checkCancellation()
        try await archive.publishFile(
          file, record: job.record, manifest: job.manifest, budget: budgetBytes)
        removeJob(id)
        await onUpdate?(job.record.id, nil, nil)
      } catch {
        tasks[id] = nil
        try? FileManager.default.removeItem(at: file)
        try? FileManager.default.removeItem(at: path(id, ".response"))
        try? await archive.fail(id: job.record.id, message: error.localizedDescription)
        await onUpdate?(job.record.id, error as? OfflineFailure, error.localizedDescription)
      }
    }
    nonisolated func urlSession(
      _ session: URLSession, task: URLSessionTask,
      willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest,
      completionHandler: @escaping @Sendable (URLRequest?) -> Void
    ) { completionHandler(nil) }
    nonisolated func urlSession(
      _ session: URLSession, downloadTask: URLSessionDownloadTask,
      didFinishDownloadingTo location: URL
    ) {
      guard let id = downloadTask.taskDescription, UUID(uuidString: id) != nil,
        let response = downloadTask.response as? HTTPURLResponse
      else { return }
      let file = directory.appendingPathComponent(id + ".incoming")
      do {
        if FileManager.default.fileExists(atPath: file.path) {
          try FileManager.default.removeItem(at: file)
        }
        try FileManager.default.moveItem(at: location, to: file)
        try JSONEncoder().encode(
          BackgroundCopyResponse(
            endpoint: response.url?.absoluteString, status: response.statusCode,
            mime: response.mimeType)
        ).write(to: directory.appendingPathComponent(id + ".response"), options: .atomic)
        Task { @MainActor in
          self.tasks[id] = nil
          let work = Task { await self.finishFile(id) }
          self.processing[id] = work
          await work.value
          self.processing[id] = nil
        }
      } catch {
        try? FileManager.default.removeItem(at: file)
        Task { @MainActor in
          guard let job = self.jobs[id] else { return }
          self.tasks[id] = nil
          try? await self.archive.fail(
            id: job.record.id,
            message: "The downloaded file could not be retained. Check device storage.")
          await self.onUpdate?(
            job.record.id, .quota,
            "The downloaded file could not be retained. Check device storage.")
        }
      }
    }
    nonisolated func urlSession(
      _ session: URLSession, downloadTask: URLSessionDownloadTask, didWriteData bytesWritten: Int64,
      totalBytesWritten: Int64, totalBytesExpectedToWrite: Int64
    ) {
      guard let id = downloadTask.taskDescription else { return }
      Task { @MainActor in
        guard let job = self.jobs[id] else { return }
        await self.onProgress?(job.record.id, totalBytesWritten, job.manifest.bytes)
      }
    }
    nonisolated func urlSession(
      _ session: URLSession, task: URLSessionTask, didCompleteWithError error: (any Error)?
    ) {
      guard let error, let id = task.taskDescription else { return }
      let resume = (error as NSError).userInfo[NSURLSessionDownloadTaskResumeData] as? Data
      let cancelled = (error as NSError).code == NSURLErrorCancelled
      Task { @MainActor in
        guard let job = self.jobs[id] else { return }
        self.tasks[id] = nil
        if let resume {
          try? self.vault.seal(resume, at: self.path(id, ".resume"))
        } else if !cancelled {
          try? FileManager.default.removeItem(at: self.path(id, ".resume"))
        }
        if self.suspending.contains(id) || cancelled { return }
        let message = "Download interrupted. Retry when the network is available."
        try? await self.archive.fail(id: job.record.id, message: message)
        await self.onUpdate?(job.record.id, nil, message)
      }
    }
    nonisolated func urlSessionDidFinishEvents(forBackgroundURLSession session: URLSession) {
      Task { @MainActor in
        for work in self.processing.values { await work.value }
        await self.recoverFiles()
        BackgroundTransferHub.finish(self.identifier)
      }
    }
  }
#endif
