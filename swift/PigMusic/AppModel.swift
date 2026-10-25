import CryptoKit
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
  var searchSession: SearchSession?
  var actionTrack: Track?
  /// Music requests the server is still getting, from the requests page summary.
  var serverActiveRequests = 0
  var pendingEdits: [PendingMutation] = []
  @ObservationIgnored private var flushingEdits = false
  var mutationActive = false
  var canMutate: Bool { service != nil && !busy && !syncing && !mutationActive }
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
  @ObservationIgnored private var artworkCache: ArtworkCache?
  @ObservationIgnored private let artworkMemory = NSCache<NSString, UIImage>()
  @ObservationIgnored private var artistCovers: [String: String?] = [:]
  @ObservationIgnored private var catalogueMemory: [String: any Sendable] = [:]
  @ObservationIgnored private(set) var service: NativeService?
  @ObservationIgnored private(set) var store: LibraryStore?
  @ObservationIgnored private var sync: SyncCoordinator?
  @ObservationIgnored private var generation = 0
  /// One refresh at a time; one asked for meanwhile runs after it (RV23).
  @ObservationIgnored private let refreshLoop = RefreshLoop()
  @ObservationIgnored private var restoring = false
  @ObservationIgnored private var liveTask: Task<Void, Never>?
  @ObservationIgnored private var activeScene = true
  @ObservationIgnored private let defaults: UserDefaults
  @ObservationIgnored private let network = NetworkConditions()
  init(defaults: UserDefaults = .standard) {
    self.defaults = defaults
    watch = WatchPhoneController(model: self, defaults: defaults)
    artworkMemory.countLimit = 128
    artworkMemory.totalCostLimit = 24 * 1024 * 1024
  }

  func restore() async {
    guard !restoring, !busy, person == nil else { return }
    restoring = true
    busy = true
    defer {
      restoring = false
      busy = false
    }
    #if DEBUG
      if ProcessInfo.processInfo.arguments.contains("--demo") {
        await loadDemo()
        return
      }
    #endif
    guard let data = defaults.data(forKey: "pig.connection"),
      let connection = try? JSONDecoder().decode(Connection.self, from: data)
    else { return }
    server = connection.server
    do {
      let vault = TokenVault(account: connection.server)
      guard try await vault.read() != nil else {
        defaults.removeObject(forKey: "pig.connection")
        return
      }
      let service = try NativeService(server: connection.server, vault: vault)
      try await attach(connection.person, service: service)
      await refresh()
      startLiveUpdates()
    } catch { message = error.userMessage }
  }
  /// When the app last stopped being active.
  @ObservationIgnored private var inactiveSince: ContinuousClock.Instant?
  func sceneActive(_ active: Bool) async {
    activeScene = active
    liveTask?.cancel()
    liveTask = nil
    if !active, inactiveSince == nil { inactiveSince = .now }
    if active, let since = inactiveSince {
      inactiveSince = nil
      // Connections kept through a break may be dead without the system
      // knowing; the first request on one waited for its timeout.
      let away = ContinuousClock.now - since
      if away >= .seconds(30) {
        service?.renewConnections()
        player.renewConnections()
        DiagnosticsLog.shared.record(
          "network", "new connections after \(Int(away / .seconds(1))) s away")
      }
    }
    if !active {
      await downloads?.suspendPreparation()
      await watch?.suspend()
      return
    }
    downloads?.resumePreparation()
    watch?.resume()
    await refresh()
    startLiveUpdates()
  }
  /// The last library revision the live updates announced, this account.
  @ObservationIgnored private var libraryEvent: String?
  private func startLiveUpdates() {
    guard activeScene, liveTask == nil, let service else { return }
    let stamp = generation
    liveTask = Task { [weak self] in
      var delay = 1
      while !Task.isCancelled {
        guard let self, generation == stamp else { return }
        do {
          let events = try await service.events()
          for try await event in events {
            guard generation == stamp, !Task.isCancelled else { return }
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
            // A request moved on the server: open request lists reload.
            if event.name == "queue" {
              requestsRevision += 1
              await refreshServerRequests()
            }
          }
        } catch NativeFailure.authentication {
          revoked(generation: stamp)
          return
        } catch is CancellationError { return } catch {
          // Cached browsing and manual refresh remain available.
        }
        guard generation == stamp, !Task.isCancelled else { return }
        do { try await Task.sleep(for: .seconds(delay)) } catch { return }
        delay = min(30, delay * 2)
        await refresh()
      }
    }
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
      let stamp = generation
      let covers = Set(tracks.compactMap(\.coverSha))
      for sha in covers {
        guard generation == stamp, !Task.isCancelled else { return }
        _ = await artwork(sha: sha, width: 512)
        _ = await artwork(sha: sha, width: 256)
      }
    } catch { message = error.userMessage }
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
    if preferences.appearance != value.appearance {
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
      let settings = try? await service.preferences(), generation == stamp,
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
      let service = try NativeService(server: server, vault: TokenVault(account: server))
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
        server: origin.absoluteString, vault: TokenVault(account: origin.absoluteString))
      let user = try await service.signIn(
        username: username, password: password, deviceName: UIDevice.current.name)
      server = origin.absoluteString
      try await attach(user, service: service)
      defaults.set(
        try JSONEncoder().encode(Connection(server: server, person: user)), forKey: "pig.connection"
      )
      await refresh()
      startLiveUpdates()
    } catch { message = error.userMessage }
  }
  private func attach(_ user: Person, service: NativeService) async throws {
    generation += 1
    refreshLoop.reset()
    libraryEvent = nil
    let stamp = generation
    let base = try FileManager.default.url(
      for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
    let origin = await service.server
    guard generation == stamp else { throw CancellationError() }
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
    guard generation == stamp else { throw CancellationError() }
    artworkCache = try? ArtworkCache(directory: directory.appendingPathComponent("artwork"))
    artworkNamespace = LibraryNamespace.key(server: origin, personId: user.id)
    artworkMemory.removeAllObjects()
    server = origin.absoluteString
    self.service = service
    store = database
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
    let listeningGeneration = generation
    player.onListen = { [weak self] event in
      Task {
        guard let self, self.generation == listeningGeneration, let service = self.service else {
          return
        }
        let identity = await service.qualifiedHistoryTrackID(event.trackId) ?? event.trackId
        let exists: Bool
        if identity.hasPrefix("mix:") {
          exists = true
        } else {
          exists = (try? await database.track(id: identity)) != nil
        }
        guard exists, self.generation == listeningGeneration else { return }
        try? await database.recordListen(
          ListeningEvent(
            eventId: event.eventId, trackId: identity, playedAt: event.playedAt,
            seconds: event.seconds))
        await self.flushHistory()
      }
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
      }
    }
    player.onSkip = { [weak self] track, heard in
      guard let self, self.radioIDs.contains(track.id), heard < 30, let service = self.service
      else { return }
      Task { try? await service.radioFeedback(trackId: track.id, action: "skip") }
    }
    player.onQueueEnd = { [weak self] in
      guard let self, let current = self.player.queue.current,
        self.radioIDs.contains(current.track.id)
      else { return }
      self.refillRadio(continuing: true)
    }
    player.onCheckpoint = { checkpoint in
      let ids = checkpoint.queue.items.map(\.id)
      let changed = ids != savedOccurrenceIds
      savedOccurrenceIds = ids
      Task {
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
    searchSession = SearchSession(cached: database, online: service) { [weak self] in
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
    let stamp = generation
    let began = ContinuousClock.now
    syncing = true
    defer { if generation == stamp { syncing = false } }
    do {
      let current = try await service.me()
      guard generation == stamp else { return }
      guard current.id == person?.id else {
        detach()
        message = "The server account changed. Please sign in again."
        return
      }
      watch?.confirmAccount()
      let before = try? await store?.revision()
      let artworkBefore = try? await store?.artworkRevision()
      try await sync.refresh()
      guard generation == stamp else { return }
      // Reloading redraws every open page and list, so it waits for a real
      // change. Reconnecting the live updates syncs often with none. Covers
      // reload only when the sync changed one.
      let libraryChanged = (try? await store?.revision()) != before
      if libraryChanged { homeLibraryChanged() }
      if reloadGate.afterSync(libraryChanged: libraryChanged) {
        try await loadCache(
          artworkChanged: (try? await store?.artworkRevision()) != artworkBefore)
      }
      let revisions = await player.cachedRevisions()
      guard generation == stamp else { return }
      // One store query answers every cached and downloaded song at once.
      let records = downloads?.records ?? []
      let known =
        try await store?.sourceRevisions(ids: Set(revisions.keys).union(records.map(\.id))) ?? [:]
      guard generation == stamp else { return }
      var invalid = revisions.contains { id, revision in
        guard let current = known[id] else { return true }
        return current != revision
      }
      if let downloads {
        var allowed: [String: String?] = [:]
        for record in records {
          if let current = known[record.id] {
            allowed.updateValue(current, forKey: record.id)
            if record.state == "ready" && record.track.sourceRevision != current {
              invalid = true
            }
          } else if record.state == "ready" {
            invalid = true
          }
        }
        if invalid { await player.invalidateSources() }
        await downloads.reconcile(allowed)
        guard generation == stamp else { return }
        downloads.resume(automatic: true)
      } else if invalid {
        await player.invalidateSources()
      }
      guard generation == stamp else { return }
      await adoptServerPreferences(service, generation: stamp)
      let editsApplied = await flushEdits()
      guard generation == stamp else { return }
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
      guard generation == stamp else { return }
      DiagnosticsLog.shared.record(
        "sync",
        "refreshed library in \(Int((ContinuousClock.now - began) / .milliseconds(1))) ms"
          + (invalid ? "; cached playback invalidated" : ""))
      message =
        invalid ? "Playback stopped because the server changed or removed cached music." : nil
    } catch NativeFailure.authentication {
      guard generation == stamp else { return }
      revoked(generation: stamp)
    } catch {
      guard generation == stamp else { return }
      // The cached library carries on quietly; the next foreground or pull retries.
      DiagnosticsLog.shared.record("sync", "refresh failed: \(error.localizedDescription)")
      message = nil
    }
    // A category chosen during this sync, changed or failed, loads now;
    // `syncing` stays set until this finishes, so a later choice joins it.
    while generation == stamp, reloadGate.takePending() {
      do { try await loadCache() } catch { message = error.userMessage }
    }
  }
  /// Reloads the lists from the local library. `artworkChanged` also redraws
  /// every cover; only an account change or a sync that changed covers needs it.
  private func loadCache(artworkChanged: Bool = false) async throws {
    guard let store else { return }
    let stamp = generation
    let tracks = try await store.tracks(sort: songSort)
    let albums = try await store.albums(category: albumCategory, sort: albumSort)
    let artists = try await store.artists(sort: artistSort)
    let playlists = try await store.playlists()
    let count = try await store.count(.tracks)
    let categoryCounts = try await store.albumCategoryCounts()
    let artistCount = try await store.ownArtistCount()
    guard generation == stamp else { return }
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
      artworkRefresh += 1
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
  /// Library changes can change recommendations; the next Home visit refreshes them.
  func homeLibraryChanged() {
    homeLoadedAt[.recommended] = nil
    homeLoadedAt[.mixes] = nil
  }
  /// Home's catalogue albums, fetched ahead one at a time with their
  /// recommended edition and its cover, so a tap opens like a library album.
  private func prefetchAlbums(_ albums: [DiscoveryItem]) {
    // Optional work: not on cellular, Low Data Mode or Low Power Mode.
    guard network.conditions.allowsPrefetch else { return }
    let stamp = generation
    Task(priority: .utility) { [weak self] in
      for album in albums.prefix(20) {
        guard let self, generation == stamp, !Task.isCancelled,
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
    let stamp = generation
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
        guard generation == stamp, !Task.isCancelled else {
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
    guard generation == stamp else { return }
    homeLoading.subtract(sections)
    // Like the web: a chart still being prepared is asked for again, a few times.
    if HomeSection.allCases.contains(where: {
      homeContent[$0]?.feed?.state == .preparing || homeContent[$0]?.mixesPreparing == true
    }),
      homePreparingRetries < 6
    {
      homePreparingRetries += 1
      try? await Task.sleep(for: .seconds(5 * homePreparingRetries))
      guard generation == stamp, !Task.isCancelled else { return }
      await refreshHome()
    } else {
      homePreparingRetries = 0
    }
  }

  func readCatalogue<T: Sendable>(
    _ fetch: @Sendable (NativeService) async throws -> T
  ) async throws -> T {
    guard let service else {
      throw NativeFailure.unavailable("Connect to your server to browse the catalogue.")
    }
    let stamp = generation
    do {
      let value = try await fetch(service)
      try Task.checkCancellation()
      guard generation == stamp else { throw CancellationError() }
      return value
    } catch NativeFailure.authentication {
      revoked(generation: stamp)
      throw NativeFailure.authentication
    }
  }

  func performPersonal<T: Sendable>(
    _ operation: @Sendable (NativeService) async throws -> T
  ) async throws -> T {
    guard canMutate, let service else {
      throw NativeFailure.unavailable("Connect to your server and wait for sync to finish.")
    }
    let stamp = generation
    mutationActive = true
    defer { if generation == stamp { mutationActive = false } }
    do {
      let result = try await operation(service)
      try Task.checkCancellation()
      guard generation == stamp else { throw CancellationError() }
      await refresh(afterMutation: true)
      guard generation == stamp else { throw CancellationError() }
      return result
    } catch NativeFailure.authentication {
      revoked(generation: stamp)
      throw NativeFailure.authentication
    }
  }

  @discardableResult func saveEdit(_ mutation: OfflineMutation) async throws -> Bool {
    guard let store, person != nil else { throw NativeFailure.authentication }
    let stamp = generation
    try await store.enqueueMutation(mutation)
    guard generation == stamp else { throw CancellationError() }
    pendingEdits = try await store.pendingMutations()
    let applied = await flushEdits()
    guard generation == stamp else { throw CancellationError() }
    if applied { await refresh() }
    return !pendingEdits.contains { $0.id == mutation.id }
  }
  @discardableResult func flushEdits() async -> Bool {
    guard !flushingEdits, let store, let service, person != nil else { return false }
    flushingEdits = true
    let stamp = generation
    defer { flushingEdits = false }
    var applied = false
    do {
      // Edits to one playlist (or one song's favourite) stay in order: once one
      // conflicts or is rejected, later edits to the same thing wait for review.
      // Edits to anything else still go through. A network failure stops all.
      var blocked: Set<String> = []
      sending: for item in try await store.pendingMutations() {
        guard generation == stamp else { return applied }
        let scope = Self.editScope(item.mutation)
        guard item.state == "queued", !blocked.contains(scope) else {
          blocked.insert(scope)
          continue
        }
        do {
          let receipt = try await service.applyMutation(item.mutation)
          guard receipt.applied, receipt.operationId == item.id, generation == stamp else {
            return applied
          }
          try await store.acknowledgeMutation(item.id)
          applied = true
        } catch NativeFailure.authentication {
          revoked(generation: stamp)
          return applied
        } catch NativeFailure.changed {
          var changed = item
          changed.state = "conflict"
          changed.message =
            "This playlist changed on the server. Discard this edit or review the current playlist before reapplying it."
          try await store.updateMutation(changed)
          blocked.insert(scope)
        } catch is MutationRejection {
          var rejected = item
          rejected.state = "rejected"
          rejected.message = "The server could not apply this edit. Review its song or playlist."
          try await store.updateMutation(rejected)
          blocked.insert(scope)
        } catch {
          break sending
        }
      }
      if generation == stamp { pendingEdits = try await store.pendingMutations() }
    } catch { if generation == stamp { message = error.userMessage } }
    return applied
  }
  /// What an edit changes, for keeping dependent edits in order.
  private static func editScope(_ mutation: OfflineMutation) -> String {
    if let playlist = mutation.playlistId { return "playlist:" + playlist }
    if let track = mutation.track { return mutation.action + ":" + track.id }
    return mutation.action + ":" + (mutation.name ?? "")
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
    let stamp = generation
    let database = store
    do {
      let result = try await readCatalogue(load)
      guard generation == stamp else { throw CancellationError() }
      remember(result, key: key)
      try? await database?.cache(result, key: "catalogue:" + key)
      return result
    } catch NativeFailure.authentication {
      throw NativeFailure.authentication
    } catch is CancellationError { throw CancellationError() } catch {
      guard generation == stamp else { throw CancellationError() }
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
    let stamp = generation
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
      guard generation == stamp else { throw CancellationError() }
      remember(result, key: key)
      return result
    } catch NativeFailure.authentication {
      throw NativeFailure.authentication
    } catch is CancellationError { throw CancellationError() } catch {
      guard generation == stamp else { throw CancellationError() }
      if let saved = try? await database.cached("catalogue:" + key, as: T.self) { return saved }
      throw error
    }
  }
  /// The last catalogue answer for a key, so a revisited page draws at once.
  /// Pages still refresh it through `cachedCatalogue`.
  func peekCatalogue<T: Sendable>(key: String, as type: T.Type = T.self) -> T? {
    catalogueMemory[key] as? T
  }
  /// The memory answer, else the one saved on this device from an earlier session.
  func storedCatalogue<T: Codable & Sendable>(key: String, as type: T.Type = T.self) async -> T? {
    if let value = peekCatalogue(key: key, as: T.self) { return value }
    let stamp = generation
    guard let saved = try? await store?.cached("catalogue:" + key, as: T.self),
      generation == stamp
    else { return nil }
    remember(saved, key: key)
    return saved
  }
  private func remember(_ value: any Sendable, key: String) {
    if catalogueMemory.count > 400 { catalogueMemory.removeAll() }
    catalogueMemory[key] = value
  }
  /// Artwork addressed by stored hash, or a server alias for catalogue items.
  enum ArtworkSource: Hashable, Sendable {
    case stored(String)
    case release(String)
    case releaseGroup(String)
    case artist(String)
  }
  func artwork(_ source: ArtworkSource?, width: Int = 256) async -> UIImage? {
    switch source {
    case .stored(let sha): return await artwork(sha: sha, width: width)
    case .release(let id):
      return await catalogueImage("release", id, width) {
        try await $0.catalogueArtwork(kind: .release, id: id, request: $1)
      }
    case .releaseGroup(let id):
      return await catalogueImage("release-group", id, width) {
        try await $0.catalogueArtwork(kind: .releaseGroup, id: id, request: $1)
      }
    case .artist(let id):
      return await catalogueImage("artist", id, width) {
        try await $0.artistArtwork(id: id, request: $1)
      }
    case nil: return nil
    }
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
    let sha = SHA256.hash(data: Data(("catalogue-\(kind):" + id.lowercased()).utf8)).map {
      String(format: "%02x", $0)
    }.joined()
    guard let request = try? ArtworkRequest(sha: sha, width: width) else { return nil }
    return artworkMemory.object(forKey: request.filename as NSString)
  }
  private func catalogueImage(
    _ kind: String, _ id: String, _ width: Int,
    fetch: @escaping @Sendable (NativeService, ArtworkRequest) async throws -> Data
  ) async -> UIImage? {
    guard let service, let cache = artworkCache, UUID(uuidString: id) != nil else { return nil }
    let stamp = generation
    // Catalogue artwork has no content hash on the client: key it by its alias.
    let sha = SHA256.hash(data: Data(("catalogue-\(kind):" + id.lowercased()).utf8)).map {
      String(format: "%02x", $0)
    }.joined()
    guard let request = try? ArtworkRequest(sha: sha, width: width) else { return nil }
    let key = request.filename as NSString
    if let image = artworkMemory.object(forKey: key) { return image }
    do {
      let data = try await cache.data(for: request) { try await fetch(service, $0) }
      let decoded = try await Task.detached(priority: .utility) {
        try ArtworkCache.thumbnail(data, width: width)
      }.value
      guard generation == stamp else { return nil }
      let image = UIImage(cgImage: decoded)
      artworkMemory.setObject(image, forKey: key, cost: decoded.bytesPerRow * decoded.height)
      return image
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
      let onWatch =
        record.checksum != nil && watch?.receipt?.ready[trackId] == record.checksum
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
      $0.state != "failed" && ($0.checksum == nil || receipt?.ready[$0.id] != $0.checksum)
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
    let stamp = generation
    loadingMore = true
    defer { if generation == stamp { loadingMore = false } }
    do {
      switch collection {
      case .tracks:
        let next = try await store.tracks(offset: songs.count, sort: songSort)
        guard generation == stamp else { return }
        songs += next
      case .albums:
        let next = try await store.albums(
          category: albumCategory, offset: albums.count, sort: albumSort)
        guard generation == stamp else { return }
        albums += next
      case .artists:
        let next = try await store.artists(offset: artists.count, sort: artistSort)
        guard generation == stamp else { return }
        artists += next
      case .playlists: break
      }
    } catch { message = error.userMessage }
  }
  func albumTracks(_ album: Album) async -> [Track] {
    (try? await store?.tracks(albumId: album.id)) ?? []
  }
  func artwork(sha: String?, width: Int = 256) async -> UIImage? {
    guard let sha, let request = try? ArtworkRequest(sha: sha, width: width),
      let cache = artworkCache
    else { return nil }
    let service = self.service
    var demoData: Data?
    #if DEBUG
      if ProcessInfo.processInfo.arguments.contains("--demo") {
        demoData = demoArtwork(request)
      }
    #endif
    guard service != nil || demoData != nil else { return nil }
    let stamp = generation
    let key = request.filename as NSString
    if let image = artworkMemory.object(forKey: key) { return image }
    do {
      let synthetic = demoData
      let data = try await cache.data(for: request) {
        if let synthetic { return synthetic }
        guard let service else { throw ArtworkFailure.unavailable }
        return try await service.artwork($0)
      }
      try Task.checkCancellation()
      let decoded = try await Task.detached(priority: .utility) {
        try ArtworkCache.thumbnail(data, width: width)
      }.value
      try Task.checkCancellation()
      guard generation == stamp else { return nil }
      let image = UIImage(cgImage: decoded)
      artworkMemory.setObject(image, forKey: key, cost: decoded.bytesPerRow * decoded.height)
      return image
    } catch NativeFailure.authentication {
      revoked(generation: stamp)
      return nil
    } catch { return nil }
  }
  func flushHistory() async {
    guard !flushingHistory, let database = store, let service else { return }
    let stamp = generation
    flushingHistory = true
    defer { flushingHistory = false }
    do {
      let pending = try await database.pendingListens()
      var valid: [ListeningEvent] = []
      var discarded: [String] = []
      let now = Int(Date().timeIntervalSince1970)
      for event in pending {
        let exists: Bool
        if event.trackId.hasPrefix("mix:") {
          exists = true
        } else {
          exists = try await database.track(id: event.trackId) != nil
        }
        if event.playedAt < now - 30 * 86400 || event.playedAt > now + 300 || !exists {
          discarded.append(event.id)
        } else {
          valid.append(event)
        }
      }
      try await database.acknowledgeListens(discarded)
      guard generation == stamp else { return }
      try await service.recordListens(valid)
      guard generation == stamp else { return }
      try await database.acknowledgeListens(valid.map(\.id))
    } catch NativeFailure.authentication { revoked(generation: stamp) } catch {
      // Durable events remain queued until the next confirmed connection.
    }
  }
  func shuffleLibrary() async {
    guard let store else { return }
    let stamp = generation
    do {
      let tracks = try await store.tracks(limit: max(songCount, 1))
      guard generation == stamp else { return }
      play(tracks.shuffled())
    } catch { message = error.userMessage }
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
            await self?.artwork(track.artworkSource, width: 512)
          }, resolve: { _ in url }
        )
        return
      }
    #endif
    guard !busy || restored != nil, let service else { return }
    let archive = downloads?.archive
    let stamp = generation
    player.play(
      tracks, startingAt: index, autoplay: restored == nil, restored: restored,
      artwork: { [weak self] track in
        await self?.artwork(track.artworkSource, width: 512)
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
  func signOut() async {
    guard !busy, !mutationActive else { return }
    guard let service else {
      detach()
      return
    }
    busy = true
    defer { busy = false }
    do {
      try await service.signOut()
      detach()
    } catch { message = error.userMessage }
  }
  private func detach() {
    watch?.disconnect()
    generation += 1
    refreshLoop.reset()
    libraryEvent = nil
    liveTask?.cancel()
    liveTask = nil
    if let downloads { Task { await downloads.clear() } }
    downloads = nil
    searchSession?.clear()
    searchSession = nil
    actionTrack = nil
    mutationActive = false
    pendingEdits = []
    player.clearAccount()
    preferences = AccountPreferences()
    person = nil
    service = nil
    store = nil
    sync = nil
    if let cache = artworkCache { Task { await cache.cancelPending() } }
    artworkCache = nil
    artworkNamespace = ""
    artworkMemory.removeAllObjects()
    artistCovers = [:]
    catalogueMemory = [:]
    homeContent = [:]
    homeLoadedAt = [:]
    homeErrors = [:]
    homeLoading = []
    songs = []
    albums = []
    artists = []
    playlists = []
    songCount = 0
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
    guard generation == stamp else { return }
    DiagnosticsLog.shared.record("account", "session revoked or expired")
    detach()
    message = "This device session was revoked or expired. Please sign in again."
  }
  #if DEBUG
    private func loadDemo() async {
      do {
        let fixture =
          #"{"revision":"demo","tracks":[{"id":"one","title":"First Light","artist":"The Orchard","album":"Sunrise","album_id":"sunrise","track_number":1,"duration":3},{"id":"two","title":"Open Road","artist":"The Orchard","album":"Sunrise","album_id":"sunrise","track_number":2,"duration":3},{"id":"three","title":"After the Rain","artist":"Riverlight","album":"Quiet Hours","album_id":"quiet","track_number":1,"duration":3}],"albums":[{"id":"sunrise","title":"Sunrise","artist":"The Orchard","track_count":2},{"id":"quiet","title":"Quiet Hours","artist":"Riverlight","track_count":1}],"artists":[{"id":"orchard","name":"The Orchard","track_count":2},{"id":"river","name":"Riverlight","track_count":1}],"playlists":[]}"#
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
        try await database.replace(snapshot)
        let demoDirectory = try FileManager.default.url(
          for: .cachesDirectory, in: .userDomainMask, appropriateFor: nil, create: true
        )
        .appendingPathComponent("PigMusicDemoArtwork-v1")
        try await player.configureSources(directory: demoDirectory.appendingPathComponent("audio"))
        artworkCache = try ArtworkCache(directory: demoDirectory)
        artworkNamespace = "synthetic-demo"
        store = database
        searchSession = SearchSession(cached: database)
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
