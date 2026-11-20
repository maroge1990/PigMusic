import AVFoundation
import Combine
import CryptoKit
import Foundation
import MediaPlayer
import Observation
import PigCompanion
import PigDomain
import PigPlayback
import UIKit
import WatchKit

@MainActor @Observable final class WatchModel {
  var manifest = CompanionManifest(context: CompanionContext(revision: 0, account: nil), songs: [])
  var receipt: CompanionReceipt?
  var link: CompanionLink?
  /// Neutral notices ("Sent to iPhone"), shown in grey and cleared after a
  /// few seconds (#252). Playback errors are `playback.error`.
  let notices = ActionNotices()
  /// Why the Watch's music couldn't be opened; the home shows it in place
  /// of an empty library (#252).
  var loadError: String?
  /// The saved library was damaged and started empty: a grey notice until
  /// the iPhone's next copy arrives.
  var recovery = WatchRecovery(damagedFiles: [])
  /// Songs whose removal is on its way to the iPhone: left out of lists and counts.
  var removing: Set<String> = []
  /// Songs the Watch turned away this session, and why (#252).
  var problems: [String: WatchSaveProblem] = [:]
  /// Songs whose removal has been committed (the Undo ran out), noted at
  /// once so a queue still being built leaves them out.
  @ObservationIgnored private var committedRemovals = WatchRemovalMarks()
  /// Removals waiting for their Undo (D-79, #183).
  let undo = UndoQueue()
  static func undoKey(_ id: String) -> String { "watch-song:" + id }
  var queue: [PlaybackOccurrence] = []
  /// False while the Watch screen is off or the app is in the background.
  @ObservationIgnored var sceneActive = true {
    didSet { if sceneActive, !oldValue { playback = latest } }
  }
  /// Every engine report, including the ones not published to the screen.
  @ObservationIgnored private var latest = QueuePlaybackSnapshot()
  /// When the phone last delivered this Watch's library.
  var lastSynced = UserDefaults.standard.object(forKey: "watch.synced") as? Date {
    didSet { UserDefaults.standard.set(lastSynced, forKey: "watch.synced") }
  }
  /// When watchOS gave the app time and what it did with it (#219). Saved,
  /// so a background launch adds to the same record.
  @ObservationIgnored private(set) var wakeLog =
    UserDefaults.standard.data(forKey: "watch.wake-log")
    .flatMap { try? JSONDecoder().decode(WatchWakeLog.self, from: $0) } ?? WatchWakeLog()
  /// Song files that reached the inbox since this launch.
  @ObservationIgnored private var receivedThisRun = 0
  private func noteWake(_ change: (inout WatchWakeLog) -> Void) {
    var next = wakeLog
    change(&next)
    guard next != wakeLog else { return }
    wakeLog = next
    if let data = try? JSONEncoder().encode(next) {
      UserDefaults.standard.set(data, forKey: "watch.wake-log")
    }
  }
  /// Whether the app is on screen, as watchOS sees it.
  private var appActive: Bool { WKApplication.shared().applicationState == .active }
  /// How this launch began, noted once the app is running (#219).
  private func noteLaunch() {
    let background = WKApplication.shared().applicationState == .background
    noteWake { $0.launched(background: background) }
  }
  /// The person opened the app.
  func opened() { noteWake { $0.opened() } }
  /// The library's songs, without those being removed.
  var songs: [CompanionSong] { manifest.songs.filter { !removing.contains($0.id) } }
  /// Whether the iPhone is in reach; nil when the link can't tell (demo, not activated).
  var iPhoneReachable: Bool? {
    guard let link, link.activated else { return nil }
    return link.reachable
  }
  /// Every count and mark on the Watch reads this (#252).
  var status: WatchLibraryStatus {
    WatchLibraryStatus(
      manifest: manifest, receipt: receipt, removing: removing, problems: problems,
      iPhoneReachable: iPhoneReachable)
  }
  /// On the Watch and verified: plays with no iPhone or network.
  func isReady(_ song: CompanionSong) -> Bool { song.isReady(in: receipt) }
  var playback = QueuePlaybackSnapshot()
  /// What the iPhone plays, for the home's iPhone row (D-104). Kept across
  /// launches: the phone sends it only when the song or play/pause changes.
  var phoneNowPlaying: PhoneNowPlaying? =
    UserDefaults.standard.data(forKey: "watch.phone-now-playing").flatMap(PhoneNowPlaying.decode)
  {
    didSet {
      phoneCover = phoneNowPlaying?.cover.flatMap(UIImage.init(data:))
      UserDefaults.standard.set(
        phoneNowPlaying.flatMap { try? JSONEncoder().encode($0) }, forKey: "watch.phone-now-playing")
    }
  }
  /// The iPhone song's cover, decoded once.
  private(set) var phoneCover: UIImage?
  /// Contexts announced so far, noted before any await (APP1).
  @ObservationIgnored private var contextFence = CompanionContextFence()
  var repeatMode: PlaybackRepeat =
    PlaybackRepeat(rawValue: UserDefaults.standard.string(forKey: "watch.repeat") ?? "off") ?? .off
  {
    didSet {
      UserDefaults.standard.set(repeatMode.rawValue, forKey: "watch.repeat")
      engine?.setRepeatMode(repeatMode)
    }
  }
  /// Artists by album artist, each with their albums on the Watch.
  var artists: [WatchArtist] { WatchLibraryIndex.artists(albums) }
  /// A to Z ignoring case and a leading "The", like artists (#252).
  var albums: [WatchAlbum] { WatchLibraryIndex.albums(songs) }
  /// Every song by title (#255: was arrival order).
  var songIds: [String] { WatchLibraryIndex.songs(songs) }
  func finishBackgroundDelivery() async {
    await link?.finishBackgroundDelivery()
    await reload()
  }
  /// watchOS woke the app for the iPhone link (#219): noted, with whether
  /// anything was waiting for it.
  func linkBackgroundTask() async {
    let pending = link?.contentPending == true
    let received = receivedThisRun
    await finishBackgroundDelivery()
    let delivered = receivedThisRun > received
    noteWake { $0.linkTask(pending: pending || delivered) }
    planRefresh()
  }
  /// The Watch's scheduled refresh, which takes songs the iPhone handed to
  /// the link while the app wasn't running (#219).
  static let refreshIdentifier = "au.com.pig.music.watch.refresh"
  /// Asks watchOS to wake the app while songs wait, only as
  /// `WatchRefreshPlan` allows (stops after 2 h without a new song until the
  /// app is opened, D-113), with one refresh pending at a time.
  func planRefresh(now: Date = Date()) {
    guard link != nil else { return }
    let plan = WatchRefreshPlan.next(
      now: now, waiting: status.waiting, lastAccepted: wakeLog.lastAccepted,
      lastOpened: wakeLog.lastOpened)
    guard let date = WatchRefreshPlan.request(plan, pending: wakeLog.pendingRefresh, now: now)
    else { return }
    noteWake { $0.refreshAsked(for: date, at: now) }
    // @Sendable: watchOS may call it from another queue.
    WKApplication.shared().scheduleBackgroundRefresh(
      withPreferredDate: date, userInfo: Self.refreshIdentifier as NSString
    ) { @Sendable [weak self] error in
      guard let error else { return }
      let text = error.localizedDescription
      Task { @MainActor in self?.refreshRefused(text) }
    }
  }
  private func refreshRefused(_ text: String) {
    DiagnosticsLog.shared.record("link", "background refresh not scheduled: \(text)")
    noteWake { $0.refreshRefused() }
  }
  /// watchOS ran the scheduled refresh (#219): take what the link has
  /// waiting, as its own background task does, then ask the plan again.
  func backgroundRefresh() async {
    noteWake { $0.refreshRan() }
    link?.reconnect()
    await finishBackgroundDelivery()
    planRefresh()
  }
  func retry() async {
    guard queue.indices.contains(playback.index) else { return }
    await play(queue.map { $0.track.id }, at: playback.index)
  }
  var crossfade = UserDefaults.standard.object(forKey: "watch.crossfade") as? Bool ?? true {
    didSet { UserDefaults.standard.set(crossfade, forKey: "watch.crossfade") }
  }
  var volume: Double = 0.7 { didSet { output?.volume = Float(volume) } }
  @ObservationIgnored private var archive: CompanionArchive?
  @ObservationIgnored private var output: EngineQueueOutput?
  @ObservationIgnored private var engine: RealtimeQueueSession?
  @ObservationIgnored private var notifications: [AnyCancellable] = []
  @ObservationIgnored private var playbackGeneration = 0
  @ObservationIgnored private var intent = PlaybackIntent()
  @ObservationIgnored private var routeRecovery: Task<Void, Never>?
  @ObservationIgnored private var pendingRestore: Checkpoint?
  @ObservationIgnored private var restoredCheckpoint = false
  @ObservationIgnored private var lastCheckpoint: TimeInterval = 0
  @ObservationIgnored private var checkpointIndex = -1
  @ObservationIgnored private var listenOccurrence: UUID?
  @ObservationIgnored private var listenSeconds: Double = 0
  @ObservationIgnored private var listenReported = false
  @ObservationIgnored private var listenTick = ProcessInfo.processInfo.systemUptime
  private func measureListen(_ snapshot: QueuePlaybackSnapshot) {
    let now = ProcessInfo.processInfo.systemUptime
    let delta = min(1, max(0, now - listenTick))
    listenTick = now
    guard let id = snapshot.occurrenceID, queue.indices.contains(snapshot.index) else { return }
    if listenOccurrence != id {
      listenOccurrence = id
      listenSeconds = 0
      listenReported = false
      return
    }
    if snapshot.state == .playing { listenSeconds += delta }
    if listenSeconds >= 30, !listenReported, let archive {
      listenReported = true
      let event = ListeningEvent(
        eventId: id.uuidString, trackId: queue[snapshot.index].track.id,
        playedAt: Int(Date().timeIntervalSince1970), seconds: 30)
      Task {
        do {
          try await archive.recordListen(event)
          await flushListens()
        } catch { notices.post("Couldn't save listening history: " + error.localizedDescription) }
      }
    }
  }
  private func flushRequests() async {
    guard let archive else { return }
    let requests = await archive.pendingRequests()
    let account = await archive.activeContext().account
    for action in ["download", "remove"] {
      let ids = requests.filter { $0.value == action }.keys.sorted()
      for offset in stride(from: 0, to: ids.count, by: 100) {
        let command = CompanionCommand(
          action, account: account, songIds: Array(ids.dropFirst(offset).prefix(100)))
        if let data = try? JSONEncoder().encode(command) { link?.message(data) }
      }
    }
  }
  /// Listens handed to the phone and not acknowledged yet go once (#97).
  @ObservationIgnored private var listenOutbox = ListenOutbox()
  private func flushListens() async {
    guard let archive else { return }
    let pending = await archive.pendingListens()
    for event in listenOutbox.take(pending) {
      let command = CompanionCommand("listen", account: manifest.context.account, listen: event)
      if let data = try? JSONEncoder().encode(command) { link?.message(data) }
    }
  }
  /// Sends the Watch's lifecycle log to the phone, for its diagnostics export.
  func sendDiagnostics() {
    let info = Bundle.main.infoDictionary
    let version =
      "\(info?["CFBundleShortVersionString"] as? String ?? "?") (\(info?["CFBundleVersion"] as? String ?? "?"))"
    // When watchOS woke the Watch app, above its log (#219).
    let wakes = "Background wakes:\n" + wakeLog.summary() + "\n\n"
    let payload = CompanionDiagnostics(
      version: version,
      text: wakes
        + DiagnosticsLog.shared.text(
          maximumBytes: CompanionDiagnostics.maximumTextBytes - wakes.utf8.count),
      endedUnexpectedly: DiagnosticsLog.shared.previousRunEndedUnexpectedly)
    if let data = try? JSONEncoder().encode(payload) { link?.message(data) }
  }
  /// The diagnostics a person sent from Storage: says it went (#252).
  func sendDiagnosticsFromStorage() {
    sendDiagnostics()
    notices.post(link?.reachable == true ? "Sent to iPhone" : "Waiting to send")
  }
  /// The Watch's own folder, kept out of backups.
  private static func baseDirectory() throws -> URL {
    let base = try FileManager.default.url(
      for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true
    ).appendingPathComponent(Self.demo ? "PigMusicWatchDemo" : "PigMusicWatch")
    var values = URLResourceValues()
    values.isExcludedFromBackup = true
    try FileManager.default.createDirectory(at: base, withIntermediateDirectories: true)
    var excluded = base
    try excluded.setResourceValues(values)
    return base
  }
  /// Opens the music archive; a failure shows on the home as an error, not
  /// as an empty library (#252).
  private func openArchive() {
    guard archive == nil else { return }
    do {
      let opened = try CompanionArchive(
        directory: try Self.baseDirectory().appendingPathComponent("Music"))
      archive = opened
      loadError = nil
      recovery = WatchRecovery(damagedFiles: opened.damagedFiles)
      if !opened.damagedFiles.isEmpty {
        DiagnosticsLog.shared.record(
          "sync", "saved library damaged: \(opened.damagedFiles.joined(separator: ", "))")
      }
    } catch {
      DiagnosticsLog.shared.record("sync", "archive didn't open: \(error.localizedDescription)")
      loadError = error.localizedDescription
    }
  }
  /// Try again after the music couldn't be opened.
  func reopen() async {
    openArchive()
    await reload()
  }
  @ObservationIgnored private var sentCrashNotice = false
  init() {
    phoneCover = phoneNowPlaying?.cover.flatMap(UIImage.init(data:))
    let info = Bundle.main.infoDictionary
    DiagnosticsLog.shared.launched(
      "Apple Watch",
      version:
        "\(info?["CFBundleShortVersionString"] as? String ?? "?") (\(info?["CFBundleVersion"] as? String ?? "?"))"
    )
    Task { [weak self] in self?.noteLaunch() }
    openArchive()
    do {
      // The demo library never talks to the phone, so a real one can't replace it.
      if !Self.demo {
        link = try CompanionLink(inbox: try Self.baseDirectory().appendingPathComponent("Inbox"))
      }
      link?.onContext = { [weak self] context in await self?.contextReceived(context) }
      link?.onFile = { [weak self] file, data in await self?.fileReceived(file, data: data) }
      link?.onReceived = { [weak self] data in
        guard let envelope = try? JSONDecoder().decode(CompanionEnvelope.self, from: data),
          envelope.kind == "audio"
        else { return }
        self?.receivedThisRun += 1
        self?.noteWake { $0.received() }
      }
      link?.onActivated = { [weak self] in
        // After an unexpected end, the phone gets the log without being asked.
        if let self, DiagnosticsLog.shared.previousRunEndedUnexpectedly, !self.sentCrashNotice {
          self.sentCrashNotice = true
          self.sendDiagnostics()
        }
        await self?.reload()
        await self?.acknowledge()
        // A reconnect: listens not acknowledged yet go again.
        self?.listenOutbox.reset()
        await self?.flushListens()
      }
      link?.onMessage = { [weak self] data in
        if let state = PhoneNowPlaying.decode(data) {
          if let self, state.supersedes(self.phoneNowPlaying) { self.phoneNowPlaying = state }
          return
        }
        if let request = CompanionDiagnostics.decode(data), request.text == nil {
          self?.sendDiagnostics()
          return
        }
        if let reply = try? JSONDecoder().decode(CompanionReply.self, from: data) {
          if let id = reply.listenId, let archive = self?.archive {
            try? await archive.acknowledgeListen(id, account: reply.account)
            await self?.flushListens()
          }
          if let message = reply.message { self?.notices.post(message) }
        }
      }
      try makeEngine()
      notifications += AudioSessionEvent.observe { [weak self] event in
        guard let self else { return }
        switch event {
        case .interruptionBegan:
          DiagnosticsLog.shared.record("audio", "interruption began")
          intent.beginInterruption()
          engine?.pause()
        case .interruptionEnded(let resume):
          DiagnosticsLog.shared.record("audio", "interruption ended, resume=\(resume)")
          intent.endInterruption(shouldResume: resume)
          if intent.canPlay { activateAndResume() }
        case .routeChanged(let removed):
          if removed { pause() }
          recoverRoute()
        case .engineConfigurationChanged:
          recoverRoute()
        case .mediaServicesReset:
          Task { await self.rebuildAudio() }
        }
      }
      // @Sendable keeps each handler off the main actor: the system may call it
      // from another queue, and an isolated handler would assert before its Task.
      MPRemoteCommandCenter.shared().playCommand.addTarget { @Sendable [weak self] _ in
        Task { @MainActor in self?.activateAndResume() }
        return .success
      }
      MPRemoteCommandCenter.shared().pauseCommand.addTarget { @Sendable [weak self] _ in
        Task { @MainActor in self?.pause() }
        return .success
      }
      // Headphone presses and the system Now Playing controls.
      MPRemoteCommandCenter.shared().togglePlayPauseCommand.addTarget { @Sendable [weak self] _ in
        Task { @MainActor in self?.toggle() }
        return .success
      }
      MPRemoteCommandCenter.shared().changePlaybackPositionCommand.addTarget { @Sendable [weak self] event in
        guard let event = event as? MPChangePlaybackPositionCommandEvent else {
          return .commandFailed
        }
        let position = event.positionTime
        Task { @MainActor in self?.seek(position) }
        return .success
      }
      MPRemoteCommandCenter.shared().nextTrackCommand.addTarget { @Sendable [weak self] _ in
        Task { @MainActor in self?.next() }
        return .success
      }
      MPRemoteCommandCenter.shared().previousTrackCommand.addTarget { @Sendable [weak self] _ in
        Task { @MainActor in self?.previous() }
        return .success
      }
    } catch { notices.post(error.localizedDescription) }
  }
  #if DEBUG
    /// Headless UI review (`--demo`): its own archive with three generated
    /// songs, stored through the archive's usual checks, and no phone link.
    static let demo = ProcessInfo.processInfo.arguments.contains("--demo")
    func loadDemo() async {
      guard let archive else { return }
      // `--demo-big`: a library the size of a real one, for design review
      // (scripts/simulator_showcase.py). Every sixth song has not arrived.
      let big = ProcessInfo.processInfo.arguments.contains("--demo-big")
      // Its own revision, so it replaces a small demo left in the archive.
      let context = CompanionContext(revision: big ? 2 : 1, account: "demo")
      // (title, artist, album, album artist)
      var entries = [
        ("First Light", "The Orchard", "Sunrise", "The Orchard"),
        (
          "A Very Long Song Title That Keeps On Going Past the Edge", "The Orchard", "Sunrise",
          "The Orchard"
        ),
        (
          "After the Rain", "Riverlight with a Long Featured Artist Name", "Sunrise", "The Orchard"
        ),
      ]
      if big {
        let albums = [
          ("Rumours", "Fleetwood Mac", 11), ("Tusk", "Fleetwood Mac", 8),
          ("Back to Black", "Amy Winehouse", 10), ("Blue", "Joni Mitchell", 10),
          ("The Miseducation of Lauryn Hill", "Lauryn Hill", 9),
          ("Random Access Memories", "Daft Punk", 9), ("Currents", "Tame Impala", 8),
          ("Hamilton (Original Broadway Cast Recording)", "Original Broadway Cast", 12),
          ("abbey road", "the beatles", 7),
        ]
        entries = albums.flatMap { album, artist, count in
          (1...count).map { ("\(album.split(separator: " ")[0]) Song \($0)", artist, album, artist) }
        }
        entries[3].0 = "A Very Long Song Title That Keeps On Going Past the Edge of the Screen"
      }
      do {
        var songs: [CompanionSong] = []
        var files: [(URL, CompanionFile)] = []
        for (number, (title, artist, album, albumArtist)) in entries.enumerated() {
          var track = Track(
            id: "demo-\(number)", title: title, artist: artist, album: album,
            duration: big ? Double(150 + (number * 37) % 200) : 30)
          track.albumArtist = albumArtist
          track.trackNumber = number + 1
          if big && number % 6 == 5 {
            // A well-formed checksum whose audio never arrives.
            let absent = String(repeating: "0", count: 60) + String(format: "%04x", number)
            songs.append(CompanionSong(track: track, checksum: absent, bytes: 4_000_000))
            continue
          }
          let file = FileManager.default.temporaryDirectory.appendingPathComponent(
            "demo-\(number).m4a")
          try? FileManager.default.removeItem(at: file)
          try Self.writeTone(
            to: file, frequency: big ? 220 + Double(number) * 7 : 220 * Double(number + 2),
            seconds: big ? 4 : 30)
          let data = try Data(contentsOf: file)
          let checksum = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
          songs.append(CompanionSong(track: track, checksum: checksum, bytes: Int64(data.count)))
          files.append((file, CompanionFile(context: context, songId: track.id, checksum: checksum)))
        }
        let ids = songs.map(\.id)
        let collections =
          big
          ? [
            CompanionCollection(
              id: "favourites", name: "Favourites", songIds: Array(ids.prefix(14)),
              favourites: true),
            CompanionCollection(id: "run", name: "Morning run", songIds: Array(ids.dropFirst(10).prefix(25))),
            CompanionCollection(
              id: "long", name: "Songs for a very long drive up the coast",
              songIds: Array(ids.suffix(18))),
          ] : []
        try await archive.apply(context)
        try await archive.apply(
          CompanionManifest(context: context, songs: songs, collections: collections))
        for (file, ticket) in files { try await archive.accept(file, ticket: ticket) }
      } catch {
        notices.post("Demo library: \(error.localizedDescription)")
      }
      await reload()
      if ProcessInfo.processInfo.arguments.contains("--demo-play") {
        await play(manifest.songs.map(\.id))
      }
    }
    private static func writeTone(to url: URL, frequency: Double, seconds: Double) throws {
      let rate = 44_100.0
      let file = try AVAudioFile(
        forWriting: url,
        settings: [
          AVFormatIDKey: kAudioFormatMPEG4AAC, AVSampleRateKey: rate, AVNumberOfChannelsKey: 1,
        ])
      let frames = AVAudioFrameCount(rate * seconds)
      guard let buffer = AVAudioPCMBuffer(pcmFormat: file.processingFormat, frameCapacity: frames),
        let samples = buffer.floatChannelData?[0]
      else { throw CocoaError(.fileWriteUnknown) }
      for frame in 0..<Int(frames) {
        samples[frame] = Float(0.2 * sin(2 * .pi * frequency * Double(frame) / rate))
      }
      buffer.frameLength = frames
      try file.write(from: buffer)
      file.close()
    }
  #else
    static let demo = false
  #endif
  func reload() async {
    guard let archive else { return }
    link?.refreshState()
    manifest = await archive.snapshot()
    removing = Set(await archive.pendingRequests().filter { $0.value == "remove" }.keys)
    // Songs gone from the library need no reminder.
    committedRemovals.libraryChanged(to: Set(manifest.songs.map(\.id)))
    let next = await archive.receipt()
    // A song saved since it was turned away no longer counts against it.
    problems = problems.filter { id, _ in next.ready[id] == nil }
    if receipt?.context != next.context || receipt?.ready != next.ready
      || receipt?.manifestApplied != next.manifestApplied
    {
      DiagnosticsLog.shared.record(
        "sync", "archive revision \(next.context.revision): \(next.ready.count)/\(manifest.songs.count) songs ready"
          + ", manifest applied=\(next.manifestApplied)")
    }
    receipt = next
    restoreCheckpoint()
    await flushListens()
    await flushRequests()
  }
  private func acknowledge() async {
    guard let archive else { return }
    let receipt = await archive.receipt()
    self.receipt = receipt
    do {
      try link?.publishReceipt(
        CompanionAcknowledgement(receipt: receipt, manifest: await archive.snapshot()))
    } catch { notices.post(error.localizedDescription) }
  }
  private func contextReceived(_ context: CompanionContext) async {
    guard let archive else { return }
    contextFence.note(context)
    do {
      let old = await archive.activeContext()
      switch context.arrival(over: old) {
      case .stale: return
      case .repeated: return await acknowledge()
      case .apply: break
      }
      DiagnosticsLog.shared.record(
        "sync",
        "context revision \(context.revision)"
          + (old.account != context.account ? " for another account" : ""))
      // Only an account change revokes music; a routine revision keeps playing.
      if old.account != context.account { await stop() }
      try await archive.apply(context)
      await reload()
      await acknowledge()
    } catch CompanionFailure.stale {} catch { notices.post(error.localizedDescription) }
  }
  private func fileReceived(_ file: URL, data: Data) async {
    // Already taken by an earlier delivery of the same transfer.
    guard FileManager.default.fileExists(atPath: file.path) else { return }
    guard let archive, let envelope = try? JSONDecoder().decode(CompanionEnvelope.self, from: data)
    else {
      deleteInbox(file)
      return
    }
    let context = await archive.activeContext()
    // Context may arrive after its file.
    if envelope.context.revision > context.revision { return }
    do {
      if envelope.kind == "manifest" {
        guard envelope.context == context,
          (try file.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0) <= 2 * 1024 * 1024
        else { throw CompanionFailure.stale }
        // Up to 2 MB of JSON: read and decoded off the main thread (APP1).
        let manifest = try await Task.detached(priority: .userInitiated) {
          try JSONDecoder().decode(CompanionManifest.self, from: Data(contentsOf: file))
        }.value
        guard manifest.context == envelope.context else { throw CompanionFailure.invalid }
        let previous = await archive.snapshot()
        let applied = await archive.receipt().manifestApplied
        // A newer context may have arrived while this one was read: a
        // superseded manifest never stops what is playing.
        guard await archive.activeContext() == envelope.context else {
          throw CompanionFailure.stale
        }
        // A context announced while that answer came back is not in it yet;
        // nothing below awaits before playback may stop.
        guard contextFence.admits(envelope.context) else { throw CompanionFailure.stale }
        if previous != manifest || !applied {
          // Keep playing when every queued song is unchanged in the new library.
          let unchanged = manifest.keepsPlaying(queue.map(\.track.id), replacing: previous)
          if !unchanged { await stop() }
          try await archive.apply(manifest)
          DiagnosticsLog.shared.record(
            "sync",
            "manifest revision \(manifest.context.revision) applied: \(manifest.songs.count) songs"
              + (unchanged ? "" : "; playback stopped for a changed song"))
        }
        lastSynced = Date()
        recovery.libraryArrived()
      } else if envelope.kind == "audio", let ticket = envelope.file {
        // Audio can be delivered before its manifest, and songs from an earlier
        // revision after the next one is announced (#64): keep what can't be
        // stored yet until that manifest arrives.
        switch await archive.arrival(of: ticket) {
        case .wait: return
        case .stale: throw CompanionFailure.stale
        case .accept:
          // The envelope beside it was written when the file reached the inbox.
          let receivedAt = try? file.deletingPathExtension().appendingPathExtension("json")
            .resourceValues(forKeys: [.creationDateKey]).creationDate
          try await archive.accept(file, ticket: ticket)
          let active = appActive
          noteWake { $0.accepted(receivedAt: receivedAt, active: active) }
        }
        DiagnosticsLog.shared.record("sync", "audio accepted for revision \(ticket.context.revision)")
      } else {
        throw CompanionFailure.invalid
      }
      deleteInbox(file)
      await reload()
      await acknowledge()
      // A new library or a song taken: wake again while songs wait (#219).
      planRefresh()
      if envelope.kind == "manifest" { await link?.recoverInbox() }
    } catch CompanionFailure.stale { deleteInbox(file) } catch {
      DiagnosticsLog.shared.record(
        "sync", "\(envelope.kind) rejected: \(error.localizedDescription)")
      if envelope.kind == "audio", let ticket = envelope.file {
        // Shown with the song and in Storage's status, not as a passing notice.
        if (error as? CompanionFailure) == .quota {
          problems[ticket.songId] = .overLimit
        } else if (error as? CompanionArchiveFailure) == .deviceFull {
          problems[ticket.songId] = .deviceFull
        } else {
          problems[ticket.songId] = .unreadable
        }
      } else {
        notices.post(error.localizedDescription)
      }
      deleteInbox(file)
      await acknowledge()
    }
  }
  private func deleteInbox(_ file: URL) {
    try? FileManager.default.removeItem(at: file)
    try? FileManager.default.removeItem(
      at: file.deletingPathExtension().appendingPathExtension("json"))
  }
  func send(_ action: String, songIds: [String] = []) async {
    // Noted before any await, so a queue being built leaves these out.
    if action == "remove" { committedRemovals.commit(songIds) }
    if ["download", "remove"].contains(action), let archive {
      do { try await archive.request(action, ids: songIds) } catch {
        notices.post(error.localizedDescription)
        return
      }
    }
    if action == "sync" {
      await flushRequests()
      await flushListens()
    }
    let command = CompanionCommand(action, account: manifest.context.account, songIds: songIds)
    guard let data = try? JSONEncoder().encode(command) else { return }
    if action == "remove", let archive {
      // Only what plays the removed songs changes (#252): every removal
      // used to stop playback and clear the queue.
      await dropFromQueue(Set(songIds))
      for id in songIds { try? await archive.removeLocal(id: id) }
      await reload()
      await acknowledge()
    }
    if link?.reachable == true {
      do {
        if let reply = try await link?.request(data),
          let value = try? JSONDecoder().decode(CompanionReply.self, from: reply)
        {
          // The iPhone's own words when it has any; otherwise that it went.
          notices.post(value.message ?? "Sent to iPhone")
        }
      } catch { notices.post(error.localizedDescription) }
    } else {
      link?.message(data)
      notices.post("Waiting to send")
    }
  }
  /// Takes removed songs out of what plays (#252). A song that isn't playing
  /// leaves playback alone; the playing one moves on to the next song.
  private func dropFromQueue(_ ids: Set<String>) async {
    let staged = pendingRestore
    let current = staged?.index ?? latest.index
    let queued = staged?.ids ?? queue.map(\.track.id)
    var keep: [Int] = []
    var index = current
    var fromStart = false
    var inPlace = false
    switch WatchQueueRemoval.plan(
      queue: queued, current: current, removing: ids, repeatAll: repeatMode == .all)
    {
    case .untouched: return
    case .stop:
      await stop()
      return
    case .upcoming(let kept):
      keep = kept
      inPlace = true
    case .moved(let kept, let at):
      keep = kept
      index = at
    case .advance(let kept, let at):
      keep = kept
      index = at
      fromStart = true
    }
    let remaining = keep.map { queued[$0] }
    let elapsed = fromStart ? 0 : (staged?.elapsed ?? latest.elapsed)
    // The playing song keeps its place: the engine plays on with the new queue.
    if staged == nil, inPlace {
      let items = keep.map { queue[$0] }
      queue = items
      engine?.updateUpcoming(items)
      updateNowPlaying()
      return
    }
    if staged == nil, intent.wantsPlayback {
      await play(remaining, at: index, seconds: elapsed)
      return
    }
    // Paused, or a saved queue not prepared yet: it stays paused at its place.
    guard let account = manifest.context.account else { return await stop() }
    await stop()
    stage(Checkpoint(account: account, ids: remaining, index: index, elapsed: elapsed))
    checkpoint(playback)
  }
  func setBudget(_ gigabytes: Int) async {
    do {
      try await archive?.setBudget(Int64(gigabytes) * 1024 * 1024 * 1024)
      await reload()
      await acknowledge()
    } catch { notices.post(error.localizedDescription) }
  }
  func stop() async {
    playbackGeneration += 1
    intent.pause()
    pendingRestore = nil
    await engine?.stopAndWait()
    queue = []
    checkpointIndex = -1
    checkpoint(nil)
    updateNowPlaying()
  }
  func play(_ ids: [String], at index: Int = 0, seconds: Double = 0) async {
    guard let archive, let engine else { return }
    // Removal marks stay while this queue is collected (Codex round 2).
    let build = committedRemovals.beginBuilding()
    defer { committedRemovals.endBuilding(build) }
    await stop()
    let stamp = playbackGeneration
    var items: [PlaybackOccurrence] = []
    var start = 0
    var startSeconds: Double = 0
    for (position, id) in ids.enumerated() {
      if let song = manifest.songs.first(where: { $0.id == id }),
        await archive.localFile(id: id) != nil
      {
        // Songs still waiting for transfer are skipped: start at the chosen
        // song, or the nearest earlier one that is on the Watch.
        if position <= index { start = items.count }
        if position == index { startSeconds = seconds }
        items.append(PlaybackOccurrence(track: song.track))
      }
    }
    guard playbackGeneration == stamp, !items.isEmpty else { return }
    do {
      try await activateAudio()
      guard playbackGeneration == stamp else { return }
      // A removal committed while this queue was built (each await above)
      // found nothing to take out of it: drop those songs now, with no
      // await left before the queue is published (Codex round 1 on #252).
      guard
        let kept = WatchQueueRemoval.reconcile(
          items.map(\.track.id), start: start, removed: committedRemovals.removed(for: build))
      else { return }
      items = kept.keep.map { items[$0] }
      start = kept.start
      if !kept.startKept { startSeconds = 0 }
      queue = items
      intent.play()
      engine.replace(
        items, startingAt: start, startSeconds: startSeconds,
        crossfade: crossfade, repeatMode: repeatMode,
        acquire: { item in
          guard let file = await archive.localFile(id: item.track.id),
            let size = try file.resourceValues(forKeys: [.fileSizeKey]).fileSize
          else { throw CompanionFailure.stale }
          return PreparedAudioSource(file: file, byteCount: size)
        }, release: { _ in })
    } catch { notices.post(error.localizedDescription) }
  }
  private func activateAudio() async throws {
    let session = AVAudioSession.sharedInstance()
    try session.setCategory(.playback, mode: .default, policy: .longFormAudio)
    // The async form avoids a main-actor completion closure that the system
    // may call from another queue.
    guard try await session.activate(options: []) else {
      throw AudioSourceFailure.unavailable
    }
  }
  func activateAndResume() {
    // A restored or rebuilt queue is prepared on the first Play, at its saved position.
    if let restore = pendingRestore {
      pendingRestore = nil
      Task { await play(restore.ids, at: restore.index, seconds: restore.elapsed) }
      return
    }
    // The engine cannot resume a finished or failed queue: start it again, from
    // the top when it finished, or at the song that failed.
    if let state = engine?.snapshot.state, state == .ended || state == .failed, !queue.isEmpty {
      let index = state == .ended ? 0 : min(playback.index, queue.count - 1)
      Task { await play(queue.map { $0.track.id }, at: index) }
      return
    }
    intent.play()
    let stamp = playbackGeneration
    Task {
      do {
        try await activateAudio()
        guard playbackGeneration == stamp, intent.canPlay else { return }
        // Rebuilding a running engine is an audible skip; only recover a stopped one.
        if output?.requiresRecovery == true { engine?.recoverOutput() }
        engine?.resume()
      } catch { notices.post(error.localizedDescription) }
    }
  }
  func pause() {
    intent.pause()
    engine?.pause()
    checkpoint(latest)
  }
  // MARK: Audio lifecycle

