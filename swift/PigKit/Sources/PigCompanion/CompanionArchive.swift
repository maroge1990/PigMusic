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

/// A bounded, credential-free Watch archive. Removal is driven by a confirmed phone context.
///
/// The last applied manifest is the committed, playable library. A new revision for
/// the same account is staged: its context arrives first, and the committed songs
/// stay playable until the matching manifest replaces them. A different or signed-out
/// account revokes the library at once.
public actor CompanionArchive {
  private let directory: URL
  private var manifest: CompanionManifest
  private var context: CompanionContext
  private var appliedRevision: Int64?
  private var removed: Set<String>
  public private(set) var budgetBytes: Int64
  public init(directory: URL, budgetBytes: Int64 = 1024 * 1024 * 1024) throws {
    self.directory = directory
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
    self.budgetBytes =
      (try? decoder.decode(
        Int64.self, from: Data(contentsOf: directory.appendingPathComponent("budget.json"))))
      ?? budgetBytes
    let loadedContext =
      (try? decoder.decode(
        CompanionContext.self,
        from: Data(contentsOf: directory.appendingPathComponent("context.json"))))
      ?? CompanionContext(revision: 0, account: nil)
    context = loadedContext
    let loadedManifest =
      (try? decoder.decode(
        CompanionManifest.self,
        from: Data(contentsOf: directory.appendingPathComponent("manifest.json"))))
      ?? CompanionManifest(context: loadedContext, songs: [])
    // A committed library from the same account stays playable while a newer
    // revision is on its way; anything else is revoked.
    let sameAccount =
      loadedContext.account != nil && loadedManifest.context.account == loadedContext.account
      && loadedManifest.context.revision <= loadedContext.revision
    manifest = sameAccount ? loadedManifest : CompanionManifest(context: loadedContext, songs: [])
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
    let keep = Set(manifest.songs.filter { !removed.contains($0.id) }.map { $0.checksum + ".m4a" })
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
      }
    }
    return CompanionReceipt(
      context: context, ready: ready, usedBytes: sizes.values.reduce(0, +),
      budgetBytes: budgetBytes,
      manifestApplied: appliedRevision == context.revision
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
    let capacity = try directory.resourceValues(forKeys: [.volumeAvailableCapacityKey])
      .volumeAvailableCapacity
    guard exists || capacity == nil || Int64(capacity!) > song.bytes + 32 * 1024 * 1024 else {
      throw CompanionFailure.quota
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
  }
}
