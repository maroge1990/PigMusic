import Foundation
import Network
import Observation
import PigAPI
import PigDomain
import PigDownloads
import PigPlayback
import PigStore
import PigSync
import UIKit

enum PlayOrigin: Hashable {
  case library
  /// The catalogue artist when the page knew it.
  case catalogue(artistId: String?)
}

@MainActor @Observable final class AppModel {
  struct Connection: Codable {
    var server: String
    var person: Person
  }
  var watch: WatchPhoneController?
  /// Removals, hides and cancels waiting for their Undo (D-79, #183).
  let undo = UndoQueue()
  /// Where the one Undo bar sits: above a sheet or the mini player (#210).
  let undoPlacement = UndoPlacement()
  /// Undos VoiceOver has heard while they wait, so a bar moving from a
  /// sheet to the tabs doesn't announce one twice (#210).
  @ObservationIgnored private var announcedUndo: Set<String> = []
  func announceUndo(_ item: UndoQueue.Item) {
    announcedUndo.formIntersection(undo.items.map(\.id))
    guard announcedUndo.insert(item.id).inserted else { return }
    UIAccessibility.post(notification: .announcement, argument: item.label + ". Undo is available.")
  }
  /// A failed action's notice, shown in the Undo bar's place on every page
  /// and sheet (#245).
  let notices = ActionNotices()
  /// Says what went wrong with an action, wherever the person is (#245): a
  /// song menu's failure used to show only on the Library tab, Account, Now
  /// Playing and download settings. VoiceOver hears it at once.
  func notify(_ text: String, reference: String? = nil) {
    notices.post(text, reference: reference)
    UIAccessibility.post(notification: .announcement, argument: text)
  }
  /// `notify` with what `error` has to say, and the request a server
  /// error's log knows it by (#239).
  func notify(_ error: any Error) {
    if case .serverError(let id) = error as? NativeFailure {
      notify(error.userMessage, reference: id)
    } else {
      notify(error.userMessage)
    }
  }
  /// Rises when something the person asked for is done (a favourite, a song
  /// queued): the phone answers with a success tap (#183).
  var confirmations = 0
  /// Mix songs hidden this launch, so a hidden song stays hidden on a mix
  /// page opened again before Home refreshes.
  var hiddenMixSongs: Set<String> = []
  var downloads: DownloadController?
  var preferences = AccountPreferences()
  var person: Person?
  var server = ""
  var busy = false
  var syncing = false
  var message: String?
  var songSort: LibrarySort = .title
  var albumSort: LibrarySort = .title
  var artistSort: LibrarySort = .title
  var songs: [Track] = []
  var albums: [Album] = []
  var artists: [Artist] = []
  var playlists: [Playlist] = []
  var homeContent: [HomeSection: HomeContent] = [:]
  var homeErrors: [HomeSection: String] = [:]
  var homeLoading: Set<HomeSection> = []
  /// The ranked search (D-61); this phone's library when the server can't
  /// answer, and the only one in the demo (#177).
  var foundSearch: FoundSearch?
  /// Favourite state by song, shared by the player's star and Song actions.
  var favourites = FavouriteStates()
  /// What this person opened or played from search, newest first (D-61).
  private(set) var recentSearches = RecentSearches()
  @ObservationIgnored private var recentSearchesLoaded = false
  /// Music requests the server is still getting, from the requests page summary.
  var serverActiveRequests = 0
  var pendingEdits: [PendingMutation] = []
  @ObservationIgnored private var flushingEdits = false
  var mutationActive = false
  var canMutate: Bool { service != nil && !busy && !syncing && !mutationActive }
  /// Requests wait in their own outbox, so a sync or another change in
  /// progress doesn't stop one (#206); only a signed-in library is needed.
  var canRequest: Bool { store != nil && person != nil }
  var songCount = 0
  var albumCount = 0
  var artistCount = 0
  /// Album category shown in Library ("album", "musical", "soundtrack", "compilation").
  var albumCategory = "album"
  @ObservationIgnored private var reloadGate = LibraryReloadGate()
  var albumCategoryCounts: [String: Int] = [:]
  var loadingMore = false
  @ObservationIgnored private var flushingHistory = false
  let player = PlayerController()
  var artworkNamespace = ""
  var artworkRefresh = 0
  /// Decoded covers in memory, in front of the account's artwork cache.
  @ObservationIgnored private let images = ArtworkLoader<UIImage> { UIImage(cgImage: $0) }
  @ObservationIgnored private var artistCovers: [String: String?] = [:]
  /// Each cover's colour (#199), computed once per cover and account.
  @ObservationIgnored private var coverSwatches: [String: CoverRGB?] = [:]
  @ObservationIgnored private var catalogueMemory = CatalogueMemory()
  @ObservationIgnored private(set) var service: NativeService?
  @ObservationIgnored private(set) var store: LibraryStore?
  @ObservationIgnored private var sync: SyncCoordinator?
  /// The signed-in account's generation and the tasks that end with it.
  @ObservationIgnored private let account = AccountFence()
  /// One refresh at a time; one asked for meanwhile runs after it (RV23).
  @ObservationIgnored private let refreshLoop = RefreshLoop()
  /// Offline, every refresh fails the same way: one line and a count (#149).
  @ObservationIgnored private var refreshFailures = FailureRun()
  /// A refresh failure still repeating, for a diagnostics export taken now.
  var refreshFailurePending: String? { refreshFailures.pending }
  @ObservationIgnored private var restoring = false
  /// True once launch has shown what this phone already holds: the saved
  /// library of a restored account, or the sign-in form. The splash ends here,
  /// not after the first refresh and not after a fixed time (brand 1.8 §8).
  private(set) var launched = false
  /// The last library refresh could not reach PigMusic. The saved library
  /// carries on; an empty one says so instead of looking empty (#180).
  private(set) var syncFailed = false
  /// Asks the tab bar to open Search: an empty library's one action.
  private(set) var searchRequests = 0
  func openSearch() { searchRequests += 1 }
  /// Whether the scene is active and the foreground work runs (#97).
  @ObservationIgnored private var foreground = ForegroundGate()
  @ObservationIgnored private let defaults: UserDefaults
  @ObservationIgnored private let network = NetworkConditions()
  init(defaults: UserDefaults = .standard) {
    self.defaults = defaults
    watch = WatchPhoneController(model: self, defaults: defaults)
    // The Watch's home shows what plays here (D-104).
    player.onNowPlaying = { [weak self] track, playing, cover in
      self?.watch?.sendNowPlaying(track, playing: playing, cover: cover)
    }
  }

