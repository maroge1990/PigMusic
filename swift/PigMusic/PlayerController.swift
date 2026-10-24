import PigAPI
import AVFoundation
import Combine
import MediaPlayer
import Observation
import PigDomain
import PigPlayback
import PigSync

/// Streaming alpha with an opt-in bounded engine for comparative device testing.
@MainActor @Observable final class PlayerController {
  private(set) var queue = PlaybackQueue()
  private(set) var playing = false
  private(set) var preparing = false
  private(set) var elapsed: Double = 0
  private(set) var duration: Double = 0
  /// `duration` is seamless playback's estimate for an MP3 that states no
  /// length: not the song's end, so a seek may go past it.
  private(set) var durationIsEstimate = false
  /// At the song's known end (an estimate never is).
  private var atEnd: Bool { !durationIsEstimate && duration > 0 && elapsed >= duration }
  var error: String?
  @ObservationIgnored var onListen: (@MainActor (ListeningEvent) -> Void)?
  /// Called when a new song becomes current, with the songs queued after it.
  @ObservationIgnored var onUpcoming: (@MainActor ([Track]) -> Void)?
  /// A person skipped a song, with the seconds of it they heard.
  @ObservationIgnored var onSkip: (@MainActor (Track, Double) -> Void)?
  /// Playback reached the end of the queue without repeat.
  @ObservationIgnored var onQueueEnd: (@MainActor () -> Void)?
  @ObservationIgnored private var listenOccurrence: UUID?
  @ObservationIgnored private var listenSeconds: Double = 0
  @ObservationIgnored private var listenReported = false
  @ObservationIgnored private var listenTick = ProcessInfo.processInfo.systemUptime
  private func measureListen() {
    let now = ProcessInfo.processInfo.systemUptime
    let delta = min(1, max(0, now - listenTick))
    listenTick = now
    guard let item = queue.current else {
      listenOccurrence = nil
      return
    }
    if listenOccurrence != item.id {
      listenOccurrence = item.id
      listenSeconds = 0
      listenReported = false
      let next = queue.items.dropFirst(queue.index + 1).prefix(2).map(\.track)
      onUpcoming?(Array(next))
      return
    }
    if playing { listenSeconds += delta }
    if listenSeconds >= 30, !listenReported {
      listenReported = true
      onListen?(
        ListeningEvent(
          eventId: item.id.uuidString, trackId: item.track.id,
          playedAt: Int(Date().timeIntervalSince1970), seconds: 30))
    }
  }
  @ObservationIgnored var onCheckpoint: (@MainActor (PlaybackCheckpoint) -> Void)?
  @ObservationIgnored private var checkpointTime = Date.distantPast
  /// Where the next song prepared starts, and whether a restored queue is
  /// still waiting for Play.
  @ObservationIgnored private var queueStart = QueueStart()
  private func checkpoint(force: Bool = false) {
    guard force || Date().timeIntervalSince(checkpointTime) >= 5 else { return }
    checkpointTime = Date()
    let pending = queueStart.seconds(for: queue.current)
    onCheckpoint?(PlaybackCheckpoint(queue: queue, elapsed: pending > 0 ? pending : elapsed))
  }
  func enqueue(_ tracks: [Track], next: Bool = false) {
    guard queue.current != nil else { return }
    queue.append(tracks, next: next)
    engineQueueChanged()
  }
  func removeUpcoming(_ id: UUID) {
    queue.removeUpcoming(id)
    engineQueueChanged()
  }
  func moveUpcoming(_ id: UUID, by delta: Int) {
    queue.moveUpcoming(id, by: delta)
    engineQueueChanged()
  }
  private func engineQueueChanged() {
    if usingEngine { engine?.updateUpcoming(queue.items) }
    checkpoint(force: true)
  }
  var seamlessEnabled: Bool {
    didSet {
      guard oldValue != seamlessEnabled else { return }
      defaults.set(seamlessEnabled, forKey: "pig.playback.seamless")
      // Keep the queue and position: restart the current song in the new mode.
      if !restartingForMode, queue.current != nil { restartInCurrentMode() }
    }
  }
  @ObservationIgnored private var restartingForMode = false
  private func restartInCurrentMode() {
    guard let resolve else { return }
    let wanted = intent.wantsPlayback
    let checkpoint = PlaybackCheckpoint(
      queue: queue, elapsed: atEnd ? 0 : elapsed)
    play(
      checkpoint.queue.items.map(\.track), startingAt: checkpoint.queue.index,
      autoplay: wanted, restored: checkpoint, artwork: artworkSource ?? { _ in nil },
      resolve: resolve)
  }
  var repeatMode: PlaybackRepeat = .off {
    didSet {
      defaults.set(repeatMode.rawValue, forKey: "pig.playback.repeat")
      if usingEngine { engine?.setRepeatMode(repeatMode) }
    }
  }
  private(set) var shuffled = false
  @ObservationIgnored private var originalOrder: [PlaybackOccurrence] = []
  func toggleShuffle() {
    if shuffled {
      queue.restoreUpcoming(originalOrder)
    } else {
      originalOrder = queue.items
      queue.shuffleUpcoming()
    }
    shuffled.toggle()
    engineQueueChanged()
  }
  var crossfadeEnabled: Bool {
    didSet { defaults.set(crossfadeEnabled, forKey: "pig.playback.crossfade") }
  }
  var wantsPlayback: Bool { intent.wantsPlayback }
  @ObservationIgnored private let defaults: UserDefaults
  @ObservationIgnored private let player = AVPlayer()
  @ObservationIgnored private var engine: RealtimeQueueSession?
  @ObservationIgnored private var engineOutput: EngineQueueOutput?
  @ObservationIgnored private var repository: PlaybackSourceRepository?
  @ObservationIgnored private var usingEngine = false
  @ObservationIgnored private var observations: [AnyCancellable] = []
  private var intent = PlaybackIntent()
  @ObservationIgnored private var clock: Task<Void, Never>?
  @ObservationIgnored private var preparation: Task<Void, Never>?
  @ObservationIgnored private var resolve: (@Sendable (Track) async throws -> URL)?
  @ObservationIgnored private var generation = 0
  @ObservationIgnored private var artworkTask: Task<Void, Never>?
  @ObservationIgnored private var artworkSource: (@MainActor (Track) async -> UIImage?)?
  @ObservationIgnored private var artworkOccurrence: UUID?
  @ObservationIgnored private var currentArtwork: MPMediaItemArtwork?
  @ObservationIgnored private var routeRecovery: Task<Void, Never>?

