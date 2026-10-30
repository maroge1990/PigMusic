import Foundation
import PigDomain

/// Account-scoped, two-file temporary cache. Each decoder gets its own reference lease.
/// This is transient playback storage, not durable offline access.
private actor FirstResolution {
  var value: URL?
  init(_ value: URL?) { self.value = value }
  func take() -> URL? {
    defer { value = nil }
    return value
  }
}

public actor PlaybackSourceRepository {
  private struct Key: Hashable {
    let id: String
    let revision: String?
    let state: String?
    init(_ track: Track) {
      id = track.id
      revision = track.sourceRevision
      state = track.serverState
    }
  }
  private struct Entry {
    let source: PreparedAudioSource
    var references = 0
    var touched: UInt64 = 0
  }
  private let preparer: AudioSourcePreparer
  private var entries: [Key: Entry] = [:]
  private var references: [UUID: Key] = [:]
  private var clock: UInt64 = 0
  private var busy = false
  private var generation = 0
  public init(preparer: AudioSourcePreparer) { self.preparer = preparer }

  public func acquire(
    _ track: Track, initialURL: URL? = nil, resolve: @escaping @Sendable (Track) async throws -> URL
  )
    async throws -> PreparedAudioSource
  {
    try Task.checkCancellation()
    let key = Key(track)
    dropFailed(key)
    if entries[key] != nil { return lease(key) }
    guard !busy else { throw AudioSourceFailure.busy }
    busy = true
    defer { busy = false }
    let stamp = generation
    if entries.count >= 2 {
      guard
        let victim = entries.filter({ $0.value.references == 0 }).min(by: {
          $0.value.touched < $1.value.touched
        })
      else {
        throw AudioSourceFailure.storageLimit
      }
      entries.removeValue(forKey: victim.key)
      await preparer.release(victim.value.source)
    }
    guard generation == stamp else { throw CancellationError() }
    let seed = FirstResolution(initialURL)
    let source = try await preparer.prepare {
      if let url = await seed.take() { return url }
      return try await resolve(track)
    }
    guard generation == stamp, !Task.isCancelled else {
      await preparer.release(source)
      throw CancellationError()
    }
    entries[key] = Entry(source: source)
    return lease(key)
  }
  /// Trusted local staging is deliberately separate from the remote resolver.
  /// A song already on this device plays from its own file: no staging copy
  /// and no size limit. The lease is not cached, and releasing it never
  /// deletes the file, which belongs to downloads.
  public func leaseLocal(_ file: URL) throws -> PreparedAudioSource {
    try Task.checkCancellation()
    guard file.isFileURL,
      let size = try file.resourceValues(forKeys: [.fileSizeKey, .isRegularFileKey]).fileSize,
      size > 0
    else { throw AudioSourceFailure.unavailable }
    return PreparedAudioSource(file: file, byteCount: size)
  }
  /// Copies a local file into playback staging (bounded); used for sources that
  /// must not be read in place.
  public func acquireLocal(_ track: Track, file: URL) async throws -> PreparedAudioSource {
    let key = Key(track)
    try Task.checkCancellation()
    if entries[key] != nil { return lease(key) }
    guard !busy else { throw AudioSourceFailure.busy }
    busy = true
    defer { busy = false }
    let stamp = generation
    if entries.count >= 2 {
      guard
        let victim = entries.filter({ $0.value.references == 0 }).min(by: {
          $0.value.touched < $1.value.touched
        })
      else { throw AudioSourceFailure.storageLimit }
      entries.removeValue(forKey: victim.key)
      await preparer.release(victim.value.source)
    }
    guard generation == stamp else { throw CancellationError() }
    let source = try await preparer.stageLocalFile(file)
    guard generation == stamp, !Task.isCancelled else {
      await preparer.release(source)
      throw CancellationError()
    }
    entries[key] = Entry(source: source)
    return lease(key)
  }
  /// A source whose transfer gave up is fetched again rather than reused.
  private func dropFailed(_ key: Key) {
    guard let entry = entries[key], entry.references == 0,
      (entry.source.bytes as? ProgressiveDownload)?.phase == .failed
    else { return }
    entries.removeValue(forKey: key)
    Task { [preparer] in await preparer.release(entry.source) }
  }
  private func lease(_ key: Key) -> PreparedAudioSource {
    clock &+= 1
    var entry = entries[key]!
    entry.references += 1
    entry.touched = clock
    entries[key] = entry
    (entry.source.bytes as? ProgressiveDownload)?.setIdle(false)
    let id = UUID()
    references[id] = key
    return PreparedAudioSource(
      id: id, file: entry.source.file, byteCount: entry.source.byteCount,
      bytes: entry.source.bytes)
  }
  public func release(_ source: PreparedAudioSource) {
    guard let key = references.removeValue(forKey: source.id), var entry = entries[key] else {
      return
    }
    entry.references = max(0, entry.references - 1)
    entries[key] = entry
    // Kept for going back, but no longer fetched while nothing plays it.
    if entry.references == 0 { (entry.source.bytes as? ProgressiveDownload)?.setIdle(true) }
  }
  /// Revisions of cached sources from this person's own library, which a refresh
  /// checks against their library. Household files and remote streams are not in
  /// it, so personal membership says nothing about whether they were removed.
  /// Nor are mix and radio songs: they play from the mix cache as `mix:` songs
  /// the library never lists, even when you own them.
  public func savedRevisions() -> [String: String?] {
    var result: [String: String?] = [:]
    for key in entries.keys
    where !key.id.hasPrefix("mix:") && (key.state == nil || key.state == "in_library") {
      result.updateValue(key.revision, forKey: key.id)
    }
    return result
  }
  /// Drops the cached sources of songs the server changed or removed, so they
  /// are fetched fresh next time. A source something still plays is kept; the
  /// player stops only when a changed song is in its queue (#76).
  public func forget(ids: Set<String>) async {
    for (key, entry) in entries where ids.contains(key.id) && entry.references == 0 {
      entries.removeValue(forKey: key)
      await preparer.release(entry.source)
    }
  }
  /// Owner must stop and await decoders before clearing account/library access.
  public func reset() async {
    generation += 1
    references.removeAll()
    entries.removeAll()
    await preparer.reset()
  }
}
