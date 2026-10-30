import PigAPI
import Foundation
import Observation
import PigBrand
import PigCompanion
import PigDomain
import PigDownloads
import SwiftUI

@MainActor @Observable final class WatchPhoneController {
  var link: CompanionLink?
  var downloads: DownloadController?
  var receipt: CompanionReceipt?
  var message: String?
  var context: CompanionContext
  /// When the Watch last confirmed the current library.
  var confirmedAt: Date?
  /// The Watch's last lifecycle log, for the diagnostics export.
  var watchDiagnostics: CompanionDiagnostics? = WatchPhoneController.loadWatchDiagnostics()
  /// Playlists sent to the Watch, by id with their last known name. Saved on their
  /// own so an emptied or newly sent empty playlist keeps following its changes.
  private(set) var subscriptions: [String: String] = [:]
  /// Songs taken off the Watch from a followed playlist, so following its
  /// changes does not send them back. Cleared when the song leaves the
  /// playlist or the playlist is sent again.
  private var exclusions: [String: Set<String>] = [:]
  @ObservationIgnored private weak var model: AppModel?
  @ObservationIgnored private let defaults: UserDefaults
  @ObservationIgnored private var namespace: String?
  @ObservationIgnored private var verified = false
  @ObservationIgnored private var poll: Task<Void, Never>?
  @ObservationIgnored private var published: CompanionManifest?
  @ObservationIgnored private var manifestURL: URL?
  /// The Watch's last confirmation, kept so a relaunch does not count every
  /// song as still to send until the Watch answers again.
  @ObservationIgnored private var receiptURL: URL?
  @ObservationIgnored private var subscriptionsURL: URL?
  @ObservationIgnored private var generation = 0
  @ObservationIgnored private var peerRevision: Int64 = 0
  @ObservationIgnored private var publishing = false
  /// The ready songs and their files from the last publish, with what they
  /// were built from. The two-second poll reuses them while nothing changed
  /// instead of checking every file again (RV23).
  @ObservationIgnored private var readyCache:
    (key: [String], archive: Int, songs: [CompanionSong], files: [String: URL])?
  /// When each manifest or song was last handed to WatchConnectivity.
  @ObservationIgnored private var sent = CompanionSendLog()
  @ObservationIgnored private var lastAdvance = Date.distantPast
  /// The context last handed to WatchConnectivity, which keeps delivering it.
  @ObservationIgnored private var contextSent: CompanionContext?
  @ObservationIgnored private var deferredPublish: Task<Void, Never>?
  init(model: AppModel, defaults: UserDefaults) {
    self.model = model
    self.defaults = defaults
    context = CompanionContext(
      revision: Int64(defaults.integer(forKey: "pig.watch.revision")),
      account: defaults.string(forKey: "pig.watch.account"))
    do {
      let base = try FileManager.default.url(
        for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true
      ).appendingPathComponent("WatchBridge")
      link = try CompanionLink(inbox: base.appendingPathComponent("Inbox"))
      link?.onActivated = { [weak self] in
        guard let self else { return }
        // Reannounce the small context so a reinstalled Watch can report what
        // it needs. Songs already handed over keep their marks (#70): they are
        // confirmed by the next receipt or retried after ten minutes.
        sent.forgetManifests()
        contextSent = nil
        await publish()
      }
      link?.onMessage = { [weak self] data in
        if let diagnostics = CompanionDiagnostics.decode(data), diagnostics.text != nil {
          self?.received(diagnostics)
          return
        }
        if let acknowledgement = try? JSONDecoder().decode(
          CompanionAcknowledgement.self, from: data),
          acknowledgement.context.version == 1,
          acknowledgement.context.revision >= 0,
          acknowledgement.context.revision < 9_000_000_000_000_000,
          let self
        {
          peerRevision = max(peerRevision, acknowledgement.context.revision)
          if verified, context.revision < peerRevision {
            link?.cancelTransfers()
            sent.reset()
            published = nil
            receipt = nil
            advance(account: namespace)
            await publish(force: true)
          } else if let manifest = published,
            let confirmed = try? acknowledgement.receipt(for: manifest),
            confirmed.context == context
          {
            if receipt?.ready.count != confirmed.ready.count || receipt?.context != confirmed.context {
              DiagnosticsLog.shared.record(
                "watch",
                "Watch confirmed revision \(confirmed.context.revision): \(confirmed.ready.count) songs ready, manifest applied=\(confirmed.manifestApplied)"
              )
            }
            receipt = confirmed
            confirmedAt = Date()
            saveReceipt()
            downloads?.budgetBytes = max(2 * 1024 * 1024 * 1024, confirmed.budgetBytes)
            await publish()
          }
        } else if let command = try? JSONDecoder().decode(CompanionCommand.self, from: data) {
          if let reply = await self?.handle(command), let encoded = try? JSONEncoder().encode(reply)
          {
            self?.link?.message(encoded)
          }
        }
      }
      link?.onRequest = { [weak self] data in
        let reply: CompanionReply
        if let command = try? JSONDecoder().decode(CompanionCommand.self, from: data), let self {
          reply = await self.handle(command)
        } else {
          reply = CompanionReply(message: "Invalid Watch request.")
        }
        return (try? JSONEncoder().encode(reply)) ?? Data()
      }
    } catch { message = error.userMessage }
  }
  func configure(directory: URL, namespace: String) async throws {
    generation += 1
    poll?.cancel()
    // Not pause(): that saves a user pause the background session would keep.
    await downloads?.suspendPreparation()
    self.namespace = namespace
    verified = false
    published = nil
    receipt = nil
    // AAC copies download through a background session, so preparing music for
    // the Watch continues while the phone is locked.
    let identifier = BackgroundTransferHub.watchIdentifierPrefix + namespace
    let archive =
      try BackgroundTransferHub.archive(for: identifier)
      ?? DownloadArchive(directory: directory.appendingPathComponent("WatchAAC"))
    guard let service = model?.service else { return }
    readyCache = nil
    downloads = DownloadController(archive: archive, source: service) { [weak self] error in
      self?.message = error.userMessage
    }
    downloads?.budgetBytes = max(2 * 1024 * 1024 * 1024, receipt?.budgetBytes ?? 0)
    try downloads?.enableBackground(
      identifier: identifier,
      directory: directory.appendingPathComponent("WatchBackgroundTransfers"))
    // A copy finished in the background goes on to the Watch straight away.
    downloads?.onChange = { [weak self] in await self?.publish() }
    await downloads?.reload()
    subscriptionsURL = directory.appendingPathComponent("watch-playlists.json")
    subscriptions =
      subscriptionsURL.flatMap { try? Data(contentsOf: $0) }
      .flatMap { try? JSONDecoder().decode([String: String].self, from: $0) } ?? [:]
    exclusions =
      exclusionsURL.flatMap { try? Data(contentsOf: $0) }
      .flatMap { try? JSONDecoder().decode([String: Set<String>].self, from: $0) } ?? [:]
    manifestURL = directory.appendingPathComponent("watch-manifest.json")
    receiptURL = directory.appendingPathComponent("watch-receipt.json")
    let previousManifest = directory.appendingPathComponent(
      "watch-manifest-\(context.revision).json")
    if context.account == namespace,
      let data = try? Data(contentsOf: previousManifest),
      let old = try? JSONDecoder().decode(CompanionManifest.self, from: data),
      old.context == context
    {
      published = old
      if let saved = receiptURL.flatMap({ try? Data(contentsOf: $0) })
        .flatMap({ try? JSONDecoder().decode(SavedReceipt.self, from: $0) }),
        saved.receipt.context == context
      {
        receipt = saved.receipt
        confirmedAt = saved.at
        downloads?.budgetBytes = max(2 * 1024 * 1024 * 1024, saved.receipt.budgetBytes)
      }
    }
    startPolling()
  }
  /// Foreground only. Without an installed Watch app there is nothing to
  /// deliver, so the bridge checks back slowly instead of every two seconds.
  private func startPolling() {
    poll?.cancel()
    guard namespace != nil else { return }
    let stamp = generation
    poll = Task { [weak self] in
      while !Task.isCancelled {
        guard let self, generation == stamp else { return }
        link?.refreshState()
        let paired = link?.available == true
        if paired {
          await downloads?.reload()
          await publish()
        }
        do { try await Task.sleep(for: .seconds(paired ? 2 : 30)) } catch { return }
      }
    }
  }
  func resume() {
    link?.refreshState()
    // Watch AAC preparation restarts only after the library was reconciled.
    if verified { downloads?.resumePreparation() }
    startPolling()
  }
  /// Retry: reconnect to the Watch when activation failed, then resend everything.
  func retry() async {
    message = nil
    link?.reconnect()
    if verified { downloads?.resume() }
    await publish(force: true)
  }
  func confirmAccount() {
    if context.account != namespace || context.revision < peerRevision {
      link?.cancelTransfers()
      sent.reset()
      advance(account: namespace)
      try? link?.publish(context)
    }
  }
  func reconcile() async throws {
    guard let downloads, let store = model?.store else { return }
    let stamp = generation
    // One store query for every Watch song (each song's own query before).
    let allowed = try await store.sourceRevisions(ids: downloads.records.map(\.id))
    guard generation == stamp else { return }
    await downloads.reconcile(allowed)
    guard generation == stamp else { return }
    verified = true
    await followPlaylists()
    guard generation == stamp else { return }
    downloads.resume()
    await publish()
  }
  var supported: Bool { link?.supported == true }
  /// Returns what happened, for the screen where Send to Apple Watch was tapped.
  @discardableResult func enqueue(_ tracks: [Track], origin: DownloadOrigin = .direct) async
    -> String?
  {
    guard supported else {
      message = "Watch downloads are managed by its paired iPhone."
      return message
    }
    guard verified, let downloads else {
      message = "Connect and refresh your phone library first."
      return message
    }
    if let id = origin.playlistId {
      subscribe(id, name: origin.playlistName)
      exclude([], from: id, sending: Set(tracks.map(\.id)))
      if tracks.isEmpty {
        await publish()
        return "\(origin.playlistName ?? "This playlist") will follow on your Watch: songs you add are sent automatically."
      }
    }
    guard
      downloads.records.count
        + Set(tracks.map(\.id)).subtracting(Set(downloads.records.map(\.id))).count
        <= 1000
    else {
      message = "Choose up to 1,000 songs for your Watch library."
      return message
    }
    let stamp = generation
    do {
      var personal: [Track] = []
      for id in Set(tracks.map(\.id)) {
        if let track = try await model?.store?.track(id: id) { personal.append(track) }
      }
      guard generation == stamp else { return nil }
      guard !personal.isEmpty else {
        message = "Add these songs to your library before downloading them to Watch."
        return message
      }
      try await downloads.enqueue(personal, quality: "aac", origin: origin)
      var result =
        "Sending \(personal.count) song\(personal.count == 1 ? "" : "s") to Apple Watch. Follow progress in Downloads › Apple Watch."
      if personal.count < Set(tracks.map(\.id)).count {
        message =
          "Only songs in your library can be sent to Watch. Add or request the remaining songs first."
        result += " " + (message ?? "")
      }
      if link?.activated == true, link?.installed == false {
        result += " PigMusic isn't on your Watch yet: install it from the Watch app on this iPhone."
      }
      await publish()
      return result
    } catch {
      message = error.userMessage
      return message
    }
  }
  func remove(_ id: String) async {
    // Removed from the Watch: followed playlists must not send it back.
    for playlist in downloads?.records.first(where: { $0.id == id })?.effectiveOrigins
      .compactMap(\.playlistId) ?? []
    {
      exclude([id], from: playlist)
    }
    await downloads?.remove(id: id)
    await publish()
  }
  /// Removes an album's or playlist's claim; songs another one needs stay.
  func release(_ ids: [String], origin: DownloadOrigin) async {
    await downloads?.release(ids: ids, origin: origin)
    // Removing a whole playlist from the Watch stops following it; removing
    // some of its songs keeps following it without them.
    if let id = origin.playlistId {
      if (downloads?.records ?? []).contains(where: {
        $0.effectiveOrigins.contains { $0.playlistId == id }
      }) {
        exclude(Set(ids), from: id)
      } else {
        unsubscribe(id)
      }
    }
    await publish()
  }
  private func subscribe(_ id: String, name: String?) {
    guard subscriptions[id] != name ?? "" else { return }
    subscriptions[id] = name ?? ""
    saveSubscriptions()
  }
  private func unsubscribe(_ id: String) {
    if exclusions.removeValue(forKey: id) != nil { saveExclusions() }
    guard subscriptions.removeValue(forKey: id) != nil else { return }
    saveSubscriptions()
  }
  /// Adds songs to a playlist's exclusions, and clears any being sent again.
  private func exclude(_ ids: Set<String>, from playlist: String, sending: Set<String> = []) {
    let excluded = exclusions[playlist, default: []].union(ids).subtracting(sending)
    guard excluded != exclusions[playlist, default: []] else { return }
    exclusions[playlist] = excluded.isEmpty ? nil : excluded
    saveExclusions()
  }
  private var exclusionsURL: URL? {
    subscriptionsURL?.deletingLastPathComponent().appendingPathComponent(
      "watch-playlist-exclusions.json")
  }
  private func saveExclusions() {
    guard let exclusionsURL, let data = try? JSONEncoder().encode(exclusions) else { return }
    try? data.write(to: exclusionsURL, options: .atomic)
  }
  private struct SavedReceipt: Codable {
    var receipt: CompanionReceipt
    var at: Date
  }
  private func saveReceipt() {
    guard let receiptURL, let receipt,
      let data = try? JSONEncoder().encode(SavedReceipt(receipt: receipt, at: confirmedAt ?? Date()))
    else { return }
    try? data.write(to: receiptURL, options: .atomic)
  }
  private func saveSubscriptions() {
    guard let subscriptionsURL, let data = try? JSONEncoder().encode(subscriptions) else { return }
    try? data.write(to: subscriptionsURL, options: .atomic)
  }
  /// Every playlist on the Watch: saved subscriptions, plus any inferred from songs
  /// sent before subscriptions were saved.
  private var followedPlaylists: Set<String> {
    Set(subscriptions.keys).union(
      (downloads?.records ?? []).flatMap { $0.effectiveOrigins.compactMap(\.playlistId) })
  }
  /// Playlists sent to the Watch follow later changes: new songs are added,
  /// removed songs are released, and a deleted playlist leaves the Watch.
  /// Runs only from `reconcile`, after a successful sync, so an empty list means
  /// the person has no playlists left and the last one's songs are released.
  private func followPlaylists() async {
    guard let downloads, let model, let store = model.store else { return }
    var sent: [String: WatchPlaylistPlan.Sent] = [:]
    for record in downloads.records {
      for origin in record.effectiveOrigins {
        guard let id = origin.playlistId else { continue }
        sent[id, default: .init(name: origin.playlistName, songIds: [])].songIds.insert(record.id)
      }
    }
    var listed: [String: WatchPlaylistPlan.Listed] = [:]
    for playlist in model.playlists {
      listed[playlist.id] = .init(
        name: playlist.name,
        songIds: playlist.entries?.compactMap { $0.savedId ?? $0.savedTrack?.id })
    }
    // The rules live in PigCompanion, where they are unit-tested.
    let plan = WatchPlaylistPlan.make(
      subscriptions: subscriptions, exclusions: exclusions, sent: sent, playlists: listed,
      onDevice: Set(downloads.records.map(\.id)))
    for change in plan.release {
      await downloads.release(
        ids: change.songIds,
        origin: DownloadOrigin(playlistId: change.playlistId, playlistName: change.name))
    }
    for change in plan.send {
      var tracks: [Track] = []
      for songId in change.songIds {
        if let track = try? await store.track(id: songId) { tracks.append(track) }
      }
      if !tracks.isEmpty {
        try? await downloads.enqueue(
          tracks, quality: "aac",
          origin: DownloadOrigin(playlistId: change.playlistId, playlistName: change.name))
      }
    }
    if plan.subscriptions != subscriptions {
      subscriptions = plan.subscriptions
      saveSubscriptions()
    }
    if plan.exclusions != exclusions {
      exclusions = plan.exclusions
      saveExclusions()
    }
  }
  /// The app left the foreground: stop polling and server-side preparation, but
  /// leave the system's background downloads and Watch transfers running.
  func suspend() async {
    poll?.cancel()
    poll = nil
    await downloads?.suspendPreparation()
  }
  func disconnect() {
    generation += 1
    poll?.cancel()
    poll = nil
    if let downloads { Task { await downloads.clear() } }
    downloads = nil
    readyCache = nil
    verified = false
    namespace = nil
    receipt = nil
    confirmedAt = nil
    published = nil
    subscriptions = [:]
    exclusions = [:]
    subscriptionsURL = nil
    if let receiptURL { try? FileManager.default.removeItem(at: receiptURL) }
    receiptURL = nil
    sent.reset()
    link?.cancelTransfers()
    advance(account: nil)
    try? link?.publish(context)
  }
  private func advance(account: String?) {
    context = CompanionContext(revision: max(context.revision, peerRevision) + 1, account: account)
    defaults.set(context.revision, forKey: "pig.watch.revision")
    defaults.set(account, forKey: "pig.watch.account")
  }
  func publish(force: Bool = false) async {
    guard !publishing else { return }
    guard verified, let downloads, let namespace, let baseManifestURL = manifestURL else {
      if context.account == nil { try? link?.publish(context) }
      return
    }
    if force { sent.reset() }
    publishing = true
    defer { publishing = false }
    let stamp = generation
    var songs: [CompanionSong] = []
    var files: [String: URL] = [:]
    let ready = downloads.records.filter { $0.state == "ready" && $0.checksum != nil }
    let key = ready.map { "\($0.id):\($0.checksum ?? ""):\($0.track.sourceRevision ?? "")" }
    let archiveRevision = await downloads.archive.revision
    if !force, let cache = readyCache, cache.key == key, cache.archive == archiveRevision {
      songs = cache.songs
      files = cache.files
    } else {
      // One indexed lookup for every ready song, not a search per song.
      files = await downloads.archive.localFiles(ready.map(\.track))
      for record in ready {
        if let checksum = record.checksum, files[record.id] != nil {
          songs.append(CompanionSong(track: record.track, checksum: checksum, bytes: record.total))
        }
      }
      songs.sort { $0.id < $1.id }
      readyCache = (key, archiveRevision, songs, files)
    }
    guard generation == stamp else { return }
    let ids = Set(songs.map(\.id))
    var remainingOccurrences = 10000
    // Only playlists sent to the Watch appear there, including empty ones.
    let sentPlaylists = followedPlaylists
    var collections = (model?.playlists ?? []).filter { sentPlaylists.contains($0.id) }.prefix(200)
      .map { playlist in
      let selected = (playlist.entries ?? []).compactMap { $0.savedId ?? $0.savedTrack?.id }.filter
      { ids.contains($0) }
      let songIds = Array(selected.prefix(remainingOccurrences))
      remainingOccurrences -= songIds.count
      return CompanionCollection(
        id: playlist.id, name: playlist.name, songIds: songIds,
        favourites: playlist.favourites == true)
    }
    let batchActive =
      downloads.running || downloads.records.contains { ["queued", "downloading"].contains($0.state) }
    let sameAccount = context.account == namespace && published?.context.account == namespace
    let current = Set(songs)
    let growthOnly =
      sameAccount
      && (published?.songs.allSatisfy { current.contains($0) } ?? false)
    // While a batch is still preparing, the Watch gets a new library version
    // at most every 45 seconds instead of one per finished song: each version
    // must be acknowledged before more audio is sent. Removals go at once.
    if batchActive, growthOnly, !force, Date().timeIntervalSince(lastAdvance) < 45 {
      deferPublish(after: 45 - Date().timeIntervalSince(lastAdvance))
      songs = published?.songs ?? []
      collections = published?.collections ?? []
    }
    let folder = baseManifestURL.deletingLastPathComponent()
    var manifestURL = folder.appendingPathComponent("watch-manifest-\(context.revision).json")
    var next = CompanionManifest(context: context, songs: songs, collections: collections)
    if context.account != namespace || published?.songs != songs
      || published?.collections != collections
    {
      if sameAccount {
        // Same account: songs already on their way stay valid for the new
        // version (the Watch accepts earlier tickets for songs it still
        // lists). Only superseded manifests and songs that left are cancelled.
        let keep = Set(songs.map { "\($0.id):\($0.checksum)" })
        link?.cancelTransfers { data in
          guard let envelope = try? JSONDecoder().decode(CompanionEnvelope.self, from: data)
          else { return false }
          if envelope.kind == "manifest" { return true }
          guard let file = envelope.file else { return false }
          return !keep.contains("\(file.songId):\(file.checksum)")
        }
        sent.forgetManifests()
      } else {
        link?.cancelTransfers()
        sent.reset()
      }
      advance(account: namespace)
      lastAdvance = Date()
      DiagnosticsLog.shared.record(
        "watch",
        "publishing revision \(context.revision): \(songs.count) songs, \(collections.count) playlists")
      next.context = context
      manifestURL = folder.appendingPathComponent("watch-manifest-\(context.revision).json")
      receipt = nil
      published = next
      do {
        let data = try JSONEncoder().encode(next)
        guard data.count <= 2 * 1024 * 1024 else {
          published = nil
          message = "Watch metadata is too large. Choose fewer songs or shorter playlist names."
          return
        }
        try data.write(to: manifestURL, options: .atomic)
      } catch {
        published = nil
        message = error.userMessage
        return
      }
    }
    // The Watch confirms every context it receives, and each confirmation
    // publishes again: send the context only when it changed (or when asked).
    if force || contextSent != context {
      do { try link?.publish(context) } catch {
        message = error.userMessage
        return
      }
      contextSent = context
    }
    let outstanding = (link?.outstanding ?? []).compactMap {
      try? JSONDecoder().decode(CompanionEnvelope.self, from: $0)
    }
    func due(_ key: String) -> Bool { sent.due(key) }
    if force || receipt?.context != context || receipt?.manifestApplied != true {
      let metadata = CompanionEnvelope(kind: "manifest", context: context)
      let key = "manifest:\(context.revision)"
      if let data = try? JSONEncoder().encode(metadata), due(key),
        !outstanding.contains(where: { $0.kind == "manifest" && $0.context == context })
      {
        link?.transfer(manifestURL, metadata: data)
        sent.mark(key)
      }
    }
    // Manifest files stay a few versions longer than their transfers, so a
    // transfer the system is still finishing never loses its file.
    let retained = Set(outstanding.map(\.context.revision))
    for file
      in (try? FileManager.default.contentsOfDirectory(
        at: folder, includingPropertiesForKeys: nil)) ?? []
    where file.lastPathComponent.hasPrefix("watch-manifest-") && file != manifestURL {
      let revision = Int64(
        file.deletingPathExtension().lastPathComponent.replacingOccurrences(
          of: "watch-manifest-", with: ""))
      if let revision, !retained.contains(revision), revision < context.revision - 3 {
        try? FileManager.default.removeItem(at: file)
      }
    }
    // Do not send audio until the Watch has applied this manifest and acknowledged its budget.
    guard let receipt, receipt.context == context, receipt.manifestApplied else { return }
    let inFlight = Set(outstanding.compactMap { $0.file.map { "\($0.songId):\($0.checksum)" } })
    var remaining = receipt.budgetBytes - receipt.usedBytes
    for song in songs where receipt.ready[song.id] != song.checksum {
      guard song.bytes <= remaining else {
        message = "Watch storage budget is full. Remove Watch songs before adding more."
        continue
      }
      remaining -= song.bytes
      let identity = "\(song.id):\(song.checksum)"
      guard !inFlight.contains(identity), due("audio:" + identity), let file = files[song.id]
      else { continue }
      let ticket = CompanionFile(context: context, songId: song.id, checksum: song.checksum)
      let envelope = CompanionEnvelope(kind: "audio", context: context, file: ticket)
      if let data = try? JSONEncoder().encode(envelope) {
        link?.transfer(file, metadata: data)
        sent.mark("audio:" + identity)
      }
    }
  }
  /// Publishes again once the batch interval has passed.
  private func deferPublish(after seconds: TimeInterval) {
    guard deferredPublish == nil else { return }
    let stamp = generation
    deferredPublish = Task { [weak self] in
      try? await Task.sleep(for: .seconds(max(1, seconds)))
      guard let self, generation == stamp else { return }
      deferredPublish = nil
      await publish()
    }
  }
  /// Asks the Watch for its lifecycle log; it answers when it can.
  func requestWatchDiagnostics() {
    if let data = try? JSONEncoder().encode(CompanionDiagnostics()) { link?.message(data) }
  }
  private static var diagnosticsFile: URL? {
    FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first?
      .appendingPathComponent("Diagnostics/watch.json")
  }
  private static func loadWatchDiagnostics() -> CompanionDiagnostics? {
    diagnosticsFile.flatMap { try? Data(contentsOf: $0) }.flatMap {
      try? JSONDecoder().decode(CompanionDiagnostics.self, from: $0)
    }
  }
  private func received(_ diagnostics: CompanionDiagnostics) {
    watchDiagnostics = diagnostics
    DiagnosticsLog.shared.record(
      "watch",
      "Watch log received (\(diagnostics.version ?? "unknown version"))"
        + (diagnostics.endedUnexpectedly ? "; its previous run ended unexpectedly" : ""))
    if let file = Self.diagnosticsFile, let data = try? JSONEncoder().encode(diagnostics) {
      try? data.write(to: file, options: .atomic)
    }
  }
  private func handle(_ command: CompanionCommand) async -> CompanionReply {
    guard verified, command.account == context.account, let model, let store = model.store else {
      return CompanionReply(message: "Refresh the library on your phone first.")
    }
    let stamp = generation
    do {
      switch command.action {
      case "listen":
        guard let event = command.listen, UUID(uuidString: event.id) != nil,
          event.seconds == 30, event.playedAt > Int(Date().timeIntervalSince1970) - 30 * 86400,
          event.playedAt <= Int(Date().timeIntervalSince1970) + 300,
          try await store.track(id: event.trackId) != nil
        else {
          return CompanionReply(listenId: command.listen?.id, account: command.account)
        }
        try await store.recordListen(event)
        await model.flushHistory()
        return CompanionReply(listenId: event.id, account: command.account)
      case "download":
        guard command.songIds.count <= 100 else {
          return CompanionReply(message: "Choose up to 100 songs at a time.")
        }
        var tracks: [Track] = []
        for id in command.songIds {
          if let track = try await store.track(id: id) { tracks.append(track) }
        }
        guard generation == stamp, verified, command.account == context.account else {
          return CompanionReply(message: "The phone account changed. Retry after syncing.")
        }
        await enqueue(tracks)
        return CompanionReply(
          message: "Queued on your phone. AAC files transfer when both devices are available.")
      case "remove":
        for id in command.songIds.prefix(100) {
          guard generation == stamp, command.account == context.account else {
            return CompanionReply(message: "The phone account changed.")
          }
          await remove(id)
        }
      case "playPause": model.player.toggle()
      case "next": model.player.skip()
      case "previous": model.player.previous()
      case "sync": await publish(force: true)
      default: return CompanionReply(message: "Unsupported Watch command.")
      }
      return CompanionReply()
    } catch { return CompanionReply(message: error.localizedDescription) }
  }
}