  init(defaults: UserDefaults = .standard) {
    self.defaults = defaults
    repeatMode =
      PlaybackRepeat(rawValue: defaults.string(forKey: "pig.playback.repeat") ?? "off") ?? .off
    seamlessEnabled = defaults.object(forKey: "pig.playback.seamless") as? Bool ?? true
    crossfadeEnabled = defaults.object(forKey: "pig.playback.crossfade") as? Bool ?? true
    #if DEBUG
      if ProcessInfo.processInfo.arguments.contains("--seamless-demo") { seamlessEnabled = true }
    #endif
    observations.append(
      NotificationCenter.default.publisher(for: .AVPlayerItemDidPlayToEndTime).receive(
        on: DispatchQueue.main
      ).sink {
        [weak self] notification in
        let identity = notification.object.map { ObjectIdentifier($0 as AnyObject) }
        Task { @MainActor in
          guard let self, !self.usingEngine, let item = self.player.currentItem,
            identity == ObjectIdentifier(item)
          else { return }
          self.finishedStreaming()
        }
      })
    observations.append(
      NotificationCenter.default.publisher(for: AVAudioSession.interruptionNotification).receive(
        on: DispatchQueue.main
      ).sink {
        [weak self] notification in
        let began =
          (notification.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt)
          == AVAudioSession.InterruptionType.began.rawValue
        let options = notification.userInfo?[AVAudioSessionInterruptionOptionKey] as? UInt ?? 0
        let shouldResume = AVAudioSession.InterruptionOptions(rawValue: options).contains(
          .shouldResume)
        let disconnected =
          (notification.userInfo?[AVAudioSessionInterruptionReasonKey] as? UInt)
          == AVAudioSession.InterruptionReason.routeDisconnected.rawValue
        Task { @MainActor in
          self?.interruption(began: began, shouldResume: shouldResume, disconnected: disconnected)
        }
      })
    observations.append(
      NotificationCenter.default.publisher(for: AVAudioSession.routeChangeNotification).receive(
        on: DispatchQueue.main
      ).sink {
        [weak self] notification in
        let reason = notification.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt
        Task { @MainActor in
          guard let self else { return }
          if reason == AVAudioSession.RouteChangeReason.oldDeviceUnavailable.rawValue {
            self.pause()
          }
          if self.usingEngine { self.recoverRoute() }
        }
      })
    observations.append(
      NotificationCenter.default.publisher(for: AVAudioSession.mediaServicesWereResetNotification)
        .receive(on: DispatchQueue.main).sink { [weak self] _ in
          DiagnosticsLog.shared.record("audio", "media services reset")
          Task { @MainActor in self?.sessionActive = false }
        })
    observations.append(
      NotificationCenter.default.publisher(for: .AVAudioEngineConfigurationChange).receive(
        on: DispatchQueue.main
      ).sink {
        [weak self] notification in
        let identity = notification.object.map { ObjectIdentifier($0 as AnyObject) }
        Task { @MainActor in
          guard let self, identity == self.engineOutput?.engineIdentity,
            self.engineOutput?.requiresRecovery == true
          else { return }
          self.recoverRoute()
        }
      })
    let commands = MPRemoteCommandCenter.shared()
    // @Sendable keeps each handler off the main actor: the system may call it
    // from another queue, and an isolated handler would assert before its Task.
    commands.playCommand.addTarget { @Sendable [weak self] _ in
      Task { @MainActor in self?.resume() }
      return .success
    }
    commands.pauseCommand.addTarget { @Sendable [weak self] _ in
      Task { @MainActor in self?.pause() }
      return .success
    }
    // Headphone and AirPods presses arrive as a single toggle command.
    commands.togglePlayPauseCommand.addTarget { @Sendable [weak self] _ in
      Task { @MainActor in self?.toggle() }
      return .success
    }
    commands.nextTrackCommand.addTarget { @Sendable [weak self] _ in
      Task { @MainActor in self?.skip() }
      return .success
    }
    commands.previousTrackCommand.addTarget { @Sendable [weak self] _ in
      Task { @MainActor in self?.previous() }
      return .success
    }
    commands.changePlaybackPositionCommand.addTarget { @Sendable [weak self] event in
      guard let event = event as? MPChangePlaybackPositionCommandEvent else {
        return .commandFailed
      }
      let position = event.positionTime
      Task { @MainActor in self?.seek(position) }
      return .success
    }
  }

