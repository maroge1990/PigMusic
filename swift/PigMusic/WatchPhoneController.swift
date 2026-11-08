import PigAPI
import Foundation
import Observation
import PigBrand
import PigCompanion
import PigDomain
import PigDownloads
import SwiftUI

@MainActor @Observable final class WatchPhoneController {
  /// The Watch's budget until it reports its own (CompanionArchive's default).
  static let watchDefaultBudget: Int64 = 1024 * 1024 * 1024
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
  /// Whether the app is in the foreground: copies are prepared only then (D-51).
  @ObservationIgnored private var foreground = true
  @ObservationIgnored private var published: CompanionManifest?
  @ObservationIgnored private var manifestURL: URL?
  /// The Watch's last confirmation, kept so a relaunch does not count every
  /// song as still to send until the Watch answers again.
  @ObservationIgnored private var receiptURL: URL?
  @ObservationIgnored private var subscriptionsURL: URL?
  /// The phone account's generation; the poll ends with it.
  @ObservationIgnored private let account = AccountFence()
  @ObservationIgnored private var peerRevision: Int64 = 0
  @ObservationIgnored private let publishing = WatchPublishLoop()
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
  @ObservationIgnored private let deferredPublish = DeferredRun()
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
            downloads?.budgetBytes = confirmed.budgetBytes
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
    account.advance()
    account.cancel("poll")
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
    // The Watch's own budget (its default until it reports one): copies beyond
    // it would wait for the Watch for good (#77, D-57).
    downloads?.budgetBytes = receipt?.budgetBytes ?? Self.watchDefaultBudget
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
        downloads?.budgetBytes = saved.receipt.budgetBytes
      }
    }
    startPolling()
  }
  /// Foreground only. Without an installed Watch app there is nothing to
  /// deliver, so the bridge checks back slowly instead of every two seconds.
  private func startPolling() {
    account.cancel("poll")
    guard namespace != nil else { return }
    let stamp = account.generation
    account.own("poll", Task { [weak self] in
      while !Task.isCancelled {
        guard let self, account.isCurrent(stamp) else { return }
        link?.refreshState()
        let paired = link?.available == true
        if paired {
          await downloads?.reload()
          await publish()
        }
        do { try await Task.sleep(for: .seconds(paired ? 2 : 30)) } catch { return }
      }
    })
  }
  func resume() {
    foreground = true
    link?.refreshState()
    // Watch AAC preparation restarts only after the library was reconciled.
    if verified { downloads?.resumePreparation() }
    startPolling()
  }
  /// Retry: reconnect to the Watch when activation failed, then resend everything.
  func retry() async {
    message = nil
    link?.reconnect()
    if verified { downloads?.resume(automatic: true) }
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
    let stamp = account.generation
    // One store query for every Watch song (each song's own query before).
    let allowed = try await store.sourceRevisions(ids: downloads.records.map(\.id))
    guard account.isCurrent(stamp) else { return }
    await downloads.reconcile(allowed)
    guard account.isCurrent(stamp) else { return }
    verified = true
    await followPlaylists()
    guard account.isCurrent(stamp) else { return }
    // A reconcile that finishes after the phone was locked must not restart
    // preparation (#65); returning to the foreground does that.
    if foreground { downloads.resumePreparation() }
    await publish()
  }
  var supported: Bool { link?.supported == true }
  /// Returns what happened, for the screen where Send to Apple Watch was tapped.
  /// Its outcome as a status line: a refusal or failure is an error (Codex on #159).
  @discardableResult func enqueue(_ tracks: [Track], origin: DownloadOrigin = .direct) async
    -> StatusMessage?
  {
    guard supported else {
      message = "Watch downloads are managed by its paired iPhone."
      return message.map(StatusMessage.error)
    }
    guard verified, let downloads else {
      message = "Connect and refresh your phone library first."
      return message.map(StatusMessage.error)
    }
    if let id = origin.playlistId {
      subscribe(id, name: origin.playlistName)
      exclude([], from: id, sending: Set(tracks.map(\.id)))
      if tracks.isEmpty {
        await publish()
        return .info(
          "\(origin.playlistName ?? "This playlist") will follow on your Watch: songs you add are sent automatically."
        )
      }
    }
    guard
      downloads.records.count
        + Set(tracks.map(\.id)).subtracting(Set(downloads.records.map(\.id))).count
        <= 1000
    else {
      message = "Choose up to 1,000 songs for your Watch library."
      return message.map(StatusMessage.error)
    }
    let stamp = account.generation
    do {
      var personal: [Track] = []
      for id in Set(tracks.map(\.id)) {
        if let track = try await model?.store?.track(id: id) { personal.append(track) }
      }
      guard account.isCurrent(stamp) else { return nil }
      guard !personal.isEmpty else {
        message = "Add these songs to your library before downloading them to Watch."
        return message.map(StatusMessage.error)
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
      return .info(result)
    } catch {
      message = error.userMessage
      return message.map(StatusMessage.error)
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
    foreground = false
    account.cancel("poll")
    await downloads?.suspendPreparation()
  }
  func disconnect() {
    account.end()
    deferredPublish.cancel()
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
  /// Publishes once at a time; a request during a publish runs after it (#97).
  func publish(force: Bool = false) async {
    await publishing.run(force: force) { await self.publishOnce(force: $0) }
  }
  private func publishOnce(force: Bool) async {
    guard verified, let downloads, let namespace, let baseManifestURL = manifestURL else {
      if context.account == nil { try? link?.publish(context) }
      return
    }
    if force { sent.reset() }
    let stamp = account.generation
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
    guard account.isCurrent(stamp) else { return }
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
    let stale = WatchDelivery.staleManifests(
      (try? FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil))
        ?? [], current: manifestURL, revision: context.revision,
      retained: Set(outstanding.map(\.context.revision)))
    for file in stale { try? FileManager.default.removeItem(at: file) }
    // Do not send audio until the Watch has applied this manifest and acknowledged its budget.
    guard let receipt, receipt.context == context, receipt.manifestApplied else { return }
    let audio = WatchDelivery.audio(
      songs: songs, receipt: receipt,
      inFlight: Set(outstanding.compactMap { $0.file.map { "\($0.songId):\($0.checksum)" } }),
      hasFile: { files[$0] != nil }, due: due)
    if audio.budgetFull {
      let full = "Watch storage budget is full. Remove Watch songs before adding more."
      if message != full { message = full }
    }
    for song in audio.send {
      guard let file = files[song.id] else { continue }
      let identity = WatchDelivery.identity(song)
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
    let stamp = account.generation
    // Cleared whenever its wait ends, even for an account that has gone, so
    // the next deferred publish is scheduled (#123).
    deferredPublish.schedule(
      after: .seconds(max(1, seconds)),
      isCurrent: { [weak self] in self?.account.isCurrent(stamp) ?? false }
    ) { [weak self] in
      await self?.publish()
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
    let stamp = account.generation
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
        guard account.isCurrent(stamp), verified, command.account == context.account else {
          return CompanionReply(message: "The phone account changed. Retry after syncing.")
        }
        await enqueue(tracks)
        return CompanionReply(
          message: "Queued on your phone. AAC files transfer when both devices are available.")
      case "remove":
        for id in command.songIds.prefix(100) {
          guard account.isCurrent(stamp), command.account == context.account else {
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