  func restore() async {
    // An account a background refresh restored still waits for its refresh.
    guard !restoring, !busy, person == nil || refreshAfterRestore else { return }
    restoring = true
    busy = true
    defer {
      restoring = false
      busy = false
      launched = true
    }
    #if DEBUG
      if ProcessInfo.processInfo.arguments.contains("--demo") {
        await loadDemo()
        return
      }
    #endif
    if person == nil {
      Task { await Self.parkedSessions.end() }
      switch await restoreAccount() {
      case .attached: break
      case .nothingSaved: return
      case .noToken:
        defaults.removeObject(forKey: "pig.connection")
        return
      case .failed(let error, _):
        message = error.userMessage
        return
      }
    }
    refreshAfterRestore = false
    // The saved library is on screen; the refresh fills it in behind.
    launched = true
    await refresh()
    startLiveUpdates()
  }
  private enum RestoredAccount: Sendable {
    /// Attached under this account generation.
    case attached(generation: Int)
    case nothingSaved
    /// The saved account's token is gone.
    case noToken(generation: Int)
    /// `generation` is the one the restoration itself left.
    case failed(any Error, generation: Int)
  }
  /// The launch's one restoration of the saved account, shared by the scene
  /// and a background refresh, whichever asks first (#223). Only what the
  /// phone saved: the library refresh and live updates are the scene's.
  @ObservationIgnored private let restoration = SingleFlight<RestoredAccount>()
  /// An account restored without the scene still waits for its first refresh.
  @ObservationIgnored private var refreshAfterRestore = false
  private func restoreAccount() async -> RestoredAccount {
    await restoration.run { [weak self] in
      await self?.attachSavedAccount() ?? .nothingSaved
    }
  }
  private func attachSavedAccount() async -> RestoredAccount {
    guard let data = defaults.data(forKey: "pig.connection"),
      let connection = try? JSONDecoder().decode(Connection.self, from: data)
    else { return .nothingSaved }
    var stamp = account.generation
    server = connection.server
    do {
      let vault = TokenVault(account: connection.server)
      guard try await vault.read() != nil else { return .noToken(generation: stamp) }
      try account.check(stamp)
      let service = try NativeService(
        server: connection.server, vault: vault, parked: Self.parkedSessions)
      // attach() starts the account's generation before its first await.
      stamp += 1
      try await attach(connection.person, service: service)
      try account.check(stamp)
      refreshAfterRestore = true
      return .attached(generation: stamp)
    } catch { return .failed(error, generation: stamp) }
  }
  /// A background refresh for Watch copies (#223). iOS may launch the app for
  /// it with no scene: the saved account is restored first, without a library
  /// refresh or live updates, and the Watch's queued copies are checked
  /// against the saved library and handed on. A restoration that cannot
  /// finish now never signs out; another refresh is asked for instead.
  func continueWatchInBackground() async {
    #if DEBUG
      if ProcessInfo.processInfo.arguments.contains("--demo") { return }
    #endif
    guard let watch else { return }
    // A launch for the refresh alone has never been active: nothing may run
    // as the foreground, which resumes it all at the first activation.
    if foreground.suspended { watch.leaveForeground() }
    watch.refreshRan()
    let attached = person != nil && !restoration.isRunning ? account.generation : nil
    var reason = ""
    let result = await BackgroundRefresh.run(
      attached: attached, fence: account,
      inForeground: { !foreground.suspended },
      // D-113: once the Watch has received nothing for two hours, the
      // refresh restores nothing, hands nothing on and asks for no other.
      mayStart: { watch.mayStartInBackground() },
      restore: {
        // A sign-in under way is not a saved account to restore.
        guard restoration.isRunning || !busy else {
          reason = "a sign-in is under way"
          return .unavailable(generation: account.generation)
        }
        switch await restoreAccount() {
        case .attached(let generation): return .attached(generation: generation)
        case .nothingSaved: return .signedOut
        case .noToken(let generation):
          reason = "no device token"
          return .unavailable(generation: generation)
        case .failed(let error, let generation):
          reason = error.localizedDescription
          return .unavailable(generation: generation)
        }
      },
      handOff: { await watch.continueInBackground() },
      reschedule: { watch.scheduleRefresh() })
    if result == .rescheduled {
      DiagnosticsLog.shared.record(
        "watch", "background refresh: account not restored (\(reason)), another refresh asked for")
    }
  }
  func sceneActive(_ active: Bool, background: Bool = false) async {
    let change = foreground.change(active: active, background: background)
    player.sceneBackground(background)
    switch change {
    case .stay: return
    case .suspend:
      // Waiting removals are sent now, as the web sends them when its page
      // closes (D-79), with time from iOS to finish.
      await sendWaitingActions()
      // A moment of Control Centre or a notification keeps everything
      // running; only the background stops it (#97).
      account.cancel("live")
      await downloads?.suspendPreparation()
      await watch?.suspend()
    case .back(let away, let renewConnections, let restart):
      // Connections kept through a break may be dead without the system
      // knowing; the first request on one waited for its timeout.
      if renewConnections {
        service?.renewConnections()
        player.renewConnections()
        DiagnosticsLog.shared.record(
          "network", "new connections after \(Int(away / .seconds(1))) s away")
      }
      guard restart else { return }
      downloads?.resumePreparation()
      watch?.resume()
      await refresh()
      startLiveUpdates()
    }
  }
  /// The last library revision the live updates announced, this account.
  @ObservationIgnored private var libraryEvent: String?
  private func startLiveUpdates() {
    guard foreground.active, account.task("live") == nil, let service else { return }
    let stamp = account.generation
    account.own("live", Task { [weak self] in
      var delay = 1
      while !Task.isCancelled {
        guard let self, account.isCurrent(stamp) else { return }
        do {
          let events = try await service.events()
          for try await event in events {
            guard account.isCurrent(stamp), !Task.isCancelled else { return }
            delay = 1
            if event.name == "revoked" {
              revoked(generation: stamp)
              return
            }
            // Every connection opens with the library's current revision. On
            // a reconnect after the foreground refresh it is usually the one
            // already seen, and refreshing again found nothing (25 Oct log).
            if event.name == "revision", event.data == nil || event.data != libraryEvent {
              libraryEvent = event.data
              await refresh()
            }
            // A request moved on the server: open request lists reload, and
            // search's cached server states are dropped (#211). A finished
            // request also changes the library revision, which asks again.
            if event.name == "queue" {
              requestsRevision += 1
              await foundSearch?.serverChanged(reask: false)
              await refreshServerRequests()
            }
          }
        } catch NativeFailure.authentication {
          revoked(generation: stamp)
          return
        } catch is CancellationError { return } catch {
          // Cached browsing and manual refresh remain available.
        }
        guard account.isCurrent(stamp), !Task.isCancelled else { return }
        do { try await Task.sleep(for: .seconds(delay)) } catch { return }
        delay = min(30, delay * 2)
        await refresh()
      }
    })
  }
  func saveDownloadBudget(_ bytes: Int64) {
    defaults.set(Int(bytes), forKey: "pig.download-budget:" + artworkNamespace)
  }
  func download(
    _ tracks: [Track], quality: String = "original", origin: DownloadOrigin = .direct
  ) async {
    guard let downloads else { return }
    do {
      try await downloads.enqueue(tracks, quality: quality, origin: origin)
      let stamp = account.generation
      let covers = Set(tracks.compactMap(\.coverSha))
      for sha in covers {
        guard account.isCurrent(stamp), !Task.isCancelled else { return }
        _ = await artwork(sha: sha, width: 512)
        _ = await artwork(sha: sha, width: 256)
      }
    } catch { notify(error) }
  }
  func cachePreferences() async { try? await store?.cache(preferences, key: "preferences") }
  /// Local preference choices, so the latest wins over slower saves and syncs.
  @ObservationIgnored private var preferenceChanges = LatestChange()
  /// Applies a choice at once, keeps it on this phone and queues it for the
  /// server. Returns whether the server confirmed it, or nil once a newer
  /// choice has replaced it.
  func choosePreferences(_ value: AccountPreferences) async -> Bool? {
    let change = preferenceChanges.begin()
    preferences = value
    await cachePreferences()
    // Edits are queued and sent in order, so the server ends on the latest.
    let confirmed = (try? await saveEdit(OfflineMutation(action: "preferences", preferences: value)))
    guard preferenceChanges.isLatest(change) else { return nil }
    if preferences != value {
      // A sync replaced it while this save was sending: the choice stands.
      preferences = value
      await cachePreferences()
    }
    return confirmed ?? false
  }
  /// The server's preferences, unless a local choice is still waiting to send
  /// or was made while they were being read.
  private func adoptServerPreferences(_ service: NativeService, generation stamp: Int) async {
    let change = preferenceChanges.current
    guard !pendingEdits.contains(where: { $0.mutation.action == "preferences" }),
      let settings = try? await service.preferences(), account.isCurrent(stamp),
      preferenceChanges.isLatest(change),
      !pendingEdits.contains(where: { $0.mutation.action == "preferences" })
    else { return }
    preferences = settings
    await cachePreferences()
  }
  func join(invitation: String, password: String) async {
    guard !busy else { return }
    busy = true
    message = nil
    defer { busy = false }
    do {
      let invite: Invite
      if invitation.trimmingCharacters(in: .whitespacesAndNewlines).hasPrefix("https://") {
        invite = try Invite.parse(invitation)
      } else {
        let code = invitation.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard Invite.codeLengths.contains(code.count),
          code.allSatisfy({ Invite.codeAlphabet.contains($0) })
        else {
          throw NativeFailure.unavailable("Enter a valid invitation code or full invitation link.")
        }
        invite = Invite(server: try ServerAddress.validate(server), code: code)
      }
      let saved = try await Onboarding().join(
        invite, password: password, deviceName: UIDevice.current.name)
      server = saved.origin.absoluteString
      let service = try NativeService(
        server: server, vault: TokenVault(account: server), parked: Self.parkedSessions)
      let user = try await service.me()
      try await attach(user, service: service)
      defaults.set(
        try JSONEncoder().encode(Connection(server: server, person: user)), forKey: "pig.connection"
      )
      await refresh()
      startLiveUpdates()
    } catch OnboardingError.accountCreatedSignInFailed(let username) {
      message = "Account created for \(username). Sign in to finish connecting."
    } catch { message = error.userMessage }
  }
  func signIn(username: String, password: String) async {
    guard !busy else { return }
    busy = true
    message = nil
    defer { busy = false }
    do {
      let origin = try ServerAddress.validate(server)
      let service = try NativeService(
        server: origin.absoluteString, vault: TokenVault(account: origin.absoluteString),
        parked: Self.parkedSessions)
      let user = try await service.signIn(
        username: username, password: password, deviceName: UIDevice.current.name)
      server = origin.absoluteString
      try await attach(user, service: service)
      Task { await Self.parkedSessions.end() }
      defaults.set(
        try JSONEncoder().encode(Connection(server: server, person: user)), forKey: "pig.connection"
      )
      await refresh()
      startLiveUpdates()
    } catch { message = error.userMessage }
  }
  private func attach(_ user: Person, service: NativeService) async throws {
    account.advance()
    refreshLoop.reset()
    libraryEvent = nil
    let stamp = account.generation
    let base = try FileManager.default.url(
      for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
    let origin = await service.server
    try account.check(stamp)
    let directory = base.appendingPathComponent("PigMusic").appendingPathComponent(
      LibraryNamespace.key(server: origin, personId: user.id))
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    var excluded = directory
    var values = URLResourceValues()
    values.isExcludedFromBackup = true
    try excluded.setResourceValues(values)
    let database = try LibraryStore(path: directory.appendingPathComponent("library.sqlite").path)
    let cacheBase = try FileManager.default.url(
      for: .cachesDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
    try await player.configureSources(
      directory: cacheBase.appendingPathComponent("PigMusic").appendingPathComponent(
        LibraryNamespace.key(server: origin, personId: user.id)
      ).appendingPathComponent("audio"))
    try account.check(stamp)
    images.cache = try? ArtworkCache(directory: directory.appendingPathComponent("artwork"))
    artworkNamespace = LibraryNamespace.key(server: origin, personId: user.id)
    images.clearMemory()
    server = origin.absoluteString
    self.service = service
    store = database
    requestedSongs = RequestMarks(waiting: Set(await RequestOutbox.pending(database).map(\.id)))
    try account.check(stamp)
    let backgroundIdentifier = "au.com.pig.music.downloads." + artworkNamespace
    let archive =
      try BackgroundTransferHub.archive(for: backgroundIdentifier)
      ?? DownloadArchive(directory: directory.appendingPathComponent("Downloads"))
    downloads = DownloadController(archive: archive, source: service) { [weak self] error in
      if error as? NativeFailure == .authentication {
        self?.revoked(generation: stamp)
      } else if case OfflineFailure.authentication = error {
        self?.revoked(generation: stamp)
      } else {
        self?.message = error.userMessage
      }
    }
    downloads?.budgetBytes = Int64(
      defaults.object(forKey: "pig.download-budget:" + artworkNamespace) as? Int
        ?? (5 * 1024 * 1024 * 1024))
    try downloads?.enableBackground(
      identifier: backgroundIdentifier,
      directory: directory.appendingPathComponent("BackgroundTransfers"))
    // Restored for a background refresh (#223): downloads are prepared only
    // in the foreground (D-51), which resumes them at its first activation.
    if foreground.suspended { await downloads?.suspendPreparation() }
    await downloads?.reload()
    try await watch?.configure(directory: directory, namespace: artworkNamespace)
    var restoredQueue = try? await database.cached("playback", as: PlaybackCheckpoint.self)
    if let old = restoredQueue,
      let position = try? await database.cached("playback-position", as: PlaybackPosition.self)
    {
      var queue = old.queue
      queue.select(index: position.index)
      restoredQueue = PlaybackCheckpoint(queue: queue, elapsed: position.elapsed)
    }
    var savedOccurrenceIds = restoredQueue?.queue.items.map(\.id) ?? []
    let listeningGeneration = account.generation
    player.onListen = { [weak self] event in
      Task {
        guard let self, self.account.isCurrent(listeningGeneration), let service = self.service else {
          return
        }
        let identity = await service.qualifiedHistoryTrackID(event.trackId) ?? event.trackId
        let exists: Bool
        if identity.hasPrefix("mix:") {
          exists = true
        } else {
          exists = (try? await database.track(id: identity)) != nil
        }
        guard exists, self.account.isCurrent(listeningGeneration) else { return }
        try? await database.recordListen(
          ListeningEvent(
            eventId: event.eventId, trackId: identity, playedAt: event.playedAt,
            seconds: event.seconds))
        await self.flushHistory()
      }
    }
    player.onFirstSound = { [weak self] report in
      guard let self, self.account.isCurrent(listeningGeneration), let service = self.service
      else { return }
      Task { try? await service.reportFirstSound(report) }
    }
    player.onUpcoming = { [weak self] tracks in
      guard let self, let service = self.service else { return }
      let archive = self.downloads?.archive
      Task {
        var remote: [Track] = []
        for track in tracks where await archive?.localFile(track) == nil { remote.append(track) }
        await service.prepareRemote(remote)
      }
      let queue = self.player.queue
      if let current = queue.current, self.radioIDs.contains(current.track.id),
        queue.items.count - queue.index - 1 <= 5
      {
        self.refillRadio()
      } else if queue.current != nil, queue.index == queue.items.count - 1 {
        // The last song started: similar songs join Up next ahead of time,
        // so the queue plays on without a gap (#202).
        self.continueQueue()
      }
    }
    player.onSkip = { [weak self] track, heard in
      guard let self, self.radioIDs.contains(track.id) || self.similarIDs.contains(track.id),
        heard < 30, let service = self.service
      else { return }
      Task { try? await service.radioFeedback(trackId: track.id, action: "skip") }
    }
    player.onQueueEnd = { [weak self] in
      guard let self, let current = self.player.queue.current else { return }
      if self.radioIDs.contains(current.track.id) {
        self.refillRadio(continuing: true)
      } else {
        self.continueQueue(continuing: true)
      }
    }
    // In order, so an older position never overwrites a newer one (#97).
    let checkpoints = OrderedWrites()
    player.onCheckpoint = { checkpoint in
      let ids = checkpoint.queue.items.map(\.id)
      let changed = ids != savedOccurrenceIds
      savedOccurrenceIds = ids
      checkpoints.add {
        if changed { try? await database.cache(checkpoint, key: "playback") }
        try? await database.cache(
          PlaybackPosition(index: checkpoint.queue.index, elapsed: checkpoint.elapsed),
          key: "playback-position")
      }
    }
    if let restoredQueue, restoredQueue.queue.current != nil {
      play(
        restoredQueue.queue.items.map(\.track), at: restoredQueue.queue.index,
        restored: restoredQueue)
    }
    foundSearch = FoundSearch(
      source: service, library: database, applePlaylists: AppleMusicCatalogue.playlistSearch
    ) { [weak self] in
      self?.revoked(generation: stamp)
    }
    sync = SyncCoordinator(source: service, store: database)
    person = user
    preferences =
      (try? await database.cached("preferences", as: AccountPreferences.self))
      ?? AccountPreferences()
    for section in HomeSection.allCases {
      homeContent[section] = try? await database.cached(
        "home:" + section.rawValue, as: HomeContent.self)
    }
    try await loadCache(artworkChanged: true)
    pendingEdits = (try? await database.pendingMutations()) ?? []
  }
  /// A refresh asked for while one runs (a live revision event during a
  /// pull-to-refresh, say) is not dropped: the running one goes round again,
  /// because the delta it already read cannot contain that change.
  func refresh(afterMutation: Bool = false) async {
    guard !mutationActive || afterMutation, service != nil, sync != nil else { return }
    await refreshLoop.run { await refreshOnce() }
  }
  private func refreshOnce() async {
    guard let service, let sync else { return }
    let stamp = account.generation
    // Foreground time only: a refresh the phone slept through isn't 10 h (#149).
    let began = DiagnosticsLog.shared.activeTime
    syncing = true
    defer { if account.isCurrent(stamp) { syncing = false } }
    do {
      let current = try await service.me()
      guard account.isCurrent(stamp) else { return }
      guard current.id == person?.id else {
        detach()
        message = "The server account changed. Please sign in again."
        return
      }
      watch?.confirmAccount()
      let before = try? await store?.revision()
      let artworkBefore = try? await store?.artworkRevision()
      try await sync.refresh()
      guard account.isCurrent(stamp) else { return }
      // No saved revision (first sign-in, or an update that asked for one
      // full resync, #168): the whole library was read again.
      if before == nil {
        DiagnosticsLog.shared.record(
          "sync", "full library resync: there was no saved revision (first sync or app update)")
      }
      // Reloading redraws every open page and list, so it waits for a real
      // change. Reconnecting the live updates syncs often with none. Covers
      // reload only when the sync changed one.
      let libraryChanged = (try? await store?.revision()) != before
      if libraryChanged {
        homeLibraryChanged()
        await serverStatesChanged(generation: stamp)
      }
      if reloadGate.afterSync(libraryChanged: libraryChanged) {
        try await loadCache(
          artworkChanged: (try? await store?.artworkRevision()) != artworkBefore)
      }
      let revisions = await player.cachedRevisions()
      guard account.isCurrent(stamp) else { return }
      // One store query answers every cached and downloaded song at once.
      let records = downloads?.records ?? []
      let known =
        try await store?.sourceRevisions(ids: Set(revisions.keys).union(records.map(\.id))) ?? [:]
      guard account.isCurrent(stamp) else { return }
      // Songs the server changed or removed since their audio was cached.
      var changed = Set(
        revisions.filter { id, revision in known[id].map { $0 != revision } ?? true }.keys)
      var invalid = false
      if let downloads {
        var allowed: [String: String?] = [:]
        for record in records {
          if let current = known[record.id] {
            allowed.updateValue(current, forKey: record.id)
            if record.state == "ready" && record.track.sourceRevision != current {
              changed.insert(record.id)
            }
          } else if record.state == "ready" {
            changed.insert(record.id)
          }
        }
        if !changed.isEmpty { invalid = await player.invalidateSources(changed: changed) }
        await downloads.reconcile(allowed)
        guard account.isCurrent(stamp) else { return }
        downloads.resume(automatic: true)
      } else if !changed.isEmpty {
        invalid = await player.invalidateSources(changed: changed)
      }
      guard account.isCurrent(stamp) else { return }
      await adoptServerPreferences(service, generation: stamp)
      let editsApplied = await flushEdits()
      guard account.isCurrent(stamp) else { return }
      await flushRequests()
      guard account.isCurrent(stamp) else { return }
      if editsApplied {
        let artworkBefore = try? await store?.artworkRevision()
        try await sync.refresh()
        try await loadCache(
          artworkChanged: (try? await store?.artworkRevision()) != artworkBefore)
        await adoptServerPreferences(service, generation: stamp)
      }
      try await watch?.reconcile()
      await flushHistory()
      await refreshServerRequests()
      guard account.isCurrent(stamp) else { return }
      for line in refreshFailures.succeeded() { DiagnosticsLog.shared.record("sync", line) }
      DiagnosticsLog.shared.record(
        "sync",
        "refreshed library in \(Int((DiagnosticsLog.shared.activeTime - began) / .milliseconds(1))) ms"
          + (changed.isEmpty
            ? "" : "; \(changed.count) changed songs" + (invalid ? ", playback stopped" : "")))
      message =
        invalid ? "Playback stopped because the server changed or removed cached music." : nil
      syncFailed = false
      // Favourites left unknown by a failed read are read again (Codex on #212).
      if !favourites.unresolved.isEmpty {
        Task { [weak self] in await self?.readUnresolvedFavourites(generation: stamp) }
      }
    } catch NativeFailure.authentication {
      guard account.isCurrent(stamp) else { return }
      revoked(generation: stamp)
    } catch {
      guard account.isCurrent(stamp) else { return }
      // The cached library carries on quietly; the next foreground or pull retries.
      let key = error.networkFailure.map { "offline \($0.code.rawValue)" } ?? error.localizedDescription
      for line in refreshFailures.failed(key: key, line: "refresh failed: \(error.localizedDescription)") {
        DiagnosticsLog.shared.record("sync", line)
      }
      message = nil
      syncFailed = true
    }
    // A request notice from during this refresh, from its own send or one
    // already running, is shown after the refresh's own message.
    if let held = heldRequestNotice, account.isCurrent(stamp) {
      heldRequestNotice = nil
      message = held
    }
    // A category chosen during this sync, changed or failed, loads now;
    // `syncing` stays set until this finishes, so a later choice joins it.
    while account.isCurrent(stamp), reloadGate.takePending() {
      do { try await loadCache() } catch { message = error.userMessage }
    }
  }
  /// Reloads the lists from the local library. `artworkChanged` also redraws
  /// every cover; only an account change or a sync that changed covers needs it.
  private func loadCache(artworkChanged: Bool = false) async throws {
    guard let store else { return }
    let stamp = account.generation
    let tracks = try await store.tracks(sort: songSort)
    let albums = try await store.albums(category: albumCategory, sort: albumSort)
    let artists = try await store.artists(sort: artistSort)
    let playlists = try await store.playlists()
    let count = try await store.count(.tracks)
    let categoryCounts = try await store.albumCategoryCounts()
    let artistCount = try await store.ownArtistCount()
    guard account.isCurrent(stamp) else { return }
    songs = tracks
    self.albums = albums
    self.artists = artists
    self.playlists = playlists
    songCount = count
    albumCategoryCounts = categoryCounts
    albumCount = categoryCounts[albumCategory] ?? 0
    self.artistCount = artistCount
    if artworkChanged {
      artistCovers = [:]
    coverSwatches = [:]
      artworkRefresh += 1
      // What a library address resolved to may be the old cover's hash.
      images.forgetAddresses()
      homeLibraryChanged()
    }
    libraryRevision += 1
  }
  /// Bumped after each reload from the local library, so open album and playlist
  /// pages show what the last sync brought instead of the snapshot they opened with.
  var libraryRevision = 0
  /// When each Home section last came from the server this launch. Cached
  /// sections have none, so they show at once and refresh behind the scenes.
  @ObservationIgnored private var homeLoadedAt: [HomeSection: Date] = [:]
  @ObservationIgnored private var homePreparingRetries = 0
  private static let homeFreshness: TimeInterval = 30 * 60
  /// Library changes can change recommendations, mixes and Recently added
  /// (and their covers); the next Home visit refreshes them.
  func homeLibraryChanged() {
    for section in HomeSection.followsLibrary { homeLoadedAt[section] = nil }
  }
  /// Home's catalogue albums, fetched ahead one at a time with their
  /// recommended edition and its cover, so a tap opens like a library album.
  private func prefetchAlbums(_ albums: [DiscoveryItem]) {
    // Optional work: not on cellular, Low Data Mode or Low Power Mode.
    guard network.conditions.allowsPrefetch else { return }
    let stamp = account.generation
    Task(priority: .utility) { [weak self] in
      for album in albums.prefix(20) {
        guard let self, account.isCurrent(stamp), !Task.isCancelled,
          network.conditions.allowsPrefetch
        else { return }
        let key = "editions:\(album.id):0"
        guard peekCatalogue(key: key, as: CatalogueEditions.self) == nil else { continue }
        guard
          let editions = try? await cachedCatalogue(
            key: key,
            conditional: { try await $0.catalogueEditions(groupId: album.id, offset: 0, etag: $1) }
          ),
          let first = editions.editions.first
        else { continue }
        _ = try? await cachedCatalogue(
          key: "release:" + first.id,
          conditional: { try await $0.catalogueRelease(releaseId: first.id, etag: $1) })
        _ = await artwork(.release(first.id), width: 512)
      }
    }
  }
  func refreshHome(force: Bool = false) async {
    guard let service else { return }
    let sections = HomeSection.allCases.filter { section in
      guard !homeLoading.contains(section) else { return false }
      if force { return true }
      if homeContent[section] == nil { return homeErrors[section] == nil }
      guard let loaded = homeLoadedAt[section] else { return true }
      return Date().timeIntervalSince(loaded) > Self.homeFreshness
        || homeContent[section]?.feed?.state == .preparing
        || homeContent[section]?.mixesPreparing == true
        || (section == .mixes && Date().timeIntervalSince(loaded) > 300)
    }
    guard !sections.isEmpty else { return }
    let stamp = account.generation
    homeLoading.formUnion(sections)
    for section in sections { homeErrors[section] = nil }
    await withTaskGroup(of: (HomeSection, Result<HomeContent, NativeFailure>).self) { group in
      for section in sections {
        group.addTask {
          do {
            return (section, .success(try await service.home(section)))
          } catch let failure as NativeFailure {
            return (section, .failure(failure))
          } catch {
            return (section, .failure(.unavailable(error.userMessage)))
          }
        }
      }
      for await (section, result) in group {
        guard account.isCurrent(stamp), !Task.isCancelled else {
          group.cancelAll()
          continue
        }
        homeLoading.remove(section)
        switch result {
        case .success(let content):
          homeContent[section] = content
          homeLoadedAt[section] = Date()
          try? await store?.cache(content, key: "home:" + section.rawValue)
          if let feed = content.feed { prefetchAlbums(feed.albums) }
        case .failure(.authentication):
          group.cancelAll()
          revoked(generation: stamp)
        case .failure(let error):
          // Cached content stays visible; only an empty section shows the error.
          if homeContent[section] == nil { homeErrors[section] = error.userMessage }
        }
      }
    }
    guard account.isCurrent(stamp) else { return }
    homeLoading.subtract(sections)
    // Like the web: a chart still being prepared is asked for again, a few times.
    if HomeSection.allCases.contains(where: {
      homeContent[$0]?.feed?.state == .preparing || homeContent[$0]?.mixesPreparing == true
    }),
      homePreparingRetries < 6
    {
      homePreparingRetries += 1
      try? await Task.sleep(for: .seconds(5 * homePreparingRetries))
      guard account.isCurrent(stamp), !Task.isCancelled else { return }
      await refreshHome()
    } else {
      homePreparingRetries = 0
    }
  }

  /// Songs shared to PigMusic from the Shazam app or Apple Music (D-70):
  /// into the "Shazamed" list, an Apple song looked up through the server
  /// while signed in. Nothing is requested.
  func receiveShazamShares() async {
    var server: (@Sendable (Int) async throws -> ResolvedApple?)?
    if let service {
      server = { try await service.resolveApple(kind: "song", appleId: $0) }
    }
    await ShazamListener.shared.receiveShares(server: server)
  }

  func readCatalogue<T: Sendable>(
    _ fetch: @Sendable (NativeService) async throws -> T
  ) async throws -> T {
    guard let service else {
      throw NativeFailure.unavailable("Connect to your server to browse the catalogue.")
    }
    let stamp = account.generation
    do {
      let value = try await fetch(service)
      try Task.checkCancellation()
      try account.check(stamp)
      return value
    } catch NativeFailure.authentication {
      revoked(generation: stamp)
      throw NativeFailure.authentication
    }
  }

  /// A check for a multi-step operation: whether the account it started
  /// under is still signed in (the generation it captured).
  func isCurrent(_ stamp: Int) -> Bool { account.isCurrent(stamp) }
  var currentGeneration: Int { account.generation }

  func performPersonal<T: Sendable>(
    _ operation: @Sendable (NativeService) async throws -> T
  ) async throws -> T {
    guard canMutate, let service else {
      throw NativeFailure.unavailable("Connect to PigMusic and wait for your library to finish loading.")
    }
    let stamp = account.generation
    mutationActive = true
    defer { if account.isCurrent(stamp) { mutationActive = false } }
    do {
      let result = try await operation(service)
      try Task.checkCancellation()
      try account.check(stamp)
      await refresh(afterMutation: true)
      try account.check(stamp)
      return result
    } catch NativeFailure.authentication {
      revoked(generation: stamp)
      throw NativeFailure.authentication
    } catch NativeFailure.busyUnconfirmed {
      // It may have been saved (#239): show what the server has now, and
      // never send it again on the person's behalf.
      if account.isCurrent(stamp) { await refresh(afterMutation: true) }
      throw NativeFailure.busyUnconfirmed
    }
  }

  func loadRecentSearches() async {
    guard !recentSearchesLoaded, let store else { return }
    let stamp = account.generation
    let saved = try? await store.cached("search-recent", as: RecentSearches.self)
    // A pick made while this read is newer than what was saved.
    guard account.isCurrent(stamp), !recentSearchesLoaded else { return }
    recentSearchesLoaded = true
    if let saved, recentSearches.picks.isEmpty { recentSearches = saved }
  }
  /// A recent pick as this phone's library holds it now.
  func currentPick(_ pick: RecentPick) async -> RecentPick {
    await store?.current(pick) ?? pick
  }
  /// Plays a recent song as the library holds it now; dropped if the account
  /// changes while it is looked up (#101).
  func replayRecent(_ pick: RecentPick) async {
    let stamp = account.generation
    let fresh = await currentPick(pick)
    guard account.isCurrent(stamp), case .song(let song) = fresh else { return }
    rememberSearch(fresh)
    play(
      [song],
      origin: song.serverState == "household_available" ? .catalogue(artistId: nil) : .library)
  }
  func rememberSearch(_ pick: RecentPick) {
    recentSearches.add(pick)
    saveRecentSearches()
  }
  func clearRecentSearches() {
    recentSearches.clear()
    saveRecentSearches()
  }
  private func saveRecentSearches() {
    guard let store else { return }
    recentSearchesLoaded = true
    let value = recentSearches
    Task { try? await store.cache(value, key: "search-recent") }
  }
  /// A song's favourite state: a waiting edit or the cached answer at once,
  /// then the server's when it can be reached.
  func loadFavourite(_ track: Track) async {
    let key = EditOutbox.favouriteCacheKey(track.id)
    // Every answer belongs to this account: a sign-out while reading drops it (#101).
    let stamp = account.generation
    let store = self.store
    if let value = EditOutbox.favourite(track.id, in: pendingEdits) {
      // The edit waiting on this phone is the newest state; a refused one
      // isn't (Codex on #212).
      favourites.change(track.id, value)
      return
    }
    // Until a read answers, the song waits for a retry (Codex on #212).
    favourites.wanted(track)
    // A change made while this reads wins over what the read finds.
    let revision = favourites.revision(track.id)
    // Only a cached answer is shown before the server's; with none (never
    // read, or dropped after a refusal) the state stays unknown (#212).
    if favourites[track.id] == nil,
      let cached = (try? await store?.cached(key, as: Bool.self)) ?? nil
    {
      guard account.isCurrent(stamp) else { return }
      if favourites[track.id] == nil { favourites.answer(track.id, cached, since: revision) }
    }
    let confirmed = try? await readCatalogue({ try await $0.favourite(track) })
    guard account.isCurrent(stamp), let confirmed,
      favourites.answer(track.id, confirmed, since: revision)
    else { return }
    try? await store?.cache(confirmed, key: key)
  }
  /// A song's favourite state from this iPhone alone, a waiting edit or the
  /// cached answer, without asking the server (#229): read for each song whose
  /// ⋯ menu could open, so the menu shows its favourite item at once.
  func peekFavourite(_ track: Track) async {
    guard favourites[track.id] == nil else { return }
    if let value = EditOutbox.favourite(track.id, in: pendingEdits) {
      favourites.change(track.id, value)
      return
    }
    let stamp = account.generation
    let revision = favourites.revision(track.id)
    guard
      let cached = (try? await store?.cached(EditOutbox.favouriteCacheKey(track.id), as: Bool.self))
        ?? nil,
      account.isCurrent(stamp), favourites[track.id] == nil
    else { return }
    favourites.answer(track.id, cached, since: revision)
  }
  /// Flips a song's favourite at once (#212) and saves it through the edit
  /// outbox; a refused save puts the old state back. The new state, and
  /// whether the server confirmed it (false when it waits on this phone);
  /// nil while the state is unknown.
  @discardableResult func tapFavourite(_ track: Track) async throws -> (
    value: Bool, confirmed: Bool
  )? {
    let stamp = account.generation
    let store = self.store
    guard let tap = favourites.tap(track.id) else { return nil }
    let mutation = OfflineMutation(action: "favourite", track: track, favourite: tap.value)
    do {
      let outcome = try await saveEditOutcome(mutation)
      try account.check(stamp)
      // Refused: flushEdits has already put the star back and taken the
      // edit out of the outbox (Codex rounds 1 and 2).
      if outcome == .refused { throw MutationRejection.rejected }
      let confirmed = outcome == .confirmed
      // A newer tap meanwhile is what the star shows and what is kept.
      try? await store?.cache(favourites[track.id] ?? tap.value, key: EditOutbox.favouriteCacheKey(track.id))
      confirmations += 1
      return (tap.value, confirmed)
    } catch {
      // Refused, or never saved: the star goes back unless a newer tap came.
      favourites.refused(tap)
      throw error
    }
  }
  /// Rises when the server refuses a favourite: the star says it wasn't saved.
  var favouriteRefusals = 0
  /// Shows each refused favourite's song as it is now (Codex round 2 and
  /// confirmation on #212): the newest tap still waiting, or else the
  /// server's state, read again and cached; never a guess.
  private func favouritesRefused(_ refused: [RefusedFavourite]) {
    let store = self.store
    let stamp = account.generation
    for item in refused {
      favourites.refusal(item)
      if let value = item.value {
        Task { try? await store?.cache(value, key: EditOutbox.favouriteCacheKey(item.trackId)) }
      }
    }
    favouriteRefusals += 1
    // An unknown state is read again now, and after every successful refresh
    // until a read answers.
    Task { [weak self] in await self?.readUnresolvedFavourites(generation: stamp) }
  }
  /// Reads the favourite of every song shown with an unknown state (Codex on
  /// #212): after a refusal whose read failed, or a song first opened
  /// offline. A read that fails leaves it for the next successful refresh.
  func readUnresolvedFavourites(generation stamp: Int) async {
    let store = self.store
    let answers = await favouriteRecovery.run(
      states: { [unowned self] in favourites },
      answer: { [unowned self] id, value, revision in
        account.isCurrent(stamp) && favourites.answer(id, value, since: revision)
      },
      editWaiting: { [unowned self] id in EditOutbox.favourite(id, in: pendingEdits) != nil },
      read: { [weak self] track in
        guard let self, account.isCurrent(stamp) else { return nil }
        return try? await readCatalogue({ try await $0.favourite(track) })
      })
    // Cached only while still what the star shows: a tap since then wins.
    for answer in answers where account.isCurrent(stamp)
      && favourites[answer.track.id] == answer.value
    {
      try? await store?.cache(answer.value, key: EditOutbox.favouriteCacheKey(answer.track.id))
    }
  }
  /// One recovery read loop at a time (Codex on #212).
  @ObservationIgnored private let favouriteRecovery = FavouriteRecovery()

  @discardableResult func saveEdit(_ mutation: OfflineMutation) async throws -> Bool {
    try await saveEditOutcome(mutation) == .confirmed
  }
  /// Saves an edit and says whether the server confirmed it, it waits on
  /// this phone, or it was refused (Codex on #212).
  func saveEditOutcome(_ mutation: OfflineMutation) async throws -> EditOutbox.Outcome {
    guard let store, person != nil else { throw NativeFailure.authentication }
    let stamp = account.generation
    try await store.enqueueMutation(mutation)
    try account.check(stamp)
    pendingEdits = try await store.pendingMutations()
    let applied = await flushEdits()
    try account.check(stamp)
    if applied { await refresh() }
    // The refresh can end this account (Codex on #101): its answer is stale.
    try account.check(stamp)
    // Only the server's receipt confirms it: an edit discarded meanwhile has
    // also left the outbox (Codex, batch 9).
    return EditOutbox.outcome(
      of: mutation.id, confirmed: appliedEdits.remove(mutation.id) != nil,
      pending: (try? await store.pendingMutations()) ?? pendingEdits)
  }
  /// Edits the server's receipts confirmed, until saveEdit reads them.
  @ObservationIgnored private var appliedEdits: Set<String> = []
  @discardableResult func flushEdits() async -> Bool {
    guard !flushingEdits, let store, let service, person != nil else { return false }
    flushingEdits = true
    let stamp = account.generation
    defer { flushingEdits = false }
    var applied = false
    do {
      // Edits to one thing stay in order; a network failure stops all.
      let stop = try await EditOutbox.send(
        store: store, isCurrent: { [account] in account.isCurrent(stamp) }, applied: &applied,
        confirmed: { [weak self] id in
          guard let self else { return }
          // Bounded: entries are removed as saveEdit reads them.
          if appliedEdits.count > 500 { appliedEdits.removeAll() }
          appliedEdits.insert(id)
        }
      ) { try await service.applyMutation($0) }
      switch stop {
      case .sent: break
      case .stale: return applied
      case .signedOut:
        revoked(generation: stamp)
        return applied
      }
      // Any favourite the server refused, tapped now or long ago, goes back.
      let refused = try await EditOutbox.settleRefusedFavourites(store: store)
      if account.isCurrent(stamp), !refused.isEmpty { favouritesRefused(refused) }
      if account.isCurrent(stamp) { pendingEdits = try await store.pendingMutations() }
    } catch { if account.isCurrent(stamp) { message = error.userMessage } }
    return applied
  }
  // MARK: Song requests (#206)

  /// Songs this account asked for here, in the outbox or sent (#206); with
  /// those waiting for their Undo they show Requested at once.
  var requestedSongs = RequestMarks()
  @ObservationIgnored private var flushingRequests = false
  static func undoKey(request id: String) -> String { "request:" + id }
  static func undoKey(cancelRequest id: String) -> String { "request-cancel:" + id }
  func isRequested(_ id: String) -> Bool {
    requestedSongs.contains(id) || undo.hides(Self.undoKey(request: id))
  }
  /// The library changed (#211): a requested song this phone's library now
  /// holds is no longer marked, and search asks the server again for the
  /// states it shows (without holding up the refresh).
  private func serverStatesChanged(generation stamp: Int) async {
    let marked = requestedSongs.all
    if let store, !marked.isEmpty, let held = try? await store.sourceRevisions(ids: marked),
      account.isCurrent(stamp)
    {
      requestedSongs.settled(held.keys)
    }
    guard account.isCurrent(stamp), let foundSearch else { return }
    Task { [weak self] in
      await foundSearch.serverChanged()
      guard let self, account.isCurrent(stamp) else { return }
      noteSearchStates()
    }
  }
  /// Search's newest answer: songs it shows finished are no longer marked.
  func noteSearchStates() {
    requestedSongs.observed(foundSearch?.results?.songs ?? [])
  }
  /// A song as search's newest answer has it (#211), so an open song sheet
  /// follows a request that finishes; the song itself when search hasn't it.
  func liveState(_ track: Track) -> Track {
    foundSearch?.results?.songs.first {
      $0.id == track.id || $0.libraryTrackId == track.id
    }?.actionTrack ?? track
  }
  /// This phone's copy of the person's library's own song, found by id,
  /// recording, or title and artist (#211): a row from Home, a catalogue
  /// page or search carries a catalogue or recording id, not the library's.
  func ownCopy(of track: Track) async -> Track? {
    guard let store else { return nil }
    return try? await store.ownCopy(of: track)
  }
  /// Asks the server for `track` at once, with Undo (D-79): when the Undo
  /// runs out the request joins the outbox, which sends it now or, offline,
  /// the next time the server answers. Nothing on screen waits for it.
  func requestLater(_ track: Track) {
    let stamp = account.generation
    undo.schedule(Self.undoKey(request: track.id), label: "Requested " + track.title) {
      [weak self] in
      // Only for the account that asked.
      guard let self, account.isCurrent(stamp), let store else { return false }
      do {
        try await RequestOutbox.add(track, to: store)
      } catch {
        if account.isCurrent(stamp) { notify(error) }
        return false
      }
      guard account.isCurrent(stamp) else { return false }
      requestedSongs.queued(track.id)
      // The Undo chain doesn't wait on the network.
      Task { await self.flushRequests() }
      return true
    }
  }
  /// What a send said about a song left out or refused, kept while a
  /// refresh runs: a refresh ends by setting `message`, which would clear it
  /// (Codex confirmation rounds), so the refresh shows it again last.
  @ObservationIgnored private var heldRequestNotice: String?
  private func show(requestNotice: String) {
    message = requestNotice
    if syncing { heldRequestNotice = requestNotice }
  }
  /// Sends the request outbox; a failure to reach the server keeps it for
  /// the next refresh. A song left out or refused says so.
  func flushRequests() async {
    guard !flushingRequests, let store, let service, person != nil else { return }
    flushingRequests = true
    defer { flushingRequests = false }
    let stamp = account.generation
    // A request made while one was sent goes in the next round.
    for _ in 0..<10 {
      let titles = Dictionary(
        (await RequestOutbox.pending(store)).map { ($0.id, $0.title) },
        uniquingKeysWith: { first, _ in first })
      let report: RequestOutbox.Report
      do {
        report = try await RequestOutbox.send(
          store: store, isCurrent: { [account] in account.isCurrent(stamp) }
        ) { try await service.requestMusic($0) }
      } catch {
        if account.isCurrent(stamp) { show(requestNotice: error.userMessage) }
        return
      }
      guard account.isCurrent(stamp) else { return }
      requestedSongs.sent(report.requested)
      requestedSongs.refused(report.skipped + report.refused)
      // Codex round 2: a song left out or refused says so.
      if let notice = report.notice(titles: titles) { show(requestNotice: notice) }
      if !report.requested.isEmpty || !report.skipped.isEmpty || !report.refused.isEmpty {
        requestsRevision += 1
      }
      switch report.stop {
      case .done:
        if await RequestOutbox.pending(store).isEmpty {
          await refreshServerRequests()
          return
        }
      case .offline, .stale:
        return
      case .signedOut:
        revoked(generation: stamp)
        return
      }
    }
  }
  /// Cancels this person's request for a song at once, with Undo (D-79).
  func cancelRequestLater(_ item: RequestGroupItem, done: @escaping @MainActor (Bool) -> Void) {
    let stamp = account.generation
    undo.schedule(
      Self.undoKey(cancelRequest: item.id), label: "Cancelled the request for " + item.title
    ) { [weak self] in
      guard let self, account.isCurrent(stamp) else { return false }
      do {
        let result = try await performPersonal { try await $0.cancelRequest(id: item.id) }
        // The list can't be matched to songs: no sent song stays Requested.
        requestedSongs.requestsChanged()
        requestsRevision += 1
        done(result.continuesForOthers)
        return true
      } catch {
        if account.isCurrent(stamp) { message = error.userMessage }
        return false
      }
    }
  }
  func discardEdit(_ id: String) async {
    guard !flushingEdits, let store else { return }
    try? await store.acknowledgeMutation(id)
    pendingEdits = (try? await store.pendingMutations()) ?? []
    _ = await flushEdits()
    await refresh()
  }
  func cachedCatalogue<T: Codable & Sendable>(
    key: String, load: @Sendable (NativeService) async throws -> T
  ) async throws -> T {
    let stamp = account.generation
    let database = store
    do {
      let result = try await readCatalogue(load)
      try account.check(stamp)
      remember(result, key: key)
      try? await database?.cache(result, key: "catalogue:" + key)
      return result
    } catch NativeFailure.authentication {
      throw NativeFailure.authentication
    } catch is CancellationError { throw CancellationError() } catch {
      try account.check(stamp)
      if let saved = try? await database?.cached("catalogue:" + key, as: T.self) { return saved }
      throw error
    }
  }
  /// Like `cachedCatalogue`, but revalidates the saved copy with its ETag, so an
  /// unchanged album, edition list or playlist answers `304` with no body.
  func cachedCatalogue<T: Codable & Sendable>(
    key: String,
    conditional load:
      @escaping @Sendable (NativeService, _ etag: String?) async throws ->
      Conditional<T>
  ) async throws -> T {
    let stamp = account.generation
    guard let database = store else {
      return try await cachedCatalogue(key: key) { service in
        guard case .fresh(let value, _) = try await load(service, nil) else {
          throw NativeFailure.unavailable("This page is unavailable. Try again shortly.")
        }
        return value
      }
    }
    do {
      let result = try await readCatalogue { service in
        try await ConditionalCatalogue(store: database).load(key: key) {
          try await load(service, $0)
        }
      }
      try account.check(stamp)
      remember(result, key: key)
      return result
    } catch NativeFailure.authentication {
      throw NativeFailure.authentication
    } catch is CancellationError { throw CancellationError() } catch {
      try account.check(stamp)
      if let saved = try? await database.cached("catalogue:" + key, as: T.self) { return saved }
      throw error
    }
  }
  /// The last catalogue answer for a key, so a revisited page draws at once.
  /// Pages still refresh it through `cachedCatalogue`.
  func peekCatalogue<T: Sendable>(key: String, as type: T.Type = T.self) -> T? {
    catalogueMemory.peek(key, as: T.self)
  }
  /// The memory answer, else the one saved on this device from an earlier session.
  func storedCatalogue<T: Codable & Sendable>(key: String, as type: T.Type = T.self) async -> T? {
    if let value = peekCatalogue(key: key, as: T.self) { return value }
    let stamp = account.generation
    guard let saved = try? await store?.cached("catalogue:" + key, as: T.self),
      account.isCurrent(stamp)
    else { return nil }
    remember(saved, key: key)
    return saved
  }
  private func remember(_ value: any Sendable, key: String) {
    catalogueMemory.remember(value, key: key)
  }
  /// Artwork addressed by stored hash, or a server alias for catalogue items.
  enum ArtworkSource: Hashable, Sendable {
    case stored(String)
    case release(String)
    case releaseGroup(String)
    case artist(String)
    case appleAlbum(Int)
    /// A library album's or artist's own artwork, for one the server has no
    /// stored image for yet (#168).
    case library(LibraryArtwork)
  }
  /// How long a screen waits for a catalogue cover the server is still
  /// fetching (its first ask is answered "not available"): PigKit's
  /// `CoverRetry.waits`, for as long as the tile's task lives. The lock
  /// screen waits as long (its own 2, 6 and 20 s were shorter), since a song
  /// plays for minutes and nothing else re-asks for it.
  static let coverRetries = CoverRetry.waits
  static let playerRetries = CoverRetry.waits
  func artwork(
    _ source: ArtworkSource?, width: Int = 256, retryAfter: [Duration] = coverRetries
  ) async -> UIImage? {
    switch source {
    case .stored(let sha): return await artwork(sha: sha, width: width)
    case .release(let id):
      return await catalogueImage("release", id, width, retryAfter: retryAfter) {
        try await $0.catalogueArtwork(kind: .release, id: id, request: $1)
      }
    case .releaseGroup(let id):
      return await catalogueImage("release-group", id, width, retryAfter: retryAfter) {
        try await $0.catalogueArtwork(kind: .releaseGroup, id: id, request: $1)
      }
    case .artist(let id):
      return await catalogueImage("artist", id, width, retryAfter: retryAfter) {
        try await $0.artistArtwork(id: id, request: $1)
      }
    case .appleAlbum(let id):
      return await catalogueImage("apple-album", String(id), width, retryAfter: retryAfter) {
        try await $0.catalogueArtwork(kind: .appleAlbum, id: String(id), request: $1)
      }
    case .library(let address):
      return await libraryImage(address, width, retryAfter: retryAfter)
    case nil: return nil
    }
  }
  /// A library album's or artist's own artwork: its address is resolved to
  /// the stored hash (asked again on the same schedule while the server is
  /// still fetching the image), then drawn and cached like any stored cover.
  private func libraryImage(
    _ address: LibraryArtwork, _ width: Int, retryAfter: [Duration]
  ) async -> UIImage? {
    guard let service, images.cache != nil else { return nil }
    let stamp = account.generation
    do {
      guard
        let sha = try await images.hash(
          of: address, retryAfter: retryAfter,
          isCurrent: { [account] in account.isCurrent(stamp) },
          resolve: { try await service.libraryArtworkHash(address) })
      else { return nil }
      return await artwork(sha: sha, width: width)
    } catch NativeFailure.authentication {
      revoked(generation: stamp)
      return nil
    } catch { return nil }
  }
  func artistArtwork(id: String) async -> UIImage? { await artwork(.artist(id), width: 512) }
  /// Fanart background and logo for the full artist page; nil when unavailable.
  func artistBackground(id: String) async -> UIImage? {
    await catalogueImage("background", id, 1024) {
      try await $0.artistBackground(id: id, request: $1)
    }
  }
  func artistLogo(id: String) async -> UIImage? {
    await catalogueImage("logo", id, 1024) { service, _ in try await service.artistLogo(id: id) }
  }
  /// The image already decoded in memory, for drawing a revisited page at once.
  func memoryCatalogueImage(_ kind: String, _ id: String, width: Int) -> UIImage? {
    guard let request = ArtworkLoader<UIImage>.catalogueRequest(kind: kind, id: id, width: width)
    else { return nil }
    return images.memoryImage(request)
  }
  private func catalogueImage(
    _ kind: String, _ id: String, _ width: Int, retryAfter: [Duration] = [],
    fetch: @escaping @Sendable (NativeService, ArtworkRequest) async throws -> Data
  ) async -> UIImage? {
    guard let service, images.cache != nil else { return nil }
    let stamp = account.generation
    // Catalogue artwork has no content hash on the client: key it by its alias,
    // which exists only for an id of the kind's shape (a UUID, or Apple's number).
    guard let request = ArtworkLoader<UIImage>.catalogueRequest(kind: kind, id: id, width: width)
    else { return nil }
    do {
      return try await images.load(
        request, stopsWhenCancelled: false, retryAfter: retryAfter,
        isCurrent: { [account] in account.isCurrent(stamp) }
      ) { try await fetch(service, $0) }
    } catch NativeFailure.authentication {
      revoked(generation: stamp)
      return nil
    } catch { return nil }
  }
  /// A saved artist shows their first album cover with artwork.
  /// The library artist behind an album or catalogue page, by ID or else by exact name.
  func savedArtist(id: String? = nil, name: String) async -> Artist? {
    guard let store else { return nil }
    if let id, let artist = try? await store.artist(id: id) { return artist }
    return try? await store.artist(named: name)
  }
  func artistCover(_ artist: Artist) async -> String? {
    // The server's portrait when it has one; its album choice otherwise.
    if let sha = artist.coverSha { return sha }
    if let cached = artistCovers[artist.id] { return cached }
    guard let store else { return nil }
    var albums = (try? await store.albums(artistId: artist.id, limit: 12)) ?? []
    if let preferred = artist.coverAlbumId,
      let index = albums.firstIndex(where: { $0.id == preferred })
    {
      albums.insert(albums.remove(at: index), at: 0)
    }
    let sha = albums.lazy.compactMap(\.coverSha).first
    artistCovers[artist.id] = sha
    return sha
  }
  /// The colour a cover lends its page (#199), or nil without a cover. The
  /// image comes from the same loader (and the same width) the page draws, so
  /// it is usually in memory; sampling runs off the main actor and the answer
  /// is kept per cover.
  func coverSwatch(_ source: ArtworkSource?, width: Int) async -> CoverRGB? {
    guard let source else { return nil }
    let key = "\(artworkNamespace)|\(source)"
    if let known = coverSwatches[key] { return known }
    guard let image = await artwork(source, width: width), let cgImage = image.cgImage,
      !Task.isCancelled
    else { return nil }
    let swatch = await CoverSampler.swatch(cgImage)
    if coverSwatches.count > 256 { coverSwatches = [:] }
    coverSwatches[key] = swatch
    return swatch
  }
  func sortLibrary(_ collection: LibraryCollection, by sort: LibrarySort) async {
    guard !loadingMore, !syncing else { return }
    switch collection {
    case .tracks: songSort = sort
    case .albums: albumSort = sort
    case .artists: artistSort = sort
    case .playlists: return
    }
    do { try await loadCache() } catch { message = error.userMessage }
  }
  /// Where a song's file is on this side of the server: iPhone and Apple Watch.
  func deviceCopies(_ trackId: String) -> (phone: DeviceCopyMark?, watch: DeviceCopyMark?) {
    let phone = downloads?.records.first { $0.id == trackId }.map {
      $0.state == "ready" ? DeviceCopyMark.ready : $0.state == "failed" ? .failed : .pending
    }
    var watchMark: DeviceCopyMark?
    if let record = watch?.downloads?.records.first(where: { $0.id == trackId }) {
      // A delivered song's iPhone copy is gone because the Watch has it (#218).
      let onWatch =
        record.delivered
        || (record.checksum != nil && watch?.receipt?.ready[trackId] == record.checksum)
      watchMark = onWatch ? .ready : record.state == "failed" ? .failed : .pending
    } else if watch?.receipt?.ready[trackId] != nil {
      watchMark = .ready
    }
    return (phone, watchMark)
  }
  func phoneProgress(_ trackId: String) -> Double {
    guard let record = downloads?.records.first(where: { $0.id == trackId }), record.total > 0
    else { return 0 }
    return min(1, Double(record.bytes) / Double(record.total))
  }
  /// Active means requested and not yet finished (queued or downloading), so
  /// sending a 10-song album shows 10 and counts down to 0.
  var phoneActiveDownloads: Int {
    downloads?.records.count { $0.state != "ready" && $0.state != "failed" } ?? 0
  }
  var watchActiveDownloads: Int {
    let receipt = watch?.receipt
    return watch?.downloads?.records.count {
      $0.state != "failed" && !$0.delivered
        && ($0.checksum == nil || receipt?.ready[$0.id] != $0.checksum)
    } ?? 0
  }
  /// The Downloads tab badge: the three sections' active counts combined.
  var pendingDownloadCount: Int {
    serverActiveRequests + phoneActiveDownloads + watchActiveDownloads
  }
  /// Bumped when the server reports request progress, so open views reload.
  var requestsRevision = 0
  func refreshServerRequests() async {
    guard let page = try? await readCatalogue({ try await $0.requests(limit: 1) }) else { return }
    serverActiveRequests = page.active
  }
  func showAlbums(category: String) async {
    guard category != albumCategory else { return }
    albumCategory = category
    albums = []
    albumCount = albumCategoryCounts[category] ?? 0
    guard !syncing else {
      // Loaded when the sync finishes, whether or not it changes the library.
      reloadGate.viewChangedDuringSync()
      return
    }
    do { try await loadCache() } catch { message = error.userMessage }
  }
  func more(_ collection: LibraryCollection) async {
    guard let store, !loadingMore, !syncing else { return }
    let stamp = account.generation
    loadingMore = true
    defer { if account.isCurrent(stamp) { loadingMore = false } }
    do {
      switch collection {
      case .tracks:
        let next = try await store.tracks(offset: songs.count, sort: songSort)
        guard account.isCurrent(stamp) else { return }
        songs += next
      case .albums:
        let next = try await store.albums(
          category: albumCategory, offset: albums.count, sort: albumSort)
        guard account.isCurrent(stamp) else { return }
        albums += next
      case .artists:
        let next = try await store.artists(offset: artists.count, sort: artistSort)
        guard account.isCurrent(stamp) else { return }
        artists += next
      case .playlists: break
      }
    } catch { message = error.userMessage }
  }
  func albumTracks(_ album: Album) async -> [Track] {
    (try? await store?.tracks(albumId: album.id)) ?? []
  }
  func artwork(sha: String?, width: Int = 256) async -> UIImage? {
    guard let sha, let request = try? ArtworkRequest(sha: sha, width: width), images.cache != nil
    else { return nil }
    let service = self.service
    var demoData: Data?
    #if DEBUG
      if ProcessInfo.processInfo.arguments.contains("--demo") {
        demoData = demoArtwork(request)
      }
    #endif
    guard service != nil || demoData != nil else { return nil }
    let stamp = account.generation
    do {
      let synthetic = demoData
      return try await images.load(
        request, stopsWhenCancelled: true, isCurrent: { [account] in account.isCurrent(stamp) }
      ) {
        if let synthetic { return synthetic }
        guard let service else { throw ArtworkFailure.unavailable }
        return try await service.artwork($0)
      }
    } catch NativeFailure.authentication {
      revoked(generation: stamp)
      return nil
    } catch { return nil }
  }
  func flushHistory() async {
    guard !flushingHistory, let database = store, let service else { return }
    let stamp = account.generation
    flushingHistory = true
    defer { flushingHistory = false }
    do {
      try await HistoryUpload.send(
        store: database, isCurrent: { [account] in account.isCurrent(stamp) }
      ) { try await service.recordListens($0) }
    } catch NativeFailure.authentication { revoked(generation: stamp) } catch {
      // Durable events remain queued until the next confirmed connection.
    }
  }
  /// Songs in the queue that came from the person's radio, by `mix:` id.
  var radioIDs: Set<String> = []
  @ObservationIgnored private var radioLoading = false
  func startRadio() async {
    do {
      let batch = try await readCatalogue { try await $0.radio(after: nil) }
      guard !batch.tracks.isEmpty else {
        message = "Your radio is still preparing. Try again shortly."
        return
      }
      radioIDs = Set(batch.tracks.map(\.id))
      play(batch.tracks)
    } catch { message = error.userMessage }
  }
  /// Appends the next prepared songs. At the end of the queue, plays them
  /// as a fresh queue so the station never stops.
  private func refillRadio(continuing: Bool = false) {
    guard !radioLoading,
      let last = player.queue.items.last(where: { radioIDs.contains($0.track.id) })?.track.id
    else { return }
    radioLoading = true
    Task {
      defer { radioLoading = false }
      guard let batch = try? await readCatalogue({ try await $0.radio(after: last) }) else {
        return
      }
      let known = Set(player.queue.items.map(\.track.id))
      let added = batch.tracks.filter { !known.contains($0.id) }
      guard !added.isEmpty else { return }
      radioIDs.formUnion(added.map(\.id))
      if continuing { play(added) } else { player.enqueue(added) }
    }
  }
  /// Songs added to the queue as similar songs when it ran out (#202).
  var similarIDs: Set<String> = []
  @ObservationIgnored private var continuationLoading = false
  /// The queue ended while its similar songs were still being fetched: they
  /// play as soon as they arrive (Codex round 1).
  @ObservationIgnored private var continuationEnded = false
  /// Adds similar songs after the last song of a queue that isn't the radio,
  /// unless the person turned it off or repeat is on (#202). At the very end
  /// they play as a fresh queue, as the radio does.
  func continueQueue(continuing: Bool = false) {
    let queue = player.queue
    guard let current = queue.current,
      QueueContinuation.continues(
        keepPlaying: preferences.keepPlaying, repeating: player.repeatMode != .off,
        radio: radioIDs.contains(current.track.id))
    else { return }
    guard !continuationLoading else {
      // Fetched ahead and not back yet: play them when they come.
      if continuing { continuationEnded = true }
      return
    }
    let (seeds, exclude) = QueueContinuation.request(
      queue: queue.items.map(\.track.id), current: queue.index)
    continuationLoading = true
    continuationEnded = continuing
    let stamp = account.generation
    Task {
      defer {
        continuationLoading = false
        continuationEnded = false
      }
      guard
        let batch = try? await readCatalogue({
          try await $0.radioContinuation(seeds: seeds, exclude: exclude)
        }), account.isCurrent(stamp)
      else { return }
      // The queue changed while asking (a new album started): not for it.
      guard player.queue.items.map(\.track.id) == queue.items.map(\.track.id) else { return }
      let known = Set(player.queue.items.map(\.track.id))
      let added = batch.tracks.filter { !known.contains($0.id) }
      guard !added.isEmpty else { return }
      similarIDs.formUnion(added.map(\.id))
      if QueueContinuation.playsAtOnce(
        ended: continuationEnded,
        atLastSong: player.queue.index == player.queue.items.count - 1,
        playing: player.playing)
      {
        play(added)
      } else {
        player.enqueue(added)
      }
    }
  }
  /// Where the playing queue was started: the player's artist link opens the
  /// matching artist page (library, or the full catalogue page).
  var playOrigin: PlayOrigin = .library
  func play(
    _ tracks: [Track], at index: Int = 0, restored: PlaybackCheckpoint? = nil,
    origin: PlayOrigin = .library
  ) {
    playOrigin = origin
    #if DEBUG
      if ProcessInfo.processInfo.arguments.contains("--demo"),
        let url = Bundle.main.url(forResource: "demo-tone", withExtension: "m4a")
      {
        player.play(
          tracks, startingAt: index,
          artwork: { [weak self] track in
            await self?.artwork(
              track.artworkSource, width: 512, retryAfter: AppModel.playerRetries)
          }, resolve: { _ in url }
        )
        return
      }
    #endif
    guard !busy || restored != nil, let service else { return }
    let archive = downloads?.archive
    let stamp = account.generation
    player.play(
      tracks, startingAt: index, autoplay: restored == nil, restored: restored,
      artwork: { [weak self] track in
        await self?.artwork(track.artworkSource, width: 512, retryAfter: AppModel.playerRetries)
      },
      resolve: { [weak self] track in
        do {
          if let local = await archive?.localFile(track) { return local }
          return try await service.resolve(track)
        } catch NativeFailure.authentication {
          await self?.revoked(generation: stamp)
          throw NativeFailure.authentication
        }
      }
    )
    // The rest of the queue: its addresses arrive while the first song starts.
    let upcoming = Array(tracks.dropFirst(index + 1).prefix(50))
    Task { await service.preparePlayback(upcoming) }
  }
  /// Fetches playback addresses for a page's songs ahead of a tap (best effort).
  func preparePlayback(_ tracks: [Track]) async {
    guard let service, !tracks.isEmpty else { return }
    await service.preparePlayback(tracks)
  }
  private func sendWaitingActions() async {
    guard !undo.items.isEmpty else { return }
    let task = UIApplication.shared.beginBackgroundTask(withName: "Send waiting changes")
    await undo.sendAllAndWait()
    if task != .invalid { UIApplication.shared.endBackgroundTask(task) }
  }
  /// Removes a playlist song once its Undo has run out (#183), at the
  /// position and revision it was removed from (entry IDs are positions):
  /// shifted only by this iPhone's earlier removals from that revision, so
  /// any other change makes the server refuse it for review (Codex).
  func removeLater(at position: Int, from playlist: Playlist) {
    guard let entries = playlist.entries, entries.indices.contains(position) else { return }
    let entry = entries[position]
    let playlistId = playlist.id
    let revision = playlist.revision
    let favourites = playlist.favourites == true
    let stamp = account.generation
    undo.schedule(
      Self.undoKey(playlist: playlistId, revision: revision, position: position),
      label: "Removed from " + playlist.name
    ) { [weak self] in
      // Only for the account that removed it.
      guard let self, account.isCurrent(stamp) else { return false }
      do {
        // Favourites are removed by unfavouriting that exact song; the server
        // rejects a positional removal, which would block the edits behind it.
        if favourites {
          try await saveEdit(
            OfflineMutation(action: "favourite", track: entry.entryTrack, favourite: false))
          return true
        }
        // Already removed from this version (a stale copy showed it again):
        // nothing to send, or its neighbour would go.
        guard
          let sent = removals.plan(playlist: playlistId, position: position, revision: revision)
        else { return true }
        let confirmed = try await saveEdit(
          OfflineMutation(
            action: "remove", playlistId: playlistId, revision: sent.revision,
            position: sent.position))
        // Only a removal the server has applied shifts the next ones. One that
        // is merely queued (offline) may yet conflict and be discarded, so a
        // removal after it keeps its own revision and can only be refused
        // for review, never remove another song (Codex confirmation).
        removals.record(
          playlist: playlistId, position: position, revision: revision, applied: confirmed)
        return true
      } catch {
        message = error.userMessage + " Refresh the playlist before removing the song again."
        return false
      }
    }
  }
  /// The playlist removals the server applied, which shift the next ones.
  private var removals = PlaylistRemovalLedger()
  /// Whether this iPhone already removed that song of that playlist version.
  func removed(_ playlist: Playlist, at position: Int) -> Bool {
    removals.removed(playlist: playlist.id, position: position, revision: playlist.revision)
  }
  static func undoKey(playlist: String, revision: Int, position: Int) -> String {
    "playlist-entry:\(playlist)@\(revision)#\(position)"
  }
  static func undoKey(mix: String, song: String) -> String { "mix-song:" + mix + ":" + song }
  static func undoKey(download id: String) -> String { "download:" + id }
  static func undoKey(watchTransfer id: String) -> String { "watch-transfer:" + id }
  static func undoKey(removeFromLibrary id: String) -> String { UndoQueue.removalKey(id) }
  /// Removes a song from this person's library and from this iPhone and the
  /// Watch, at once with Undo (D-79, #214): no preview, no confirmation.
  /// Nothing is sent until the Undo runs out; the devices' copies go only
  /// once the server has removed it.
  func removeFromLibraryLater(_ track: Track) {
    let stamp = account.generation
    let id = track.id
    undo.schedule(
      Self.undoKey(removeFromLibrary: id), label: "Removed " + track.title + " from your library"
    ) { [weak self] in
      // Only for the account that removed it.
      guard let self, account.isCurrent(stamp) else { return false }
      do {
        _ = try await performPersonal { try await $0.removeFromLibrary(ids: [id]) }
      } catch {
        if account.isCurrent(stamp) { notify(error) }
        return false
      }
      guard account.isCurrent(stamp) else { return false }
      if downloads?.records.contains(where: { $0.id == id }) == true {
        await downloads?.remove(id: id)
      }
      if let watch, watch.downloads?.records.contains(where: { $0.id == id }) == true {
        await watch.remove(id)
      }
      return true
    }
  }
  /// Hides a mix song once its Undo has run out (#183).
  func hideLater(_ track: Track, from mix: PersonalMix) {
    let key = Self.undoKey(mix: mix.id, song: track.id)
    let stamp = account.generation
    undo.schedule(key, label: "Hidden from " + mix.title) { [weak self] in
      // Only for the account that hid it.
      guard let self, account.isCurrent(stamp) else { return false }
      do {
        try await performPersonal { try await $0.hideMixSong(track.id) }
        hiddenMixSongs.insert(key)
        return true
      } catch {
        message = error.userMessage
        return false
      }
    }
  }
  func signOut() async {
    // Removals still showing Undo belong to this account: send them first.
    await undo.sendAllAndWait()
    guard !busy, !mutationActive else { return }
    guard let service else {
      detach()
      return
    }
    busy = true
    defer { busy = false }
    do {
      let ended = try await service.signOut()
      detach()
      if !ended {
        message =
          "Signed out on this iPhone. PigMusic couldn't reach the server, so it will finish signing out the next time it can."
      }
    } catch { message = error.userMessage }
  }
  /// Sessions signed out offline, for every server, waiting to be ended
  /// by their own tokens once the server answers (D-55, #97).
  private static let parkedSessions = ParkedSessions(
    store: TokenVault(service: "au.com.pig.music.parked-sign-out", account: "sessions"))
  private func detach() {
    // The ended account's waiting actions are dropped, never sent with the
    // next account's sign-in (Codex).
    undo.discardAll()
    notices.clear()
    removals = PlaylistRemovalLedger()
    appliedEdits = []
    hiddenMixSongs = []
    requestedSongs = RequestMarks()
    heldRequestNotice = nil
    similarIDs = []
    watch?.disconnect()
    // Stale for every operation still running; live updates stop.
    account.end()
    refreshLoop.reset()
    libraryEvent = nil
    if let downloads { Task { await downloads.clear() } }
    downloads = nil
    foundSearch?.clear()
    foundSearch = nil
    favourites.clear()
    recentSearches = RecentSearches()
    recentSearchesLoaded = false
    mutationActive = false
    pendingEdits = []
    player.clearAccount()
    preferences = AccountPreferences()
    person = nil
    service = nil
    store = nil
    sync = nil
    images.detach()
    artworkNamespace = ""
    artistCovers = [:]
    coverSwatches = [:]
    catalogueMemory.clear()
    homeContent = [:]
    homeLoadedAt = [:]
    homeErrors = [:]
    homeLoading = []
    songs = []
    albums = []
    artists = []
    playlists = []
    songCount = 0
    syncFailed = false
    albumCount = 0
    artistCount = 0
    albumCategory = "album"
    albumCategoryCounts = [:]
    reloadGate = LibraryReloadGate()
    syncing = false
    loadingMore = false
    defaults.removeObject(forKey: "pig.connection")
  }
  private func revoked(generation stamp: Int) {
    guard account.isCurrent(stamp) else { return }
    DiagnosticsLog.shared.record("account", "session revoked or expired")
    detach()
    message = "This device session was revoked or expired. Please sign in again."
  }
  #if DEBUG
    private func loadDemo() async {
      do {
        let fixture =
          #"{"revision":"demo","tracks":[{"id":"one","title":"First Light","artist":"The Orchard","album":"Sunrise","album_id":"sunrise","track_number":1,"duration":3},{"id":"two","title":"Open Road","artist":"The Orchard","album":"Sunrise","album_id":"sunrise","track_number":2,"duration":3},{"id":"three","title":"After the Rain","artist":"Riverlight","album":"Quiet Hours","album_id":"quiet","track_number":1,"duration":3}],"albums":[{"id":"sunrise","title":"Sunrise","artist":"The Orchard","track_count":2},{"id":"quiet","title":"Quiet Hours","artist":"Riverlight","track_count":1}],"artists":[{"id":"orchard","name":"The Orchard","track_count":2,"album_ids":["sunrise"]},{"id":"river","name":"Riverlight","track_count":1,"album_ids":["quiet"]}],"playlists":[]}"#
        let database = try LibraryStore(path: ":memory:")
        var snapshot = try LibraryCodec.decoder().decode(
          LibrarySnapshot.self, from: Data(fixture.utf8))
        for index in snapshot.tracks.indices {
          snapshot.tracks[index].coverSha = String(
            repeating: snapshot.tracks[index].albumId == "sunrise" ? "a" : "b", count: 64)
        }
        for index in snapshot.albums.indices {
          snapshot.albums[index].coverSha = String(
            repeating: snapshot.albums[index].id == "sunrise" ? "a" : "b", count: 64)
        }
        homeContent[.recent] = HomeContent(albums: snapshot.albums)
        // A chart longer than Home's ten rows, so review screens show its rows
        // and the See all link.
        let chart = (1...12).map { rank in
          #"{"id":"chart-\#(rank)","name":"Chart Song \#(rank)","artist":"The Orchard","artist_id":"orchard","kind":"songs","album":"Sunrise"}"#
        }.joined(separator: ",")
        homeContent[.popular] = try LibraryCodec.decoder().decode(
          HomeContent.self,
          from: Data(
            #"{"albums":[],"feed":{"artists":[],"albums":[],"songs":[\#(chart)],"source":"","unavailable":false,"title":"Popular","state":"ready"}}"#
              .utf8))
        try await database.replace(snapshot)
        let demoDirectory = try FileManager.default.url(
          for: .cachesDirectory, in: .userDomainMask, appropriateFor: nil, create: true
        )
        .appendingPathComponent("PigMusicDemoArtwork-v1")
        try await player.configureSources(directory: demoDirectory.appendingPathComponent("audio"))
        images.cache = try ArtworkCache(directory: demoDirectory)
        artworkNamespace = "synthetic-demo"
        store = database
        foundSearch = FoundSearch(source: nil, library: database)
        person = try LibraryCodec.decoder().decode(
          Person.self,
          from: Data(
            #"{"id":"demo","username":"demo","display_name":"Demo listener","role":"member"}"#.utf8)
        )
        server = "https://demo.invalid"
        try await loadCache(artworkChanged: true)
      } catch { message = error.userMessage }
    }
    private func demoArtwork(_ request: ArtworkRequest) -> Data? {
      let format = UIGraphicsImageRendererFormat()
      format.scale = 1
      let size = CGSize(width: request.width, height: request.width)
      return UIGraphicsImageRenderer(size: size, format: format).pngData { context in
        let warm = request.sha.hasPrefix("a")
        (warm
          ? UIColor(red: 0.9, green: 0.35, blue: 0.3, alpha: 1)
          : UIColor(red: 0.18, green: 0.38, blue: 0.55, alpha: 1)).setFill()
        context.fill(CGRect(origin: .zero, size: size))
        UIColor.white.withAlphaComponent(0.3).setFill()
        context.cgContext.fillEllipse(
          in: CGRect(
            x: size.width * 0.18, y: size.height * 0.18, width: size.width * 0.64,
            height: size.height * 0.64))
        let title = warm ? "SUNRISE" : "QUIET HOURS"
        let font = UIFont.boldSystemFont(ofSize: size.width * 0.07)
        let attributes: [NSAttributedString.Key: Any] = [
          .font: font, .foregroundColor: UIColor.white,
        ]
        let textSize = title.size(withAttributes: attributes)
        title.draw(
          at: CGPoint(x: (size.width - textSize.width) / 2, y: size.height * 0.8),
          withAttributes: attributes)
      }
    }
  #endif
}

enum DeviceCopyMark { case pending, ready, failed }

/// Power and network state for optional background work, from `NWPathMonitor`
/// and Low Power Mode.
@MainActor final class NetworkConditions {
  private let monitor = NWPathMonitor()
  private var path = PrefetchConditions()
  var conditions: PrefetchConditions {
    var value = path
    value.lowPowerMode = ProcessInfo.processInfo.isLowPowerModeEnabled
    return value
  }
  init() {
    monitor.pathUpdateHandler = { [weak self] path in
      let update = PrefetchConditions(
        lowDataMode: path.isConstrained,
        cellular: path.isExpensive || path.usesInterfaceType(.cellular),
        connected: path.status == .satisfied)
      Task { @MainActor in self?.path = update }
    }
    monitor.start(queue: DispatchQueue(label: "au.com.pig.music.network-path", qos: .utility))
  }
  deinit { monitor.cancel() }
}