  private func makeEngine() throws {
    let output = EngineQueueOutput()
    output.volume = Float(volume)
    let engine = try RealtimeQueueSession(
      output: output, sampleRate: 44100, blockFrames: 2048, lookaheadSeconds: 3)
    engine.onUpdate = { [weak self, weak engine] snapshot in
      guard let self, let engine, self.engine === engine else { return }
      if snapshot.state != self.playback.state || snapshot.index != self.playback.index {
        DiagnosticsLog.shared.record(
          "playback",
          "engine \(snapshot.state) song \(snapshot.index + 1)/\(self.queue.count)"
            + (snapshot.error.map { " error=\($0)" } ?? ""))
      }
      // A finished or failed queue no longer wants playback, so the next tap is Play.
      if snapshot.state == .ended || snapshot.state == .failed { self.intent.pause() }
      self.measureListen(snapshot)
      // Compare consecutive engine reports, not a screen snapshot that may
      // be minutes old: seeks still update system controls in the background.
      let jumped = abs(snapshot.elapsed - self.latest.elapsed) > 2
      self.latest = snapshot
      // The engine reports ten times a second. The system interpolates Now
      // Playing time, and the screen shows whole seconds, so only real changes
      // are published, and elapsed-only ticks are skipped while the screen is off.
      let changed = snapshot.differsFromPlayback(self.playback)
      if jumped || snapshot.shouldPublish(over: self.playback, sceneActive: self.sceneActive) {
        self.playback = snapshot
      }
      if changed || jumped { self.updateNowPlaying() }
      self.checkpointIfDue(snapshot)
    }
    self.output = output
    self.engine = engine
  }
  /// Like the phone: route and configuration notifications are debounced, and the
  /// output is rebuilt only when the engine actually stopped.
  private func recoverRoute() {
    DiagnosticsLog.shared.record(
      "audio",
      "route change: \(AVAudioSession.sharedInstance().currentRoute.outputs.map(\.portType.rawValue).joined(separator: ","))"
    )
    routeRecovery?.cancel()
    let stamp = playbackGeneration
    routeRecovery = Task {
      do { try await Task.sleep(for: .milliseconds(250)) } catch { return }
      guard playbackGeneration == stamp, !intent.interrupted,
        output?.requiresRecovery == true
      else { return }
      do {
        if intent.canPlay { try await activateAudio() }
        engine?.recoverOutput()
      } catch { notices.post(error.localizedDescription) }
    }
  }
  /// After a media-services reset the old engine objects are invalid: build new
  /// ones and keep the queue and position, ready for the next Play.
  private func rebuildAudio() async {
    DiagnosticsLog.shared.record("audio", "media services reset; rebuilding audio")
    let saved = currentCheckpoint(latest)
    playbackGeneration += 1
    intent.pause()
    routeRecovery?.cancel()
    await engine?.stopAndWait()
    engine = nil
    output = nil
    do { try makeEngine() } catch {
      notices.post(error.localizedDescription)
      return
    }
    if let saved { stage(saved) }
    notices.post("Audio restarted. Tap Play to resume.")
  }