  func configureSources(directory: URL) async throws {
    stop()
    let stamp = generation
    let old = repository
    let cleanup = engine?.stop()
    await cleanup?.value
    await old?.reset()
    guard generation == stamp else { throw CancellationError() }
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    // Exclusive account root: all earlier owners have stopped; remove crash leftovers only here.
    for child in try FileManager.default.contentsOfDirectory(
      at: directory, includingPropertiesForKeys: [.isDirectoryKey, .isSymbolicLinkKey])
    where child.lastPathComponent.hasPrefix("playback-") {
      let values = try child.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
      if values.isDirectory == true && values.isSymbolicLink != true {
        try FileManager.default.removeItem(at: child)
      }
    }
    repository = PlaybackSourceRepository(preparer: try AudioSourcePreparer(root: directory))
    if engine == nil {
      let output = EngineQueueOutput()
      engineOutput = output
      // Songs start once 0.75 s is decoded, while the rest arrives; if the
      // network falls behind, playback waits for 2 s rather than stuttering.
      let session = try RealtimeQueueSession(
        output: output, lookaheadSeconds: 3, startSeconds: 0.75, rebufferSeconds: 2)
      session.onUpdate = { [weak self] snapshot in self?.accept(snapshot) }
      engine = session
    }
  }
  func clearAccount() {
    onListen = nil
    onCheckpoint = nil
    stop()
    let old = repository
    repository = nil
    let cleanup = engine?.stop()
    Task {
      await cleanup?.value
      await old?.reset()
    }
  }
  func cachedRevisions() async -> [String: String?] { await repository?.savedRevisions() ?? [:] }
  func invalidateSources() async {
    stop()
    let old = repository
    let cleanup = engine?.stop()
    await cleanup?.value
    await old?.reset()
  }

