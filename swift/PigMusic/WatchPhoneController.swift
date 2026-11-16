import BackgroundTasks
import PigAPI
import Foundation
import Observation
import PigBrand
import PigCompanion
import PigDomain
import PigDownloads
import SwiftUI
import UIKit

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
  /// The ready songs' files from the last publish, with the archive revision
  /// they were read at. The poll reuses them while the archive is unchanged
  /// instead of checking every file again (RV23); the archive hands them
  /// back with its records in one read, so they never mix revisions (#218).
  @ObservationIgnored private var readyCache: (archive: Int, files: [String: URL])?
  /// When each manifest or song was last handed to WatchConnectivity.
  @ObservationIgnored private var sent = CompanionSendLog()
  @ObservationIgnored private var lastAdvance = Date.distantPast
  /// The context last handed to WatchConnectivity, which keeps delivering it.
  @ObservationIgnored private var contextSent: CompanionContext?
  @ObservationIgnored private let deferredPublish = DeferredRun()
  /// The Watch's last confirmation, kept through new library versions of the
  /// same account: audio no longer waits for the current version's (#219).
  @ObservationIgnored private var lastReceipt: CompanionReceipt?
  /// Songs ("id:checksum") of the last manifest the Watch confirmed applying:
  /// it files their audio on arrival. Any other song waits there for its
  /// manifest, so fewer of those are sent at a time.
  @ObservationIgnored private var committed: Set<String> = []
  private func forgetConfirmations() {
    lastReceipt = nil
    committed = []
  }
  /// When each song passed each step on its way to the Watch (#219).
  @ObservationIgnored private(set) var deliveryLog = WatchDeliveryLog()
  @ObservationIgnored private var deliveryLogURL: URL?
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
        // A reinstalled Watch lost the phone's song: send it again (Codex on #220).
        resendNowPlaying()
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
            forgetConfirmations()
            advance(account: namespace)
            await publish(force: true)
          } else if let manifest = published,
            let confirmed = try? acknowledgement.receipt(for: manifest, lastApplied: lastReceipt),
            confirmed.context == context
          {
            if receipt?.ready.count != confirmed.ready.count || receipt?.context != confirmed.context {
              DiagnosticsLog.shared.record(
                "watch",
                "Watch confirmed revision \(confirmed.context.revision): \(confirmed.ready.count) songs ready, manifest applied=\(confirmed.manifestApplied)"
              )
            }
            receipt = confirmed
            // Until the Watch applies this manifest, its positions count the
            // songs of the one before: only an applied receipt says which
            // songs are there, and an unapplied one shows its songs (#221).
            if confirmed.manifestApplied {
              lastReceipt = confirmed
              committed = Set(manifest.songs.map(WatchDelivery.identity))
              noteDelivery { log in
                for id in confirmed.ready.keys {
                  log.arrived(id, at: confirmed.arrivals?[id] ?? Date())
                }
              }
            }
            confirmedAt = Date()
            saveReceipt()
            downloads?.budgetBytes = confirmed.budgetBytes
            await handOver(manifest, confirmed)
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
    forgetConfirmations()
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
    // Restored for a background refresh (#223): the new controller must not
    // start preparing once its session is ready (D-51).
    if !foreground { await downloads?.suspendPreparation() }
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
    deliveryLogURL = directory.appendingPathComponent("watch-delivery.json")
    deliveryLog =
      deliveryLogURL.flatMap { try? Data(contentsOf: $0) }
      .flatMap { try? JSONDecoder().decode(WatchDeliveryLog.self, from: $0) } ?? WatchDeliveryLog()
    noteDelivery { $0.phone(foreground: foreground) }
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
        if saved.receipt.manifestApplied {
          lastReceipt = saved.receipt
          committed = Set(old.songs.map(WatchDelivery.identity))
        }
        confirmedAt = saved.at
        downloads?.budgetBytes = saved.receipt.budgetBytes
      }
    }
    startPolling()
  }
  /// Foreground only, every 30 s, paired or not: downloads, receipts, the
  /// link's state changes and every library sync publish on their own (#178).
  private func startPolling() {
    account.cancel("poll")
    guard foreground, namespace != nil else { return }
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
        do { try await Task.sleep(for: WatchPublishLoop.heartbeat) } catch { return }
      }
    })
  }
  func resume() {
    foreground = true
    noteDelivery { $0.phone(foreground: true) }
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
      forgetConfirmations()
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
  /// Whether Send to Apple Watch went ahead (D-103: the button only shows a
  /// checkmark); a refusal or failure carries its reason (Codex on #159).
  enum SendOutcome: Equatable {
    case sent
    case failed(String)
    /// The account changed meanwhile: nothing to show.
    case superseded
  }
  @discardableResult func enqueue(_ tracks: [Track], origin: DownloadOrigin = .direct) async
    -> SendOutcome
  {
    guard supported else {
      let reason = "Watch downloads are managed by its paired iPhone."
      message = reason
      return .failed(reason)
    }
    guard verified, let downloads else {
      let reason = "Connect and refresh your phone library first."
      message = reason
      return .failed(reason)
    }
    if let id = origin.playlistId {
      subscribe(id, name: origin.playlistName)
      exclude([], from: id, sending: Set(tracks.map(\.id)))
      // An empty playlist is followed: songs added later are sent automatically.
      if tracks.isEmpty {
        await publish()
        return .sent
      }
    }
    guard
      downloads.records.count
        + Set(tracks.map(\.id)).subtracting(Set(downloads.records.map(\.id))).count
        <= 1000
    else {
      let reason = "Choose up to 1,000 songs for your Watch library."
      message = reason
      return .failed(reason)
    }
    let stamp = account.generation
    do {
      var personal: [Track] = []
      for id in Set(tracks.map(\.id)) {
        if let track = try await model?.store?.track(id: id) { personal.append(track) }
      }
      guard account.isCurrent(stamp) else { return .superseded }
      guard !personal.isEmpty else {
        let reason = "Add these songs to your library before downloading them to Watch."
        message = reason
        return .failed(reason)
      }
      // Songs not in your library are simply not sent (D-103).
      let known = Set(downloads.records.map(\.id))
      try await downloads.enqueue(personal, quality: "aac", origin: origin)
      noteDelivery { $0.sent(personal.map(\.id).filter { !known.contains($0) }) }
      await publish()
      return .sent
    } catch {
      message = error.userMessage
      return .failed(error.userMessage)
    }
  }
  /// Rises with every removal the person makes, so a playlist reconciliation
  /// already running doesn't undo it.
  @ObservationIgnored private var removals = 0
  func remove(_ id: String) async {
    removals += 1
    // Removed from the Watch: followed playlists must not send it back.
    for playlist in downloads?.records.first(where: { $0.id == id })?.effectiveOrigins
      .compactMap(\.playlistId) ?? []
    {
      exclude([id], from: playlist)
    }
    await downloads?.remove(id: id)
    await publish()
  }
  /// Removes everything sent to the Watch and stops following its playlists
  /// (#209): the next manifest leaves the Watch empty.
  func removeAll() async {
    removals += 1
    for id in Array(subscriptions.keys) { unsubscribe(id) }
    await downloads?.clear()
    await publish()
  }
  /// Removes an album's or playlist's claim; songs another one needs stay.
  func release(_ ids: [String], origin: DownloadOrigin) async {
    removals += 1
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
    // A removal made while the plan runs (Remove all, say) stops it, and its
    // subscriptions are not saved over what the removal left (Codex).
    let started = removals
    let finished = await plan.apply(
      isCurrent: { [weak self] in self?.removals == started },
      release: { ids, origin in await downloads.release(ids: ids, origin: origin) },
      lookup: { try? await store.track(id: $0) },
      enqueue: { [weak self] tracks, origin in
        try? await downloads.enqueue(tracks, quality: "aac", origin: origin)
        self?.noteDelivery { $0.sent(tracks.map(\.id)) }
      })
    guard finished else { return }
    if plan.subscriptions != subscriptions {
      subscriptions = plan.subscriptions
      saveSubscriptions()
    }
    if plan.exclusions != exclusions {
      exclusions = plan.exclusions
      saveExclusions()
    }
  }
  /// The app left the foreground: stop polling and the foreground worker, but
  /// leave the system's background downloads and Watch transfers running, and
  /// hand them every queued copy the server has ready (#219).
  func suspend() async {
    foreground = false
    noteDelivery { $0.phone(foreground: false) }
    account.cancel("poll")
    await downloads?.suspendPreparation()
    await handOff()
  }
  /// The background refresh that carries on with Watch copies the server was
  /// still preparing when the app left (#219). Registered in Info.plist.
  static let refreshIdentifier = "au.com.pig.music.watchprepare"
  @ObservationIgnored private var handOffTask = UIBackgroundTaskIdentifier.invalid
  @ObservationIgnored private var handOffExpired = false
  /// Hands queued Watch copies to the background session, within the time
  /// iOS gives an app that just left, or a background refresh. Copies the
  /// server is still preparing wait for a refresh the system schedules when
  /// it suits the battery; the foreground resumes them as before.
  private func handOff() async {
    guard verified, !foreground, let downloads, handOffTask == .invalid else { return }
    let stamp = account.generation
    handOffExpired = false
    handOffTask = UIApplication.shared.beginBackgroundTask(withName: "Send songs to Apple Watch") {
      [weak self] in
      MainActor.assumeIsolated { self?.endHandOff(expired: true) }
    }
    defer { endHandOff(expired: false) }
    guard
      let outcome = await downloads.handOffToBackground(isCancelled: { @MainActor [weak self] in
        guard let self else { return true }
        return foreground || handOffExpired || !account.isCurrent(stamp)
      })
    else { return }
    if !outcome.handed.isEmpty || !outcome.waiting.isEmpty {
      DiagnosticsLog.shared.record(
        "watch",
        "background hand-off: \(outcome.handed.count) copies to the background session, \(outcome.waiting.count) waiting"
          + (outcome.stopped ? " (stopped early)" : ""))
    }
    guard account.isCurrent(stamp) else { return }
    if !outcome.waiting.isEmpty, !foreground { scheduleRefresh() }
    // Copies already ready go on to the Watch now.
    await publish()
  }
  private func endHandOff(expired: Bool) {
    if expired { handOffExpired = true }
    guard handOffTask != .invalid else { return }
    UIApplication.shared.endBackgroundTask(handOffTask)
    handOffTask = .invalid
  }
  func scheduleRefresh() {
    let request = BGAppRefreshTaskRequest(identifier: Self.refreshIdentifier)
    request.earliestBeginDate = Date(timeIntervalSinceNow: 15 * 60)
    do { try BGTaskScheduler.shared.submit(request) } catch {
      DiagnosticsLog.shared.record(
        "watch", "background refresh not scheduled: \(error.localizedDescription)")
    }
  }
  /// A launch for a background refresh alone, with no scene (#223): nothing
  /// runs as the foreground until the scene's first activation resumes it.
  func leaveForeground() {
    foreground = false
    noteDelivery { $0.phone(foreground: false) }
    account.cancel("poll")
  }
  /// A background refresh: hand over what the server has prepared since, and
  /// schedule another while songs still wait. A launch for the refresh alone
  /// first checks the queued copies against the saved library, as the
  /// foreground's refresh would; nothing is fetched (#223).
  func continueInBackground() async {
    guard !foreground, let downloads else { return }
    let stamp = account.generation
    DiagnosticsLog.shared.record("watch", "background refresh")
    if !verified {
      do { try await reconcile() } catch {
        guard account.isCurrent(stamp), !foreground else { return }
        DiagnosticsLog.shared.record(
          "watch",
          "background refresh could not read the saved library: \(error.localizedDescription)")
        scheduleRefresh()
        return
      }
      guard account.isCurrent(stamp), !foreground, verified else { return }
    }
    // The hand-off runs only while the foreground worker is stopped.
    if downloads.preparationAllowed { await downloads.suspendPreparation() }
    guard account.isCurrent(stamp), !foreground else { return }
    await downloads.reload()
    await handOff()
  }
  func disconnect() {
    account.end()
    deliveryLog = WatchDeliveryLog()
    if let deliveryLogURL { try? FileManager.default.removeItem(at: deliveryLogURL) }
    deliveryLogURL = nil
    deferredPublish.cancel()
    if let downloads { Task { await downloads.clear() } }
    downloads = nil
    readyCache = nil
    verified = false
    namespace = nil
    receipt = nil
    forgetConfirmations()
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
    // Records and files from one reading of the archive (#218): a hand-over
    // settled between two separate reads dropped every song it delivered.
    let listing = await downloads.archive.listing(reusing: force ? nil : readyCache?.archive)
    guard account.isCurrent(stamp) else { return }
    let files: [String: URL]
    if let fresh = listing.files {
      files = fresh
      readyCache = (listing.revision, fresh)
    } else {
      files = readyCache?.files ?? [:]
    }
    let ready = listing.records.filter { $0.state == "ready" && $0.checksum != nil }
    noteDelivery { log in
      log.retain(Set(listing.records.map(\.id)))
      for record in ready { log.ready(record.id) }
    }
    // A song the Watch holds stays listed without the iPhone's copy (#218);
    // with no file, its audio is never sent again.
    var songs: [CompanionSong] = []
    for record in ready {
      if let checksum = record.checksum, files[record.id] != nil || record.delivered {
        songs.append(CompanionSong(track: record.track, checksum: checksum, bytes: record.total))
      }
    }
    songs.sort { $0.id < $1.id }
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
    // Songs ready meanwhile don't wait for it: their audio goes ahead (#219).
    var ahead: [CompanionSong] = []
    if batchActive, growthOnly, !force, Date().timeIntervalSince(lastAdvance) < 45 {
      deferPublish(after: 45 - Date().timeIntervalSince(lastAdvance))
      let listed = Set((published?.songs ?? []).map(WatchDelivery.identity))
      ahead = songs.filter { !listed.contains(WatchDelivery.identity($0)) }
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
        forgetConfirmations()
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
    // Audio goes without waiting for the Watch to confirm this manifest
    // (#219): it files what it can at once and keeps the rest until the
    // manifest naming it arrives. The budget is the one it last reported; the
    // Watch enforces its own on arrival.
    var known =
      lastReceipt
      ?? CompanionReceipt(
        context: context, ready: [:], usedBytes: 0, budgetBytes: Self.watchDefaultBudget,
        manifestApplied: false)
    known.budgetBytes = downloads.budgetBytes
    let audio = WatchDelivery.audio(
      songs: songs, ahead: ahead, receipt: known, committed: committed,
      inFlight: Set(outstanding.compactMap { $0.file.map { "\($0.songId):\($0.checksum)" } }),
      hasFile: { files[$0] != nil }, due: due)
    if audio.budgetFull {
      let full = "Watch storage budget is full. Remove Watch songs before adding more."
      if message != full { message = full }
    }
    // A song ahead of the published manifest names the next library version,
    // which lists every ready song: the Watch holds it until that arrives.
    let nextContext = CompanionContext(
      revision: max(context.revision, peerRevision) + 1, account: namespace)
    for (song, isAhead) in audio.send.map({ ($0, false) }) + audio.ahead.map({ ($0, true) }) {
      guard let file = files[song.id] else { continue }
      let identity = WatchDelivery.identity(song)
      let ticket = CompanionFile(
        context: isAhead ? nextContext : context, songId: song.id, checksum: song.checksum)
      let envelope = CompanionEnvelope(kind: "audio", context: ticket.context, file: ticket)
      if let data = try? JSONEncoder().encode(envelope) {
        link?.transfer(file, metadata: data)
        sent.mark("audio:" + identity)
        noteDelivery { $0.handed(song.id) }
      }
    }
  }
  /// The iPhone's copy of a song goes once the Watch confirms holding it, and
  /// a song the Watch later loses is fetched from the server again (#218).
  /// Only a confirmation of the applied library version counts.
  private func handOver(_ manifest: CompanionManifest, _ confirmed: CompanionReceipt) async {
    guard confirmed.manifestApplied, confirmed.context == context, let downloads else { return }
    let stamp = account.generation
    // Audio the Watch holds needs no transfer still on its way, and its file
    // is about to go.
    let held = Set(confirmed.ready.map { "\($0.key):\($0.value)" })
    link?.cancelTransfers { data in
      guard let envelope = try? JSONDecoder().decode(CompanionEnvelope.self, from: data),
        envelope.kind == "audio", let file = envelope.file
      else { return false }
      return held.contains("\(file.songId):\(file.checksum)")
    }
    let listed = Dictionary(
      manifest.songs.map { ($0.id, $0.checksum) }, uniquingKeysWith: { first, _ in first })
    let plan = await downloads.settleHandOver(
      listed: listed, ready: confirmed.ready, applied: confirmed.manifestApplied)
    guard account.isCurrent(stamp), !plan.isEmpty else { return }
    readyCache = nil
    DiagnosticsLog.shared.record(
      "watch",
      "Watch holds \(plan.deliver.count) more songs; their iPhone copies were deleted"
        + (plan.refetch.isEmpty ? "" : "; \(plan.refetch.count) lost songs fetched again"))
    if !plan.refetch.isEmpty {
      // A lost song's delivery starts again.
      noteDelivery { log in
        log.retain(Set(downloads.records.map(\.id)).subtracting(plan.refetch.keys))
        log.sent(plan.refetch.keys)
      }
    }
  }
  /// Changes the delivery log, saving it when something changed.
  private func noteDelivery(_ change: (inout WatchDeliveryLog) -> Void) {
    var next = deliveryLog
    change(&next)
    guard next != deliveryLog else { return }
    deliveryLog = next
    guard let deliveryLogURL, let data = try? JSONEncoder().encode(next) else { return }
    try? data.write(to: deliveryLogURL, options: .atomic)
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
  /// What was last queued for the Watch's home, and the send waiting out
  /// its debounce (D-104).
  @ObservationIgnored private var nowPlayingSent: String?
  @ObservationIgnored private var nowPlayingPending: String?
  @ObservationIgnored private var nowPlayingSend: Task<Void, Never>?
  /// The phone's latest state, sent again when the Watch reconnects or is
  /// reinstalled, which loses what it had.
  @ObservationIgnored private var nowPlayingLatest: (track: Track?, playing: Bool, cover: UIImage?)?
  private func resendNowPlaying() {
    guard let latest = nowPlayingLatest else { return }
    nowPlayingSent = nil
    nowPlayingSend?.cancel()
    nowPlayingPending = nil
    sendNowPlaying(latest.track, playing: latest.playing, cover: latest.cover)
  }
  /// Tells the Watch what plays here, for its home (D-104): only when the
  /// song, play/pause or the cover's presence changes, a second after the
  /// last change, so progress ticks and start-up flicker send nothing.
  func sendNowPlaying(_ track: Track?, playing: Bool, cover: UIImage?) {
    nowPlayingLatest = (track, playing, cover)
    let key = track.map { "\($0.id)|\(playing)|\(cover != nil)" } ?? "stopped"
    guard link?.available == true else { return }
    // Already waiting to go: the debounce is not restarted by every tick.
    guard key != nowPlayingPending else { return }
    nowPlayingSend?.cancel()
    nowPlayingPending = nil
    guard key != nowPlayingSent else { return }
    nowPlayingPending = key
    nowPlayingSend = Task { [weak self] in
      try? await Task.sleep(for: .seconds(1))
      guard !Task.isCancelled, let self else { return }
      nowPlayingPending = nil
      let state =
        track.map {
          PhoneNowPlaying(
            title: $0.title, artist: $0.artist, playing: playing,
            cover: cover.flatMap(Self.watchCover))
        } ?? .stopped()
      guard let data = try? JSONEncoder().encode(state),
        link?.sendLatest(data, replacing: { PhoneNowPlaying.decode($0) != nil }) == true
      else { return }
      nowPlayingSent = key
    }
  }
  /// A small square JPEG of the cover, or nil when it can't be kept small.
  private static func watchCover(_ image: UIImage) -> Data? {
    let side = CGFloat(PhoneNowPlaying.coverPixels)
    let format = UIGraphicsImageRendererFormat()
    format.scale = 1
    let scale = max(side / max(image.size.width, 1), side / max(image.size.height, 1))
    let size = CGSize(width: image.size.width * scale, height: image.size.height * scale)
    let square = UIGraphicsImageRenderer(size: CGSize(width: side, height: side), format: format)
      .image { _ in
        image.draw(
          in: CGRect(
            x: (side - size.width) / 2, y: (side - size.height) / 2, width: size.width,
            height: size.height))
      }
    for quality in [0.7, 0.4] {
      if let data = square.jpegData(compressionQuality: quality),
        data.count <= PhoneNowPlaying.maximumCoverBytes
      {
        return data
      }
    }
    return nil
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
          return CompanionReply(message: "The iPhone account changed. Try again in a moment.")
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
      case "sync": await publish(force: true)
      default: return CompanionReply(message: "Unsupported Watch command.")
      }
      return CompanionReply()
    } catch { return CompanionReply(message: error.localizedDescription) }
  }
}