  // MARK: Queue checkpoint

  /// The queue and position, restored paused after relaunch or an audio reset.
  struct Checkpoint: Codable, Equatable {
    var account: String
    var ids: [String]
    var index: Int
    var elapsed: Double
  }
  private static let checkpointKey = "watch.checkpoint"
  private func currentCheckpoint(_ snapshot: QueuePlaybackSnapshot) -> Checkpoint? {
    guard let account = manifest.context.account, queue.indices.contains(snapshot.index) else {
      return nil
    }
    return Checkpoint(
      account: account, ids: queue.map(\.track.id), index: snapshot.index,
      elapsed: snapshot.elapsed)
  }
  private func checkpoint(_ snapshot: QueuePlaybackSnapshot?) {
    lastCheckpoint = ProcessInfo.processInfo.systemUptime
    guard let snapshot, let saved = currentCheckpoint(snapshot),
      let data = try? JSONEncoder().encode(saved)
    else {
      UserDefaults.standard.removeObject(forKey: Self.checkpointKey)
      return
    }
    UserDefaults.standard.set(data, forKey: Self.checkpointKey)
  }
  /// Saved when the song changes and every few seconds while playing.
  private func checkpointIfDue(_ snapshot: QueuePlaybackSnapshot) {
    guard queue.indices.contains(snapshot.index), pendingRestore == nil else { return }
    let changed = snapshot.index != checkpointIndex
    let due = ProcessInfo.processInfo.systemUptime - lastCheckpoint >= 5
    guard changed || (due && snapshot.state == .playing) else { return }
    checkpointIndex = snapshot.index
    checkpoint(snapshot)
  }
  /// Shows a saved queue paused without preparing audio until Play.
  private func stage(_ saved: Checkpoint) {
    let songs = saved.ids.compactMap { id in manifest.songs.first(where: { $0.id == id }) }
    guard songs.count == saved.ids.count, saved.ids.indices.contains(saved.index) else { return }
    queue = songs.map { PlaybackOccurrence(track: $0.track) }
    playback = QueuePlaybackSnapshot(
      state: .paused, index: saved.index, elapsed: saved.elapsed,
      duration: songs[saved.index].track.duration ?? 0)
    pendingRestore = saved
    updateNowPlaying()
  }
  private func restoreCheckpoint() {
    guard !restoredCheckpoint, let account = manifest.context.account else { return }
    restoredCheckpoint = true
    guard queue.isEmpty,
      let data = UserDefaults.standard.data(forKey: Self.checkpointKey),
      let saved = try? JSONDecoder().decode(Checkpoint.self, from: data),
      saved.account == account
    else { return }
    stage(saved)
  }
  func toggle() { intent.wantsPlayback ? pause() : activateAndResume() }
  func next() {
    if let restore = pendingRestore {
      pendingRestore = nil
      guard restore.index + 1 < restore.ids.count else { return }
      Task { await play(restore.ids, at: restore.index + 1) }
      return
    }
    if playback.index + 1 < queue.count {
      engine?.next()
    } else if repeatMode == .all, !queue.isEmpty {
      Task { await play(queue.map { $0.track.id }) }
    } else {
      pause()
    }
  }
  func previous() {
    if let restore = pendingRestore {
      pendingRestore = nil
      let index = restore.elapsed > 3 ? restore.index : max(0, restore.index - 1)
      Task { await play(restore.ids, at: index) }
      return
    }
    engine?.previous()
  }
  func seek(_ seconds: Double) { engine?.seek(seconds) }
  private func updateNowPlaying() {
    guard queue.indices.contains(playback.index) else {
      MPNowPlayingInfoCenter.default().nowPlayingInfo = nil
      return
    }
    let track = queue[playback.index].track
    var info: [String: Any] = [
      MPMediaItemPropertyTitle: track.title, MPMediaItemPropertyArtist: track.artist,
      MPMediaItemPropertyAlbumTitle: track.album ?? "",
      MPMediaItemPropertyPlaybackDuration: playback.duration,
      MPNowPlayingInfoPropertyElapsedPlaybackTime: playback.elapsed,
      MPNowPlayingInfoPropertyPlaybackRate: playback.state == .playing ? 1.0 : 0.0,
    ]
    if let artwork = playerArtwork() { info[MPMediaItemPropertyArtwork] = artwork }
    MPNowPlayingInfoCenter.default().nowPlayingInfo = info
  }
  /// Songs reach the Watch without their covers, so the player shows the
  /// PigMusic cover where the system drew a generic speaker tile (#255).
  @ObservationIgnored private var standInArtwork: MPMediaItemArtwork?
  private func playerArtwork() -> MPMediaItemArtwork? {
    if let standInArtwork { return standInArtwork }
    guard let size = UIImage(named: "PigLaunch")?.size else { return nil }
    // Loaded where the system asks, so nothing main-actor is captured.
    let artwork = MPMediaItemArtwork(boundsSize: size) { @Sendable _ in
      UIImage(named: "PigLaunch") ?? UIImage()
    }
    standInArtwork = artwork
    return artwork
  }
}