/// Music sent to Apple Watch: transfers first, then what is on the Watch grouped
/// by album and playlist. Music is chosen from album, artist, playlist and song pages.
struct WatchDownloadsView: View {
  let model: AppModel
  @State private var removing: DeviceGroup?
  var body: some View {
    List {
      if let watch = model.watch {
        let records = watch.downloads?.records ?? []
        let delivered = records.filter { onWatch($0, watch: watch) }
        let transfers = records.filter { !onWatch($0, watch: watch) || $0.cleared != true }
        if records.isEmpty {
          Text("Nothing sent yet. Use Send to Apple Watch on an album, artist, playlist or song.")
            .foregroundStyle(.secondary)
        } else {
          Section { readiness(records: records, delivered: delivered, watch: watch) }
        }
        if !transfers.isEmpty {
          Section("Transfers") {
            ForEach(transfers) { record in
              HStack(spacing: 12) {
                ArtworkThumb(
                  model: model, source: record.track.artworkSource, title: record.track.title)
                VStack(alignment: .leading, spacing: 2) {
                  Text(record.track.title).lineLimit(1)
                  Text("\(status(record, watch: watch)) · \(record.track.artist)")
                    .font(PigType.caption).foregroundStyle(.secondary).lineLimit(1)
                }
                Spacer(minLength: 0)
                mark(record, watch: watch).frame(width: 28, height: 28)
              }
              .accessibilityElement(children: .combine)
              .swipeActions {
                if !onWatch(record, watch: watch) {
                  Button("Cancel", role: .destructive) { Task { await watch.remove(record.id) } }
                }
              }
            }
          }
        }
        let groups = DeviceGroup.build(delivered)
        if !groups.isEmpty {
          Section("On Apple Watch") {
            ForEach(groups) { group in
              DisclosureGroup {
                ForEach(group.records) { record in
                  HStack {
                    Text(record.track.title).lineLimit(1)
                    Spacer(minLength: 0)
                    RemoveButton(label: "Remove \(record.track.title) from Apple Watch") {
                      Task { await watch.release([record.id], origin: group.origin) }
                    }
                  }
                }
              } label: {
                HStack(spacing: 12) {
                  ArtworkThumb(model: model, source: group.artwork, title: group.title, size: 44)
                  VStack(alignment: .leading, spacing: 2) {
                    Text(group.title).lineLimit(1)
                    Text(group.caption).font(PigType.caption).foregroundStyle(.secondary)
                      .lineLimit(1)
                  }
                  Spacer(minLength: 0)
                  RemoveButton(label: "Remove \(group.title) from Apple Watch") { removing = group }
                }
              }
            }
          }
        }
        Section("Status") {
          Text(connection(watch))
          if let receipt = watch.receipt {
            Text(
              "\(receipt.ready.count) songs on Watch · \(ByteCountFormatter.string(fromByteCount: receipt.usedBytes, countStyle: .file))"
            )
          }
          Button("Retry Watch transfers") { Task { await watch.retry() } }
          if let error = watch.message ?? watch.link?.error {
            Text(error).foregroundStyle(Color("PigDanger"))
          }
          Text(
            "Playlists sent to the Watch update when they change. Copies keep downloading to this iPhone while it is locked; the system decides when to pass them to the Watch. AAC copies also use storage on this iPhone."
          ).font(PigType.footnote)
        }
      }
    }
    .pigCanvas().navigationTitle("Apple Watch")
    .toolbar {
      if let watch = model.watch {
        let records = watch.downloads?.records ?? []
        Button("Clear finished") {
          Task {
            await watch.downloads?.clearFinished(
              ids: Set(records.filter { onWatch($0, watch: watch) }.map(\.id)))
          }
        }.disabled(!records.contains { onWatch($0, watch: watch) && $0.cleared != true })
      }
    }
    .confirmationDialog(
      removing.map { "Remove \($0.title) from Apple Watch?" } ?? "",
      isPresented: Binding(get: { removing != nil }, set: { if !$0 { removing = nil } }),
      titleVisibility: .visible
    ) {
      if let group = removing, let watch = model.watch {
        Button("Remove", role: .destructive) {
          Task { await watch.release(group.records.map(\.id), origin: group.origin) }
        }
      }
    } message: {
      Text("Songs also in another album or playlist on the Watch stay there.")
    }
  }
  /// Only songs the Watch confirmed count as ready; queued or prepared on the
  /// phone is never presented as on the Watch.
  @ViewBuilder private func readiness(
    records: [DownloadRecord], delivered: [DownloadRecord], watch: WatchPhoneController
  ) -> some View {
    let failed = records.filter { $0.state == "failed" && !onWatch($0, watch: watch) }.count
    let summary = WatchReadinessSummary(
      ready: delivered.count, waiting: records.count - delivered.count - failed, failed: failed,
      seconds: delivered.reduce(0) { $0 + ($1.track.duration ?? 0) },
      bytes: delivered.reduce(Int64(0)) { $0 + $1.total })
    let waiting = summary.waiting
    VStack(alignment: .leading, spacing: 6) {
      Label(
        summary.complete ? "Ready for your run" : "Almost ready",
        systemImage: summary.complete ? "checkmark.circle.fill" : "hourglass"
      ).font(.headline)
      Text(
        "\(delivered.count) song\(delivered.count == 1 ? "" : "s") on Watch · \(summary.listening) · \(ByteCountFormatter.string(fromByteCount: summary.bytes, countStyle: .file))"
      ).font(PigType.subhead)
      if waiting > 0 {
        Text("\(waiting) still on the way. Keep your iPhone near the Watch.")
          .font(PigType.footnote).foregroundStyle(.secondary)
      }
      if failed > 0 {
        Text("\(failed) failed. Tap the retry mark next to each song.")
          .font(PigType.footnote).foregroundStyle(Color("PigDanger"))
      }
      if let confirmed = watch.confirmedAt {
        Text("Watch confirmed \(confirmed.formatted(.relative(presentation: .named)))")
          .font(PigType.footnote).foregroundStyle(.secondary)
      }
    }
    .accessibilityElement(children: .combine)
  }
  private func connection(_ watch: WatchPhoneController) -> String {
    let link = watch.link
    return WatchConnectionStatus(
      supported: watch.supported && link != nil, activated: link?.activated == true,
      error: link?.error, paired: link?.paired == true, installed: link?.installed == true,
      reachable: link?.reachable == true
    ).text
  }
  private func onWatch(_ record: DownloadRecord, watch: WatchPhoneController) -> Bool {
    record.checksum != nil && watch.receipt?.ready[record.id] == record.checksum
  }
  @ViewBuilder private func mark(_ record: DownloadRecord, watch: WatchPhoneController)
    -> some View
  {
    if onWatch(record, watch: watch) {
      Image(systemName: "checkmark.circle.fill").foregroundStyle(Color.accentColor)
    } else if record.state == "failed" {
      Button {
        Task { await watch.enqueue([record.track], origin: record.effectiveOrigins.last ?? .direct) }
      } label: {
        Image(systemName: "exclamationmark.arrow.circlepath").foregroundStyle(Color("PigDanger"))
      }.buttonStyle(.borderless).accessibilityLabel("Retry")
    } else if record.state == "ready" {
      Image(systemName: "applewatch.radiowaves.left.and.right").foregroundStyle(.secondary)
    } else {
      ProgressView().controlSize(.small)
    }
  }
  private func status(_ record: DownloadRecord, watch: WatchPhoneController) -> String {
    if onWatch(record, watch: watch) { return "On Watch" }
    return switch record.state {
    case "ready": "Waiting for Watch"
    case "failed": "Failed"
    case "downloading": "Downloading to iPhone"
    default: "Preparing on server"
    }
  }
}
