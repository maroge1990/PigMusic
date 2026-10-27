import AVFoundation
import Combine
import Foundation
import MediaPlayer
import Observation
import PigCompanion
import PigDomain
import PigPlayback

@MainActor @Observable final class WatchModel {
  var manifest = CompanionManifest(context: CompanionContext(revision: 0, account: nil), songs: [])
  var receipt: CompanionReceipt?
  var link: CompanionLink?
  var message: String?
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
  /// Songs on the Watch and verified, which play with no phone or network.
  var readySongs: [CompanionSong] {
    manifest.songs.filter { receipt?.ready[$0.id] == $0.checksum }
  }
  var playback = QueuePlaybackSnapshot()
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
  struct AlbumGroup: Identifiable {
    let id: String
    let title: String
    let artist: String
    let ids: [String]
  }
  struct ArtistGroup: Identifiable {
    let id: String
    let name: String
    let albums: [AlbumGroup]
    var ids: [String] { albums.flatMap(\.ids) }
  }
  /// Artists by album artist, each with their albums on the Watch.
  var artists: [ArtistGroup] {
    Dictionary(grouping: albums, by: \.artist).map { name, albums in
      ArtistGroup(id: name, name: name, albums: albums)
    }.sorted { $0.name.sortingCompare($1.name) == .orderedAscending }
  }
  var albums: [AlbumGroup] {
    var grouped: [String: [CompanionSong]] = [:]
    for song in manifest.songs {
      let artist = song.track.albumArtist ?? song.track.artist
      let title = song.track.album ?? "Singles"
      let key = song.track.albumId ?? (artist + "\u{0}" + title)
      grouped[key, default: []].append(song)
    }
    var result: [AlbumGroup] = []
    for (key, songs) in grouped {
      guard let first = songs.first else { continue }
      let ordered: [CompanionSong] = songs.sorted { left, right in
        let leftDisc = left.track.discNumber ?? 1
        let rightDisc = right.track.discNumber ?? 1
        if leftDisc != rightDisc { return leftDisc < rightDisc }
        let leftTrack = left.track.trackNumber ?? 0
        let rightTrack = right.track.trackNumber ?? 0
        if leftTrack != rightTrack { return leftTrack < rightTrack }
        return left.id < right.id
      }
      let ids: [String] = ordered.map { $0.id }
      result.append(
        AlbumGroup(
          id: key, title: first.track.album ?? "Singles",
          artist: first.track.albumArtist ?? first.track.artist, ids: ids))
    }
    return result.sorted { left, right in
      if left.title != right.title { return left.title < right.title }
      if left.artist != right.artist { return left.artist < right.artist }
      return left.id < right.id
    }
  }
  func finishBackgroundDelivery() async {
    await link?.finishBackgroundDelivery()
    await reload()
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
        } catch { message = "Listening history could not be saved: " + error.localizedDescription }
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
  private func flushListens() async {
    guard let archive else { return }
    for event in await archive.pendingListens().prefix(50) {
      let command = CompanionCommand("listen", account: manifest.context.account, listen: event)
      if let data = try? JSONEncoder().encode(command) { link?.message(data) }
    }
  }
  /// Sends the Watch's lifecycle log to the phone, for its diagnostics export.
  func sendDiagnostics() {
    let info = Bundle.main.infoDictionary
    let version =
      "\(info?["CFBundleShortVersionString"] as? String ?? "?") (\(info?["CFBundleVersion"] as? String ?? "?"))"
    let payload = CompanionDiagnostics(
      version: version,
      text: DiagnosticsLog.shared.text(maximumBytes: CompanionDiagnostics.maximumTextBytes),
      endedUnexpectedly: DiagnosticsLog.shared.previousRunEndedUnexpectedly)
    if let data = try? JSONEncoder().encode(payload) { link?.message(data) }
  }
  @ObservationIgnored private var sentCrashNotice = false
  init() {
    let info = Bundle.main.infoDictionary
    DiagnosticsLog.shared.launched(
      "Watch",
      version:
        "\(info?["CFBundleShortVersionString"] as? String ?? "?") (\(info?["CFBundleVersion"] as? String ?? "?"))"
    )
    do {
      let base = try FileManager.default.url(
        for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true
      ).appendingPathComponent("PigMusicWatch")
      var values = URLResourceValues()
      values.isExcludedFromBackup = true
      try FileManager.default.createDirectory(at: base, withIntermediateDirectories: true)
      var excluded = base
      try excluded.setResourceValues(values)
      archive = try CompanionArchive(directory: base.appendingPathComponent("Music"))
      link = try CompanionLink(inbox: base.appendingPathComponent("Inbox"))
      link?.onContext = { [weak self] context in await self?.contextReceived(context) }
      link?.onFile = { [weak self] file, data in await self?.fileReceived(file, data: data) }
      link?.onActivated = { [weak self] in
        // After an unexpected end, the phone gets the log without being asked.
        if let self, DiagnosticsLog.shared.previousRunEndedUnexpectedly, !self.sentCrashNotice {
          self.sentCrashNotice = true
          self.sendDiagnostics()
        }
        await self?.reload()
        await self?.acknowledge()
        await self?.flushListens()
      }
      link?.onMessage = { [weak self] data in
        if let request = CompanionDiagnostics.decode(data), request.text == nil {
          self?.sendDiagnostics()
          return
        }
        if let reply = try? JSONDecoder().decode(CompanionReply.self, from: data) {
          if let id = reply.listenId, let archive = self?.archive {
            try? await archive.acknowledgeListen(id, account: reply.account)
            await self?.flushListens()
          }
          if let message = reply.message { self?.message = message }
        }
      }
      try makeEngine()
      notifications.append(
        NotificationCenter.default.publisher(for: AVAudioSession.interruptionNotification).receive(
          on: DispatchQueue.main
        ).sink {
          [weak self] notification in
          let began =
            (notification.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt)
            == AVAudioSession.InterruptionType.began.rawValue
          let resume = AVAudioSession.InterruptionOptions(
            rawValue: notification.userInfo?[AVAudioSessionInterruptionOptionKey] as? UInt ?? 0
          ).contains(.shouldResume)
          DiagnosticsLog.shared.record(
            "audio", began ? "interruption began" : "interruption ended, resume=\(resume)")
          Task { @MainActor in
            guard let self else { return }
            if began {
              self.intent.beginInterruption()
              self.engine?.pause()
            } else {
              self.intent.endInterruption(shouldResume: resume)
              if self.intent.canPlay { self.activateAndResume() }
            }
          }
        })
      notifications.append(
        NotificationCenter.default.publisher(for: AVAudioSession.routeChangeNotification).receive(
          on: DispatchQueue.main
        ).sink {
          [weak self] notification in
          let removed =
            (notification.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt)
            == AVAudioSession.RouteChangeReason.oldDeviceUnavailable.rawValue
          Task { @MainActor in
            if removed { self?.pause() }
            self?.recoverRoute()
          }
        })
      notifications.append(
        NotificationCenter.default.publisher(for: .AVAudioEngineConfigurationChange).receive(
          on: DispatchQueue.main
        ).sink { [weak self] _ in Task { @MainActor in self?.recoverRoute() } })
      notifications.append(
        NotificationCenter.default.publisher(for: AVAudioSession.mediaServicesWereResetNotification)
          .receive(on: DispatchQueue.main).sink { [weak self] _ in
            Task { @MainActor in await self?.rebuildAudio() }
          })
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
    } catch { message = error.localizedDescription }
  }
  func reload() async {
    guard let archive else { return }
    manifest = await archive.snapshot()
    receipt = await archive.receipt()
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
    } catch { message = error.localizedDescription }
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
    } catch CompanionFailure.stale {} catch { message = error.localizedDescription }
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
      } else if envelope.kind == "audio", let ticket = envelope.file {
        // Audio can be delivered before its manifest. Keep it until that manifest arrives.
        let snapshot = await archive.snapshot()
        let applied = await archive.receipt().manifestApplied
        if snapshot.context != context || !applied,
          envelope.context == context
        {
          return
        }
        try await archive.accept(file, ticket: ticket)
      } else {
        throw CompanionFailure.invalid
      }
      deleteInbox(file)
      await reload()
      await acknowledge()
      if envelope.kind == "manifest" { await link?.recoverInbox() }
    } catch CompanionFailure.stale { deleteInbox(file) } catch {
      DiagnosticsLog.shared.record(
        "sync", "\(envelope.kind) rejected: \(error.localizedDescription)")
      message = error.localizedDescription
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
    if ["download", "remove"].contains(action), let archive {
      do { try await archive.request(action, ids: songIds) } catch {
        message = error.localizedDescription
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
      await stop()
      for id in songIds { try? await archive.removeLocal(id: id) }
      await reload()
      await acknowledge()
    }
    if link?.reachable == true {
      do {
        if let reply = try await link?.request(data),
          let value = try? JSONDecoder().decode(CompanionReply.self, from: reply)
        {
          message = value.message
        }
      } catch { message = error.localizedDescription }
    } else if ["playPause", "next", "previous"].contains(action) {
      message = "Phone remote controls require a reachable phone."
    } else {
      link?.message(data)
      message = "Request queued for your phone."
    }
  }
  func setBudget(_ gigabytes: Int) async {
    do {
      try await archive?.setBudget(Int64(gigabytes) * 1024 * 1024 * 1024)
      await reload()
      await acknowledge()
    } catch { message = error.localizedDescription }
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
    } catch { message = error.localizedDescription }
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
      } catch { message = error.localizedDescription }
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
      self.latest = snapshot
      // The engine reports ten times a second. The system interpolates Now
      // Playing time, and the screen shows whole seconds, so only real changes
      // are published, and elapsed-only ticks are skipped while the screen is off.
      let changed =
        snapshot.state != self.playback.state || snapshot.index != self.playback.index
        || snapshot.occurrenceID != self.playback.occurrenceID
        || snapshot.duration != self.playback.duration || snapshot.error != self.playback.error
        || snapshot.wantsPlayback != self.playback.wantsPlayback
      let jumped = abs(snapshot.elapsed - self.playback.elapsed) > 2
      let tick = Int(snapshot.elapsed) != Int(self.playback.elapsed)
      if changed || jumped || (tick && self.sceneActive) { self.playback = snapshot }
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
      } catch { message = error.localizedDescription }
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
      message = error.localizedDescription
      return
    }
    if let saved { stage(saved) }
    message = "Audio restarted. Tap Play to resume."
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
    MPNowPlayingInfoCenter.default().nowPlayingInfo = [
      MPMediaItemPropertyTitle: track.title, MPMediaItemPropertyArtist: track.artist,
      MPMediaItemPropertyAlbumTitle: track.album ?? "",
      MPMediaItemPropertyPlaybackDuration: playback.duration,
      MPNowPlayingInfoPropertyElapsedPlaybackTime: playback.elapsed,
      MPNowPlayingInfoPropertyPlaybackRate: playback.state == .playing ? 1.0 : 0.0,
    ]
  }
}