  func play(
    _ tracks: [Track], startingAt: Int = 0, autoplay: Bool = true,
    restored: PlaybackCheckpoint? = nil,
    artwork: @escaping @MainActor (Track) async -> UIImage? = { _ in nil },
    resolve: @escaping @Sendable (Track) async throws -> URL
  ) {
    guard !tracks.isEmpty else {
      stop()
      return
    }
    preparation?.cancel()
    engine?.stop()
    clock?.cancel()
    player.pause()
    player.replaceCurrentItem(with: nil)
    generation += 1
    if autoplay {
      // Music the person chose plays even if iOS left an interruption open;
      // a restart of the same queue keeps waiting for it.
      if restored == nil { intent.choosePlay() } else { intent.play() }
      timeStart("play")
    } else {
      intent.pause()
    }
    self.resolve = resolve
    artworkSource = artwork
    if let restored {
      queue = restored.queue
    } else {
      queue.replace(tracks, startingAt: startingAt)
      shuffled = false
      originalOrder = []
    }
    // A restored queue shows where you were but fetches nothing until Play:
    // after the app was away, the connection may still be waking, and a
    // fetch nobody asked for would hold up the song that is chosen next.
    queueStart.restore(queue, at: restored?.elapsed ?? 0, waiting: restored != nil && !autoplay)
    elapsed = queueStart.seconds(for: queue.current)
    // A new queue tries seamless playback again; a restart keeps a fallback.
    if restored == nil { streamingFallback = false }
    usingEngine = seamlessEnabled && !streamingFallback
    error = nil
    addressed.removeAll()
    if queueStart.waiting {
      playing = false
      preparing = false
      duration = queue.current?.track.duration ?? 0
      durationIsEstimate = false
      publishNowPlaying()
    } else if usingEngine {
      startEngine()
    } else {
      prepareStreaming()
    }
    checkpoint(force: true)
    refreshArtwork()
  }
  /// Starts a restored queue's current song, playing it or ready to.
  private func beginDeferred(autoplay: Bool) {
    guard queueStart.begin() else { return }
    if autoplay {
      intent.play()
      timeStart("play")
    }
    if usingEngine { startEngine() } else { prepareStreaming() }
  }
  /// Starts the song Next or Previous chose on a restored queue.
  private func beginDeferredAtStart() {
    elapsed = 0
    duration = queue.current?.track.duration ?? 0
    durationIsEstimate = false
    beginDeferred(autoplay: false)
    checkpoint(force: true)
  }
  private func startEngine() {
    guard let repository, let engine, let resolve else {
      streamingFallback = true
      usingEngine = false
      prepareStreaming()
      return
    }
    // Activation finishes long before the first song is prepared.
    if intent.canPlay { activateForPlayback() }
    let addressed = addressed
    let start = queueStart.take(for: queue.current)
    engine.replace(
      queue.items, startingAt: queue.index, startSeconds: start, autoplay: intent.canPlay,
      crossfade: crossfadeEnabled, repeatMode: repeatMode,
      acquire: { item in
        let began = ContinuousClock.now
        let url = try await resolve(item.track)
        addressed.insert(item.id)
        // Downloaded songs play from their own file, whatever their size.
        if url.isFileURL { return try await repository.leaseLocal(url) }
        DiagnosticsLog.shared.record(
          "playback", "address in \(Self.milliseconds(since: began)) ms")
        return try await repository.acquire(item.track, initialURL: url, resolve: resolve)
      }, release: { await repository.release($0) })
    watchSlowStart()
  }
  /// What the person asked for and when, until the first sound of it.
  @ObservationIgnored private var startRequest: (what: String, at: ContinuousClock.Instant)?
  private func timeStart(_ what: String) { startRequest = (what, .now) }
  private func reportStart(_ mode: String) {
    guard let request = startRequest else { return }
    startRequest = nil
    DiagnosticsLog.shared.record(
      "playback",
      "\(request.what): sound after \(Self.milliseconds(since: request.at)) ms (\(mode))")
  }
  nonisolated private static func milliseconds(since start: ContinuousClock.Instant) -> Int {
    Int(((ContinuousClock.now - start) / .milliseconds(1)).rounded())
  }
  private func accept(_ snapshot: QueuePlaybackSnapshot) {
    guard usingEngine else { return }
    if snapshot.state == .playing { reportStart("seamless") }
    if snapshot.state != loggedState || snapshot.index != loggedIndex {
      loggedState = snapshot.state
      loggedIndex = snapshot.index
      DiagnosticsLog.shared.record(
        "playback",
        "engine \(snapshot.state) song \(snapshot.index + 1)/\(queue.items.count)"
          + (snapshot.error.map { " error=\($0)" } ?? ""))
    }
    queue.select(index: snapshot.index)
    if let id = snapshot.occurrenceID { queue.renewOccurrence(at: snapshot.index, id: id) }
    elapsed = snapshot.elapsed
    duration = snapshot.duration
    durationIsEstimate = snapshot.durationIsEstimate
    playing = snapshot.state == .playing
    preparing = snapshot.state == .preparing || snapshot.state == .buffering
    if snapshot.state == .failed {
      // Too large to stage, or unreadable by the engine: play it as a normal
      // stream instead of asking the person to change modes.
      if !streamingFallback, resolve != nil {
        fallBackToStreaming("engine could not prepare the song: \(snapshot.error ?? "unknown")")
        return
      }
      error = "This song couldn't be played. " + (snapshot.error ?? "")
      intent.pause()
    } else if snapshot.state == .ended {
      intent.pause()
      onQueueEnd?()
    }
    measureListen()
    checkpoint()
    refreshArtwork()
    publishNowPlaying()
  }
  /// Songs passed over in a row while streaming because they were unavailable.
  @ObservationIgnored private var unavailableRun = 0
  private func prepareStreaming() {
    generation += 1
    let stamp = generation
    preparation?.cancel()
    clock?.cancel()
    player.pause()
    player.replaceCurrentItem(with: nil)
    playing = false
    preparing = true
    error = nil
    guard let occurrence = queue.current, let resolve else {
      stop()
      return
    }
    let start = queueStart.take(for: occurrence)
    elapsed = start
    duration = occurrence.track.duration ?? 0
    durationIsEstimate = false
    refreshArtwork()
    publishNowPlaying()
    preparation = Task {
      do {
        let url = try await resolve(occurrence.track)
        try Task.checkCancellation()
        guard generation == stamp else { return }
        if intent.canPlay { try await activate() }
        guard generation == stamp else { return }
        player.replaceCurrentItem(with: AVPlayerItem(url: url))
        preparing = false
        if start > 0 {
          await player.seek(to: CMTime(seconds: start, preferredTimescale: 600))
          guard generation == stamp else { return }
        }
        if intent.canPlay { player.play() }
        unavailableRun = 0
        startClock()
      } catch is CancellationError {} catch {
        guard generation == stamp else { return }
        // A song that can't be fetched (not on this device or the server) is
        // passed over, as the seamless engine does, until every song was tried.
        if error as? NativeFailure != .authentication,
          unavailableRun + 1 < queue.items.count,
          queue.index + 1 < queue.items.count || repeatMode == .all
        {
          unavailableRun += 1
          DiagnosticsLog.shared.record("playback", "passed over an unavailable song")
          next()
          return
        }
        unavailableRun = 0
        self.error = error.userMessage
        preparing = false
        intent.pause()
      }
    }
  }
  /// Activating the audio session can block for a long time, so it never runs
  /// on the main thread (Xcode warns about UI unresponsiveness), and it is
  /// skipped while the session is already active.
  @ObservationIgnored private var sessionActive = false
  private func activate() async throws {
    guard !sessionActive else { return }
    try await Self.activateSession()
    sessionActive = true
  }
  /// One serial queue for every activation and deactivation, so a quick Stop
  /// then Play can never finish in the wrong order and leave the session off.
  nonisolated private static let sessionQueue = DispatchQueue(
    label: "au.com.pig.music.audio-session", qos: .userInitiated)
  nonisolated private static func activateSession() async throws {
    try await withCheckedThrowingContinuation {
      (continuation: CheckedContinuation<Void, any Error>) in
      sessionQueue.async {
        do {
          let session = AVAudioSession.sharedInstance()
          try session.setCategory(.playback, mode: .default, policy: .longFormAudio)
          try session.setActive(true)
          continuation.resume()
        } catch { continuation.resume(throwing: error) }
      }
    }
  }
  /// Starts activation for a play request, reporting a failure.
  private func activateForPlayback() {
    guard !sessionActive else { return }
    let stamp = generation
    Task {
      do { try await activate() } catch {
        guard generation == stamp else { return }
        self.error = error.userMessage
        intent.pause()
        suspend()
      }
    }
  }
  private func startClock() {
    clock?.cancel()
    clock = Task { [weak self] in
      while !Task.isCancelled {
        guard let self else { return }
        let time = player.currentTime().seconds
        elapsed = time.isFinite ? time : 0
        // A VBR MP3 without a length header makes AVPlayer guess, often
        // double. The song's known length wins unless the two agree.
        let end = player.currentItem?.duration.seconds ?? 0
        let known = queue.current?.track.duration ?? 0
        if end.isFinite, end > 0, known <= 0 || abs(end - known) <= max(5, known * 0.05) {
          duration = end
        } else if known > 0 {
          duration = known
        }
        playing = player.timeControlStatus == .playing
        if playing { reportStart("stream") }
        preparing = player.timeControlStatus == .waitingToPlayAtSpecifiedRate
        if let failed = player.currentItem?.error {
          error = failed.userMessage
          playing = false
          intent.pause()
        }
        measureListen()
        checkpoint()
        publishNowPlaying()
        do { try await Task.sleep(for: .milliseconds(250)) } catch { return }
      }
    }
  }
  /// Set when seamless playback could not start this queue; it then streams.
  @ObservationIgnored private var streamingFallback = false
  @ObservationIgnored private var slowStart: Task<Void, Never>?
  private func fallBackToStreaming(_ reason: String) {
    DiagnosticsLog.shared.record("playback", "streaming instead: \(reason)")
    streamingFallback = true
    restartInCurrentMode()
  }
  /// Seamless playback starts a remote song once its first moments arrive.
  /// If it still hasn't started 8 s after its address came back (a very poor
  /// connection), stream it instead. Until the address comes back the server
  /// itself is out of reach (often a connection still waking), and streaming
  /// would only start the same wait again.
  private func watchSlowStart() {
    slowStart?.cancel()
    let stamp = generation
    slowStart = Task {
      @MainActor func starting() -> Bool {
        generation == stamp && usingEngine && !streamingFallback && intent.canPlay
          && engine?.snapshot.state == .preparing && (engine?.snapshot.elapsed ?? 0) < 1
      }
      do {
        while starting(), !(engine?.snapshot.occurrenceID.map(addressed.contains) ?? false) {
          try await Task.sleep(for: .milliseconds(250))
        }
        try await Task.sleep(for: .seconds(8))
      } catch { return }
      guard starting() else { return }
      fallBackToStreaming("slow start")
    }
  }
  /// Songs whose playback address has come back, written by the engine's
  /// acquisition and read by the slow-start check.
  @ObservationIgnored private let addressed = AddressedOccurrences()
  @ObservationIgnored private var loggedState: QueuePlaybackState?
  @ObservationIgnored private var loggedIndex = -1
  private func interruption(began: Bool, shouldResume: Bool, disconnected: Bool = false) {
    DiagnosticsLog.shared.record(
      "audio",
      began
        ? "interruption began" + (disconnected ? " (output disconnected)" : "")
        : "interruption ended, resume=\(shouldResume)")
    if began, disconnected {
      // The headphones went away: no end follows, so this is a pause the
      // person can undo with Play, not something to wait out.
      sessionActive = false
      pause()
      return
    }
    if began {
      // The system deactivates the session during an interruption.
      sessionActive = false
      intent.beginInterruption()
      suspend()
    } else {
      intent.endInterruption(shouldResume: shouldResume)
      guard intent.canPlay else { return }
      let stamp = generation
      Task {
        do { try await activate() } catch {
          self.error = error.userMessage
          return
        }
        guard generation == stamp, intent.canPlay else { return }
        if usingEngine {
          engine?.recoverOutput()
          engine?.resume()
        } else {
          player.play()
        }
      }
    }
  }
  private func recoverRoute() {
    DiagnosticsLog.shared.record(
      "audio",
      "route change: \(AVAudioSession.sharedInstance().currentRoute.outputs.map(\.portType.rawValue).joined(separator: ","))"
    )
    guard usingEngine, !intent.interrupted, let state = engine?.snapshot.state,
      state == .playing || state == .paused || state == .buffering
    else { return }
    routeRecovery?.cancel()
    let stamp = generation
    routeRecovery = Task {
      do { try await Task.sleep(for: .milliseconds(250)) } catch { return }
      // Most route notifications (category changes, wake, new output on the
      // same format) leave the engine running; rebuilding then is an audible skip.
      guard generation == stamp, usingEngine, !intent.interrupted,
        engineOutput?.requiresRecovery == true
      else { return }
      do {
        if intent.canPlay { try await activate() }
        engine?.recoverOutput()
      } catch { self.error = error.userMessage }
    }
  }
  func toggle() { intent.wantsPlayback ? pause() : resume() }
  func resume() {
    // Play is the person's choice: it ends an interruption iOS never ended.
    intent.choosePlay()
    if queueStart.waiting { return beginDeferred(autoplay: true) }
    if usingEngine, engine?.snapshot.state == .ended || engine?.snapshot.state == .failed {
      if let resolve, let artworkSource {
        play(
          queue.items.map(\.track), startingAt: queue.index, artwork: artworkSource,
          resolve: resolve)
      }
      return
    }
    if !usingEngine, atEnd { seek(0) }
    guard intent.canPlay else { return }
    let start = { [self] in
      if usingEngine { engine?.resume() } else if player.currentItem != nil { player.play() }
      publishNowPlaying()
    }
    // Usually already active: resume at once, without a main-thread wait.
    if sessionActive { return start() }
    let stamp = generation
    Task {
      do { try await activate() } catch {
        self.error = error.userMessage
        return
      }
      guard generation == stamp, intent.canPlay else { return }
      start()
    }
  }
  private func suspend() {
    if usingEngine { engine?.pause() } else { player.pause() }
    playing = false
    publishNowPlaying()
  }
  func pause() {
    measureListen()
    intent.pause()
    suspend()
    checkpoint(force: true)
  }
  private func finishedStreaming() {
    if repeatMode == .one {
      queue.renewOccurrence(at: queue.index, id: UUID())
      prepareStreaming()
    } else {
      next()
    }
  }
  func retry(streaming: Bool = false, fromBeginning: Bool = false) {
    guard let resolve else { return }
    let artwork = artworkSource ?? { _ in nil }
    let checkpoint = PlaybackCheckpoint(
      queue: queue,
      elapsed: fromBeginning || atEnd ? 0 : elapsed)
    if streaming {
      restartingForMode = true
      seamlessEnabled = false
      restartingForMode = false
    }
    play(
      checkpoint.queue.items.map(\.track), startingAt: checkpoint.queue.index,
      restored: checkpoint, artwork: artwork, resolve: resolve)
  }
  /// A person chose to move on, as opposed to the song finishing.
  func skip() {
    if let item = queue.current { onSkip?(item.track, listenSeconds) }
    timeStart("skip")
    next()
  }
  func next() {
    if queueStart.waiting {
      // Choose the song first: only that one is fetched, from its start.
      guard queueStart.next(&queue, repeatMode: repeatMode) else {
        pause()
        onQueueEnd?()
        return
      }
      return beginDeferredAtStart()
    }
    if usingEngine {
      if queue.index + 1 < queue.items.count {
        engine?.next()
      } else if repeatMode == .all {
        queue.select(index: 0)
        retry(fromBeginning: true)
      } else {
        pause()
        onQueueEnd?()
      }
    } else if queue.next() {
      prepareStreaming()
    } else if repeatMode == .all, !queue.items.isEmpty {
      queue.select(index: 0)
      for index in queue.items.indices { queue.renewOccurrence(at: index, id: UUID()) }
      prepareStreaming()
    } else {
      pause()
      onQueueEnd?()
    }
  }
  func previous() {
    timeStart("previous")
    if queueStart.waiting {
      queueStart.previous(&queue)
      return beginDeferredAtStart()
    }
    if usingEngine {
      engine?.previous()
    } else if queue.previous(elapsed: elapsed) {
      seek(0)
    } else {
      prepareStreaming()
    }
  }
  func seek(_ seconds: Double) {
    guard seconds.isFinite else { return }
    let position = PlaybackSeek.target(
      seconds, duration: duration, isEstimate: durationIsEstimate)
    if queueStart.waiting {
      queueStart.seek(queue, to: position)
      elapsed = position
      checkpoint(force: true)
      publishNowPlaying()
      return
    }
    if intent.canPlay { timeStart("seek") }
    if usingEngine {
      engine?.seek(position)
    } else {
      player.seek(to: CMTime(seconds: position, preferredTimescale: 600))
      elapsed = position
    }
  }
  func stop() {
    generation += 1
    queueStart.reset()
    preparation?.cancel()
    clock?.cancel()
    routeRecovery?.cancel()
    slowStart?.cancel()
    engine?.stop()
    usingEngine = false
    player.pause()
    player.replaceCurrentItem(with: nil)
    queue.replace([])
    resolve = nil
    artworkTask?.cancel()
    artworkSource = nil
    artworkOccurrence = nil
    currentArtwork = nil
    intent = PlaybackIntent()
    playing = false
    preparing = false
    elapsed = 0
    duration = 0
    durationIsEstimate = false
    error = nil
    MPNowPlayingInfoCenter.default().nowPlayingInfo = nil
    sessionActive = false
    // Deactivation can block too: let other apps resume without stalling the UI.
    Self.sessionQueue.async {
      try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }
  }
  private func refreshArtwork() {
    guard let occurrence = queue.current, artworkOccurrence != occurrence.id else { return }
    artworkTask?.cancel()
    artworkOccurrence = occurrence.id
    currentArtwork = nil
    guard let artworkSource else { return }
    artworkTask = Task {
      guard let image = await artworkSource(occurrence.track), !Task.isCancelled,
        queue.current?.id == occurrence.id
      else { return }
      currentArtwork = Self.nowPlayingArtwork(image)
      publishNowPlaying()
    }
  }
  /// MediaPlayer requests the image on its own queue. The handler must not be
  /// main-actor isolated, or Swift's isolation check stops the app.
  nonisolated private static func nowPlayingArtwork(_ image: UIImage) -> MPMediaItemArtwork {
    MPMediaItemArtwork(boundsSize: image.size) { _ in image }
  }
  private func publishNowPlaying() {
    guard let song = queue.current?.track else { return }
    var information: [String: Any] = [
      MPMediaItemPropertyTitle: song.title, MPMediaItemPropertyArtist: song.artist,
      MPMediaItemPropertyAlbumTitle: song.album ?? "",
      MPMediaItemPropertyPlaybackDuration: duration,
      MPNowPlayingInfoPropertyElapsedPlaybackTime: elapsed,
      MPNowPlayingInfoPropertyPlaybackRate: playing ? 1.0 : 0.0,
    ]
    if let currentArtwork { information[MPMediaItemPropertyArtwork] = currentArtwork }
    MPNowPlayingInfoCenter.default().nowPlayingInfo = information
  }
}

/// Occurrence IDs, shared between the engine's acquisition and the main actor.
private final class AddressedOccurrences: @unchecked Sendable {
  private let lock = NSLock()
  private var ids: Set<UUID> = []
  func insert(_ id: UUID) { _ = lock.withLock { ids.insert(id) } }
  func contains(_ id: UUID) -> Bool { lock.withLock { ids.contains(id) } }
  func removeAll() { lock.withLock { ids.removeAll() } }
}
