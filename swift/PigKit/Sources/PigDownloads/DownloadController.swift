import Foundation
import Observation
import PigDomain

@available(macOS 14, *)
@MainActor @Observable public final class DownloadController {
  public private(set) var records: [DownloadRecord] = []
  public private(set) var progress: [String: Double] = [:]
  public private(set) var running = false
  public var budgetBytes: Int64 = 5 * 1024 * 1024 * 1024 {
    didSet {
      #if os(iOS)
        background?.budgetBytes = budgetBytes
      #endif
    }
  }
  public var allowsCellular = false
  /// Called after a background transfer changes a record, including while the app is
  /// in the background, so an owner can pass finished files on (as to Apple Watch).
  @ObservationIgnored public var onChange: (@MainActor () async -> Void)?
  public var usesBackgroundTransfers: Bool {
    #if os(iOS)
      return background != nil
    #else
      return false
    #endif
  }
  #if os(iOS)
    @ObservationIgnored private var background: BackgroundDownloadTransport?
    public func enableBackground(identifier: String, directory: URL) throws {
      let transport = try BackgroundTransferHub.transport(
        identifier: identifier, directory: directory, archive: archive)
      background = transport
      transport.budgetBytes = budgetBytes
      allowsCellular = transport.allowsCellular
      userPaused = transport.userPaused
      transport.onUpdate = { [weak self] _, failure, message in
        guard let self else { return }
        await reload()
        await onChange?()
        if let failure {
          onFailure(failure)
        } else if let message {
          onFailure(
            NSError(
              domain: "PigMusicDownloads", code: 1, userInfo: [NSLocalizedDescriptionKey: message]))
        }
        if preparationAllowed { resume(automatic: true) }
      }
      transport.onProgress = { [weak self] id, bytes, total in
        guard let self else { return }
        progress[id] = total > 0 ? Double(bytes) / Double(total) : 0
      }
    }
    public func setCellular(_ enabled: Bool) async {
      await pause()
      allowsCellular = enabled
      background?.allowsCellular = enabled
      resume()
    }
  #endif
  public let archive: DownloadArchive
  @ObservationIgnored private let source: any DeviceCopySource
  @ObservationIgnored private let onFailure: @MainActor (any Error) -> Void
  @ObservationIgnored private var worker: Task<Void, Never>?
  @ObservationIgnored private var generation = 0
  @ObservationIgnored private var userPaused = false
  @ObservationIgnored private var preparationAllowed = true
  public init(
    archive: DownloadArchive, source: any DeviceCopySource,
    onFailure: @escaping @MainActor (any Error) -> Void = { _ in }
  ) {
    self.archive = archive
    self.source = source
    self.onFailure = onFailure
  }
  public func reload() async { records = await archive.snapshot() }
  public func enqueue(
    _ tracks: [Track], quality: String = "original", origin: DownloadOrigin = .direct
  ) async throws {
    try await archive.enqueue(tracks, quality: quality, origin: origin)
    await reload()
    resume()
  }
  /// Removes an album's or playlist's claim on these songs; files still needed
  /// by another album or playlist stay on the device.
  public func release(ids: [String], origin: DownloadOrigin) async {
    let leaving = Set(
      records.filter { ids.contains($0.id) && $0.effectiveOrigins.allSatisfy { $0.same(origin) } }
        .map(\.id))
    guard !leaving.isEmpty else {
      try? await archive.release(ids: ids, origin: origin)
      await reload()
      return
    }
    await pause()
    #if os(iOS)
      await background?.reconcile(Set(records.map(\.id)).subtracting(leaving))
    #endif
    try? await archive.release(ids: ids, origin: origin)
    await reload()
    for id in leaving { progress[id] = nil }
    resume()
  }
  public func clearFinished(ids: Set<String>? = nil) async {
    try? await archive.clearFinished(ids: ids)
    await reload()
  }
  public func resume(automatic: Bool = false) {
    guard !automatic || !userPaused else { return }
    if !automatic {
      userPaused = false
      preparationAllowed = true
      #if os(iOS)
        background?.userPaused = false
      #endif
    }
    guard preparationAllowed, worker == nil else { return }
    #if os(iOS)
      if let background, !background.ready { return }
    #endif
    let stamp = generation
    running = true
    worker = Task {
      defer {
        if generation == stamp {
          worker = nil
          running = false
        }
      }
      var asked: Set<String> = []
      while !Task.isCancelled, generation == stamp {
        #if os(iOS)
          if let background, background.activeCount >= 128 { break }
        #endif
        let all = await archive.snapshot()
        guard let record = all.first(where: { $0.state == "queued" }) else { break }
        // Ask for the waiting copies now, not one after another, so the
        // server converts the next songs while this one is prepared and
        // transferred (WATCH1). Its own turn asks again and reports failures.
        let ahead = all.filter {
          $0.state == "queued" && $0.id != record.id && !asked.contains($0.id)
        }
        .prefix(Self.requestAhead)
        if !ahead.isEmpty {
          asked.formUnion(ahead.map(\.id))
          let source = source
          let wanted = ahead.map { ($0.id, $0.quality) }
          Task.detached(priority: .utility) {
            for (id, quality) in wanted {
              guard !Task.isCancelled else { return }
              _ = try? await source.deviceCopy(trackId: id, quality: quality, create: true)
            }
          }
        }
        do {
          var manifest = try await source.deviceCopy(
            trackId: record.id, quality: record.quality, create: true)
          var polls = 0
          while ["queued", "preparing"].contains(manifest.state) {
            try Task.checkCancellation()
            guard generation == stamp else { throw CancellationError() }
            polls += 1
            guard polls < 300 else { throw OfflineFailure.unsafeResponse }
            try await Task.sleep(for: .seconds(2))
            manifest = try await source.deviceCopy(
              trackId: record.id, quality: record.quality, create: false)
          }
          let request = try await source.copyRequest(trackId: record.id, quality: record.quality)
          #if os(iOS)
            if let background {
              try await background.enqueue(record: record, manifest: manifest, request: request)
            } else {
              try await foregroundTransfer(
                record, manifest: manifest, request: request, stamp: stamp)
            }
          #else
            try await foregroundTransfer(record, manifest: manifest, request: request, stamp: stamp)
          #endif
        } catch is CancellationError { break } catch {
          guard generation == stamp else { break }
          try? await archive.fail(id: record.id, message: error.localizedDescription)
          onFailure(error)
          if case OfflineFailure.authentication = error { break }
        }
        guard generation == stamp else { break }
        await reload()
      }
    }
  }
  /// Copies asked for ahead of their turn, at most.
  static let requestAhead = 32
  private func foregroundTransfer(
    _ record: DownloadRecord, manifest: DeviceCopy, request: URLRequest, stamp: Int
  ) async throws {
    try await archive.transfer(record, manifest: manifest, request: request, budget: budgetBytes) {
      [weak self] bytes, total in
      await self?.updateProgress(id: record.id, bytes: bytes, total: total, stamp: stamp)
    }
  }
  private func updateProgress(id: String, bytes: Int64, total: Int64, stamp: Int) async {
    if generation == stamp {
      progress[id] = total > 0 ? Double(bytes) / Double(total) : 0
      let snapshot = await archive.snapshot()
      if generation == stamp { records = snapshot }
    }
  }
  public func remove(id: String) async {
    await pause()
    #if os(iOS)
      let retained = Set(records.map(\.id)).subtracting([id])
      await background?.reconcile(retained)
    #endif
    try? await archive.remove(id: id)
    await reload()
    progress[id] = nil
    resume()
  }
  /// Preparation pauses with the scene, while OS-managed transfers continue.
  public func suspendPreparation() async {
    preparationAllowed = false
    await stopWorker()
    if !usesBackgroundTransfers { try? await archive.interruptRunning() }
    await reload()
  }
  public func resumePreparation() {
    preparationAllowed = true
    resume(automatic: true)
  }
  private func stopWorker() async {
    generation += 1
    let previous = worker
    previous?.cancel()
    await previous?.value
    worker = nil
    running = false
  }
  public func pause() async {
    userPaused = true
    #if os(iOS)
      background?.userPaused = true
    #endif
    await stopWorker()
    #if os(iOS)
      await background?.pause()
    #endif
    try? await archive.interruptRunning()
    await reload()
  }
  public func reconcile(_ allowed: [String: String?]) async {
    await stopWorker()
    #if os(iOS)
      await background?.reconcile(Set(allowed.keys), revisions: allowed)
    #endif
    if !usesBackgroundTransfers { try? await archive.interruptRunning() }
    try? await archive.reconcile(allowed)
    await reload()
  }
  public func clear() async {
    await pause()
    #if os(iOS)
      await background?.clear()
    #endif
    try? await archive.clear()
    await reload()
    progress = [:]
  }
}
