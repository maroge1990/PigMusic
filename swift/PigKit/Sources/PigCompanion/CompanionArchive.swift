import CryptoKit
import Foundation
import PigDomain

/// What the Watch does with a song file the phone sent.
public enum CompanionArrival: Equatable, Sendable {
  /// The committed library lists this song and checksum: store it now.
  case accept
  /// A newer revision is announced but its manifest has not arrived: keep the
  /// file in the inbox until it does, then decide again.
  case wait
  /// It can never be stored (another account, removed, or not in the library).
  case stale
}

/// The Watch archive's own failures.
public enum CompanionArchiveFailure: Error, LocalizedError, Equatable {
  /// Apple Watch has no room left for the song.
  case deviceFull
  public var errorDescription: String? {
    "Apple Watch has no space left. Remove songs or free up space on Apple Watch."
  }
}

/// A bounded, credential-free Watch archive. Removal is driven by a confirmed phone context.
///
/// The last applied manifest is the committed, playable library. A new revision for
/// the same account is staged: its context arrives first, and the committed songs
/// stay playable until the matching manifest replaces them. A different or signed-out
/// account revokes the library at once.
public actor CompanionArchive {
  private let directory: URL
  private let now: @Sendable () -> Date
  private var manifest: CompanionManifest
  private var context: CompanionContext
  private var appliedRevision: Int64?
  private var removed: Set<String>
  /// When each stored file arrived, by checksum, for the phone's delivery
  /// times (#219).
  private var arrivals: [String: Date]
  public private(set) var budgetBytes: Int64
  /// Saved library files that were there but couldn't be read at opening
  /// (context.json, manifest.json); the library started empty instead.
  public nonisolated let damagedFiles: [String]
  public init(
    directory: URL, budgetBytes: Int64 = 1024 * 1024 * 1024,
    now: @escaping @Sendable () -> Date = { Date() }
  ) throws {
    self.directory = directory
    self.now = now
    self.budgetBytes = budgetBytes
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    let decoder = JSONDecoder()
    appliedRevision =
      (try? decoder.decode(
        Int64.self, from: Data(contentsOf: directory.appendingPathComponent("applied.json"))))
    removed =
      (try? decoder.decode(
        Set<String>.self, from: Data(contentsOf: directory.appendingPathComponent("removed.json"))))
      ?? []
    arrivals =
      (try? decoder.decode(
        [String: Date].self,
        from: Data(contentsOf: directory.appendingPathComponent("arrivals.json")))) ?? [:]
    self.budgetBytes =
      (try? decoder.decode(
        Int64.self, from: Data(contentsOf: directory.appendingPathComponent("budget.json"))))
      ?? budgetBytes
    // Absent on a fresh install, or there but unreadable: an empty library,
    // so the iPhone's next copy can be taken. A damaged one is noted, so the
    // Watch can say why its music went (Codex round 1 on #252).
    var damaged: [String] = []
    func saved<Value: Decodable>(_ type: Value.Type, _ name: String) -> Value? {
      let file = directory.appendingPathComponent(name)
      guard FileManager.default.fileExists(atPath: file.path) else { return nil }
      do { return try decoder.decode(type, from: Data(contentsOf: file)) } catch {
        damaged.append(name)
        return nil
      }
    }
    let loadedContext =
      saved(CompanionContext.self, "context.json") ?? CompanionContext(revision: 0, account: nil)
    context = loadedContext
    let loadedManifest =
      saved(CompanionManifest.self, "manifest.json")
      ?? CompanionManifest(context: loadedContext, songs: [])
    damagedFiles = damaged
    // A committed library from the same account stays playable while a newer
    // revision is on its way; anything else is revoked.
    let sameAccount =
      loadedContext.account != nil && loadedManifest.context.account == loadedContext.account
      && loadedManifest.context.revision <= loadedContext.revision
    manifest = sameAccount ? loadedManifest : CompanionManifest(context: loadedContext, songs: [])
    // The applied marker belongs to the manifest it was written for: without
    // that manifest the receipt must not claim it, or the iPhone takes the
    // empty library as applied and never sends it again, and its audio is
    // turned away as stale (Codex round 2 on #252). Audio then waits, as on
    // a fresh install, and the same revision's manifest is taken when resent.
    if appliedRevision != nil, appliedRevision != manifest.context.revision
      || damaged.contains("manifest.json")
      || !FileManager.default.fileExists(
        atPath: directory.appendingPathComponent("manifest.json").path)
    {
      appliedRevision = nil
    }
    for file in try FileManager.default.contentsOfDirectory(
      at: directory, includingPropertiesForKeys: nil) where file.pathExtension == "partial"
    {
      try? FileManager.default.removeItem(at: file)
    }
  }
  public func pendingRequests() -> [String: String] {
    (try? JSONDecoder().decode(
      [String: String].self,
      from: Data(contentsOf: directory.appendingPathComponent("requests.json")))) ?? [:]
  }
  public func request(_ action: String, ids: [String]) throws {
    guard context.account != nil, ["download", "remove"].contains(action), ids.count <= 100 else {
      throw CompanionFailure.invalid
    }
    var requests = pendingRequests()
    for id in ids {
      requests[id] = action
      if action == "download" { removed.remove(id) }
    }
    try JSONEncoder().encode(removed).write(
      to: directory.appendingPathComponent("removed.json"), options: .atomic)
    guard requests.count <= 1000 else { throw CompanionFailure.quota }
    try JSONEncoder().encode(requests).write(
      to: directory.appendingPathComponent("requests.json"), options: .atomic)
  }
  public func pendingListens() -> [ListeningEvent] {
    (try? JSONDecoder().decode(
      [ListeningEvent].self,
      from: Data(contentsOf: directory.appendingPathComponent("listens.json")))) ?? []
  }
  public func recordListen(_ event: ListeningEvent) throws {
    guard context.account != nil else { throw CompanionFailure.stale }
    var events = pendingListens().filter {
      $0.playedAt >= Int(Date().timeIntervalSince1970) - 30 * 86400
    }
    guard !events.contains(where: { $0.id == event.id }) else { return }
    guard events.count < 500 else { throw CompanionFailure.quota }
    events.append(event)
    try JSONEncoder().encode(events).write(
      to: directory.appendingPathComponent("listens.json"), options: .atomic)
  }
  public func acknowledgeListen(_ id: String, account: String?) throws {
    guard account == context.account else { throw CompanionFailure.stale }
    try JSONEncoder().encode(pendingListens().filter { $0.id != id }).write(
      to: directory.appendingPathComponent("listens.json"), options: .atomic)
  }
  public func snapshot() -> CompanionManifest { manifest }
  public func activeContext() -> CompanionContext { context }
  public func setBudget(_ bytes: Int64) throws {
    guard bytes >= 128 * 1024 * 1024, bytes <= 8 * 1024 * 1024 * 1024 else {
      throw CompanionFailure.invalid
    }
    try JSONEncoder().encode(bytes).write(
      to: directory.appendingPathComponent("budget.json"), options: .atomic)
    budgetBytes = bytes
  }
  public func apply(_ next: CompanionContext) throws {
    guard next.version == 1, next.revision >= context.revision else { throw CompanionFailure.stale }
    if next.revision == context.revision {
      guard next == context else { throw CompanionFailure.stale }
      return
    }
    let changedAccount = next.account != context.account
    try JSONEncoder().encode(next).write(
      to: directory.appendingPathComponent("context.json"), options: .atomic)
    context = next
    // Same account: the committed manifest keeps playing until its successor arrives.
    if changedAccount || next.account == nil {
      appliedRevision = nil
      try? FileManager.default.removeItem(at: directory.appendingPathComponent("applied.json"))
      try? FileManager.default.removeItem(at: directory.appendingPathComponent("listens.json"))
      try? FileManager.default.removeItem(at: directory.appendingPathComponent("requests.json"))
      removed = []
      try JSONEncoder().encode(removed).write(
        to: directory.appendingPathComponent("removed.json"), options: .atomic)
      manifest = CompanionManifest(context: next, songs: [])
      try persist()
      try clean()
    }
  }
  public func apply(_ next: CompanionManifest) throws {
    guard next.context == context, context.account != nil else { throw CompanionFailure.stale }
    guard next.songs.count <= 1000, Set(next.songs.map(\.id)).count == next.songs.count,
      next.collections.count <= 200,
      next.collections.allSatisfy({ $0.songIds.count <= 10000 }),
      next.songs.allSatisfy({
        Self.validChecksum($0.checksum) && $0.bytes > 0 && $0.bytes <= 256 * 1024 * 1024
      })
    else { throw CompanionFailure.invalid }
    let previous = manifest
    manifest = next
    do { try persist() } catch {
      manifest = previous
      throw error
    }
    removed.formIntersection(Set(next.songs.map(\.id)))
    try JSONEncoder().encode(removed).write(
      to: directory.appendingPathComponent("removed.json"), options: .atomic)
    try JSONEncoder().encode(context.revision).write(
      to: directory.appendingPathComponent("applied.json"), options: .atomic)
    appliedRevision = context.revision
    let selected = Set(next.songs.map(\.id))
    let requests = pendingRequests().filter { id, action in
      action == "download" ? !selected.contains(id) : selected.contains(id)
    }
    try JSONEncoder().encode(requests).write(
      to: directory.appendingPathComponent("requests.json"), options: .atomic)
    try clean()
  }
  public func removeLocal(id: String) throws {
    removed.insert(id)
    try JSONEncoder().encode(removed).write(
      to: directory.appendingPathComponent("removed.json"), options: .atomic)
    try clean()
  }
  private func persist() throws {
    try JSONEncoder().encode(manifest).write(
      to: directory.appendingPathComponent("manifest.json"), options: .atomic)
  }
  private static func validChecksum(_ value: String) -> Bool {
    value.count == 64 && value.allSatisfy { "0123456789abcdef".contains($0) }
  }
  private func url(_ checksum: String) -> URL {
    directory.appendingPathComponent(checksum + ".m4a")
  }
  private func clean() throws {
    let kept = Set(manifest.songs.filter { !removed.contains($0.id) }.map(\.checksum))
    if arrivals.keys.contains(where: { !kept.contains($0) }) {
      arrivals = arrivals.filter { kept.contains($0.key) }
      saveArrivals()
    }
    let keep = Set(kept.map { $0 + ".m4a" })
    for file in try FileManager.default.contentsOfDirectory(
      at: directory, includingPropertiesForKeys: nil)
    where file.pathExtension == "m4a" && !keep.contains(file.lastPathComponent) {
      try FileManager.default.removeItem(at: file)
    }
  }
  /// A playable file from the committed library, including while a newer revision
  /// for the same account is staged.
  public func localFile(id: String) -> URL? {
    guard let account = context.account, manifest.context.account == account,
      appliedRevision == manifest.context.revision, !removed.contains(id),
      let song = manifest.songs.first(where: { $0.id == id }),
      Self.validChecksum(song.checksum),
      let size = try? url(song.checksum).resourceValues(forKeys: [.fileSizeKey]).fileSize,
      Int64(size) == song.bytes
    else { return nil }
    return url(song.checksum)
  }
  /// The same rule as `localFile` for every song at once: one folder listing
  /// instead of a manifest search and a file check per song (#71).
  public func receipt() -> CompanionReceipt {
    var ready: [String: String] = [:]
    var sizes: [String: Int64] = [:]
    var times: [String: Date] = [:]
    if let account = context.account, manifest.context.account == account,
      appliedRevision == manifest.context.revision
    {
      let stored = storedSizes()
      for song in manifest.songs
      where !removed.contains(song.id) && Self.validChecksum(song.checksum)
        && stored[song.checksum] == song.bytes
      {
        ready[song.id] = song.checksum
        sizes[song.checksum] = song.bytes
        if let time = arrivals[song.checksum] { times[song.id] = time }
      }
    }
    return CompanionReceipt(
      context: context, ready: ready, usedBytes: sizes.values.reduce(0, +),
      budgetBytes: budgetBytes,
      manifestApplied: appliedRevision == context.revision, arrivals: times
    )
  }
  /// The phone announces a new revision before the songs already on their way
  /// and sends its manifest after them (#64). A song the committed library lists
  /// is stored at once; one it doesn't wait for the announced manifest.
  public func arrival(of ticket: CompanionFile) -> CompanionArrival {
    guard let account = context.account, ticket.context.account == account,
      ticket.context.version == 1, ticket.context.revision <= context.revision,
      !removed.contains(ticket.songId)
    else { return .stale }
    if committedSong(for: ticket) != nil { return .accept }
    return appliedRevision == context.revision ? .stale : .wait
  }
  private func committedSong(for ticket: CompanionFile) -> CompanionSong? {
    guard manifest.context.account == context.account,
      appliedRevision == manifest.context.revision
    else { return nil }
    return manifest.songs.first(where: {
      $0.id == ticket.songId && $0.checksum == ticket.checksum
    })
  }
  private func storedSizes() -> [String: Int64] {
    let files =
      (try? FileManager.default.contentsOfDirectory(
        at: directory, includingPropertiesForKeys: [.fileSizeKey])) ?? []
    var sizes: [String: Int64] = [:]
    for file in files where file.pathExtension == "m4a" {
      if let size = try? file.resourceValues(forKeys: [.fileSizeKey]).fileSize {
        sizes[file.deletingPathExtension().lastPathComponent] = Int64(size)
      }
    }
    return sizes
  }
  /// Called after the delegate synchronously takes ownership of the temporary file.
  public func accept(_ file: URL, ticket: CompanionFile) throws {
    guard arrival(of: ticket) == .accept, let song = committedSong(for: ticket)
    else { throw CompanionFailure.stale }
    guard Self.validChecksum(ticket.checksum),
      let size = try file.resourceValues(forKeys: [.fileSizeKey]).fileSize,
      Int64(size) == song.bytes
    else { throw CompanionFailure.integrity }
    let used = receipt().usedBytes
    let exists = FileManager.default.fileExists(atPath: url(ticket.checksum).path)
    guard exists || used + song.bytes <= budgetBytes else { throw CompanionFailure.quota }
    let capacity = try VolumeSpace.available(at: directory)
    // Apple Watch itself is full, not the Storage limit: told apart so the
    // Watch doesn't point to a limit that wouldn't help.
    guard exists || capacity == nil || capacity! > song.bytes + 32 * 1024 * 1024 else {
      throw CompanionArchiveFailure.deviceFull
    }
    let handle = try FileHandle(forReadingFrom: file)
    defer { try? handle.close() }
    var digest = SHA256()
    while let bytes = try handle.read(upToCount: 256 * 1024), !bytes.isEmpty {
      digest.update(data: bytes)
    }
    guard digest.finalize().map({ String(format: "%02x", $0) }).joined() == ticket.checksum else {
      throw CompanionFailure.integrity
    }
    let temporary = directory.appendingPathComponent(ticket.checksum + ".partial")
    try FileManager.default.moveItem(at: file, to: temporary)
    if exists {
      _ = try FileManager.default.replaceItemAt(url(ticket.checksum), withItemAt: temporary)
    } else {
      try FileManager.default.moveItem(at: temporary, to: url(ticket.checksum))
    }
    arrivals[ticket.checksum] = now()
    saveArrivals()
  }
  private func saveArrivals() {
    try? JSONEncoder().encode(arrivals).write(
      to: directory.appendingPathComponent("arrivals.json"), options: .atomic)
  }
}
