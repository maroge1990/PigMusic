import AVFoundation
import Foundation
import PigDomain

public enum QueuePlaybackState: Sendable, Equatable {
  case idle, preparing, buffering, playing, paused, ended, failed
}
public struct QueuePlaybackSnapshot: Sendable, Equatable {
  public init(
    state: QueuePlaybackState = .idle, index: Int = 0, occurrenceID: UUID? = nil,
    elapsed: Double = 0, duration: Double = 0, durationIsEstimate: Bool = false,
    wantsPlayback: Bool = false, error: String? = nil
  ) {
    self.state = state
    self.index = index
    self.occurrenceID = occurrenceID
    self.elapsed = elapsed
    self.duration = duration
    self.durationIsEstimate = durationIsEstimate
    self.wantsPlayback = wantsPlayback
    self.error = error
  }
  public var state: QueuePlaybackState = .idle
  public var index = 0
  public var occurrenceID: UUID?
  public var elapsed: Double = 0
  public var duration: Double = 0
  /// `duration` is provisional: an MP3 that states no length, still arriving.
  /// It is corrected as the song decodes and never falls behind `elapsed`.
  public var durationIsEstimate = false
  public var wantsPlayback = false
  public var error: String?

  /// Changes that must reach Now Playing even while the screen is off.
  public func differsFromPlayback(_ previous: Self) -> Bool {
    state != previous.state || index != previous.index || occurrenceID != previous.occurrenceID
      || duration != previous.duration || durationIsEstimate != previous.durationIsEstimate
      || error != previous.error || wantsPlayback != previous.wantsPlayback
  }

  public func shouldPublish(over previous: Self, sceneActive: Bool) -> Bool {
    differsFromPlayback(previous)
      || (sceneActive && (abs(elapsed - previous.elapsed) > 2 || Int(elapsed) != Int(previous.elapsed)))
  }
}

/// Where a seek lands, in both playback modes. An exact duration bounds it;
/// an estimate is not an endpoint, so a later position is tried and the
/// decoder finds the song's real end (a start past it ends the song).
public enum PlaybackSeek {
  public static func target(_ seconds: Double, duration: Double, isEstimate: Bool) -> Double {
    let position = max(0, seconds)
    return isEstimate ? position : min(position, max(0, duration))
  }
}

/// Injected output makes scheduling/lifecycle tests silent and deterministic.
@MainActor public protocol QueueAudioOutput: AnyObject {
  var positionFrames: Int64 { get }
  func configure(sampleRate: Double, blockFrames: Int) throws
  func schedule(_ block: PCMBlock, completion: @escaping @Sendable () -> Void) throws
  func resume() throws
  func pause()
  func stop()
}

/// Live output adapter; the app owner configures AVAudioSession and handles route/interruption events.
@MainActor public final class EngineQueueOutput: QueueAudioOutput {
  private let engine = AVAudioEngine()
  private let node = AVAudioPlayerNode()
  private var connected = false
  private var generation = 0
  private var scheduledFrames: Int64 = 0
  private var completedFrames: Int64 = 0
  private var segmentOrigin: Int64 = 0
  private var nodeOrigin: Int64 = 0
  private var pausedPosition: Int64 = 0
  public init() { engine.attach(node) }
  public var volume: Float {
    get { node.volume }
    set { node.volume = min(1, max(0, newValue)) }
  }
  public var engineIdentity: ObjectIdentifier { ObjectIdentifier(engine) }
  public var requiresRecovery: Bool { connected && !engine.isRunning }
  private var nodePosition: Int64 {
    guard let time = node.lastRenderTime, let player = node.playerTime(forNodeTime: time) else {
      return 0
    }
    return max(0, player.sampleTime)
  }
  public var positionFrames: Int64 {
    guard node.isPlaying else { return pausedPosition }
    return min(
      scheduledFrames, max(completedFrames, segmentOrigin + max(0, nodePosition - nodeOrigin)))
  }
  public func configure(sampleRate: Double, blockFrames: Int) throws {
    stop()
    if connected { engine.disconnectNodeOutput(node) }
    guard let format = AVAudioFormat(standardFormatWithSampleRate: sampleRate, channels: 2) else {
      throw AudioPipelineFailure.invalidConfiguration
    }
    engine.connect(node, to: engine.mainMixerNode, format: format)
    connected = true
    engine.prepare()
    try engine.start()
  }
  public func schedule(_ block: PCMBlock, completion: @escaping @Sendable () -> Void) throws {
    if scheduledFrames == completedFrames, node.isPlaying {
      // Remove starvation silence from the music timeline when scheduling resumes.
      segmentOrigin = completedFrames
      nodeOrigin = nodePosition
    }
    let frames = Int64(block.frameCount)
    scheduledFrames += frames
    let stamp = generation
    // Called on the audio render thread: @Sendable keeps it off the main actor
    // whatever the SDK infers, and the Task hops back.
    node.scheduleBuffer(try block.audioBuffer(), completionCallbackType: .dataPlayedBack) {
      @Sendable [weak self] _ in
      Task { @MainActor in
        guard let self, self.generation == stamp else { return }
        self.completedFrames += frames
        completion()
      }
    }
  }
  public func resume() throws {
    if !engine.isRunning { try engine.start() }
    node.play()
  }
  public func pause() {
    pausedPosition = positionFrames
    node.pause()
    engine.pause()
  }
  public func stop() {
    generation += 1
    node.stop()
    engine.stop()
    scheduledFrames = 0
    completedFrames = 0
    segmentOrigin = 0
    nodeOrigin = 0
    pausedPosition = 0
  }

}

/// Realtime scheduling candidate, exposed only through the app’s opt-in testing setting.
@MainActor public final class RealtimeQueueSession {
  public private(set) var snapshot = QueuePlaybackSnapshot()
  public var onUpdate: (@MainActor (QueuePlaybackSnapshot) -> Void)?
  public private(set) var peakScheduledFrames: Int64 = 0
  private let output: any QueueAudioOutput
  private let rate: Double
  private let blockFrames: Int
  private let lookaheadFrames: Int
  private let startFrames: Int
  private let rebufferFrames: Int
  private var items: [PlaybackOccurrence] = []
  private var acquire: QueuePCMStream.Acquire?
  private var release: QueuePCMStream.Release?
  private var crossfade = false
  private var repeatMode: PlaybackRepeat = .off
  private var fadeSeconds: Double = 3
  private var pump: Task<Void, Never>?
  private var generation = 0
  private var pendingFrames: Int64 = 0
  private var timeline: [QueueAudioMarker] = []
  private var ready = false
  private var clock: Task<Void, Never>?
  private var sourceWaiting = false

  /// `lookaheadSeconds` is how much decoded audio stays scheduled ahead of the
  /// speaker. The pump runs on the main actor, so live output needs enough to
  /// ride out UI work (scene changes, large list updates) without starving;
  /// nil keeps the minimal three-block bound used by tests.
  /// `startSeconds` of audio is decoded before a song starts, and when the
  /// source falls behind, playback waits for `rebufferSeconds` before going
  /// on rather than stuttering block by block (nil: no wait).
  public init(
    output: any QueueAudioOutput, sampleRate: Double = 48000, blockFrames: Int = 2048,
    lookaheadSeconds: Double? = nil, startSeconds: Double? = nil, rebufferSeconds: Double? = nil
  ) throws {
    guard [44100.0, 48000.0, 96000.0].contains(sampleRate), (256...8192).contains(blockFrames),
      (lookaheadSeconds ?? 0) >= 0, (lookaheadSeconds ?? 0) <= 10,
      (startSeconds ?? 0) >= 0, (startSeconds ?? 0) <= (lookaheadSeconds ?? 0) + 1,
      (rebufferSeconds ?? 0) >= 0, (rebufferSeconds ?? 0) <= 10
    else {
      throw AudioPipelineFailure.invalidConfiguration
    }
    self.output = output
    rate = sampleRate
    self.blockFrames = blockFrames
    lookaheadFrames = max(blockFrames * 3, Int((lookaheadSeconds ?? 0) * sampleRate))
    startFrames = max(blockFrames * 3, Int((startSeconds ?? 0) * sampleRate))
    rebufferFrames = Int((rebufferSeconds ?? 0) * sampleRate)
  }

  /// `startSeconds` resumes the first song part-way, as when restoring a checkpoint.
  public func replace(
    _ items: [PlaybackOccurrence], startingAt: Int = 0, startSeconds: Double = 0,
    autoplay: Bool = true, crossfade: Bool = false, fadeSeconds: Double = 3,
    repeatMode: PlaybackRepeat = .off,
    acquire: @escaping QueuePCMStream.Acquire, release: @escaping QueuePCMStream.Release
  ) {
    self.items = items
    self.acquire = acquire
    self.release = release
    self.crossfade = crossfade
    self.repeatMode = repeatMode
    self.fadeSeconds = fadeSeconds
    guard !items.isEmpty else {
      stop()
      return
    }
    let index = min(max(0, startingAt), items.count - 1)
    let duration = items[index].track.duration ?? 0
    let seconds = startSeconds.isFinite ? max(0, startSeconds) : 0
    restart(index: index, seconds: duration > 0 ? min(seconds, duration) : seconds, autoplay: autoplay)
  }

  /// Connects signed-server/provider resolution to temporary source leases.
  public func replace(
    _ items: [PlaybackOccurrence], startingAt: Int = 0, autoplay: Bool = true,
    crossfade: Bool = false, fadeSeconds: Double = 3, repeatMode: PlaybackRepeat = .off,
    preparer: AudioSourcePreparer,
    resolve: @escaping @Sendable (Track) async throws -> URL
  ) {
    replace(
      items, startingAt: startingAt, autoplay: autoplay, crossfade: crossfade,
      fadeSeconds: fadeSeconds, repeatMode: repeatMode,
      acquire: { item in try await preparer.prepare { try await resolve(item.track) } },
      release: { source in await preparer.release(source) })
  }

  public func pause() {
    snapshot.wantsPlayback = false
    output.pause()
    refreshPosition()
    clock?.cancel()
    clock = nil
    if ready { snapshot.state = .paused }
    publish()
  }
  public func resume() {
    guard snapshot.state != .idle, snapshot.state != .ended, snapshot.state != .failed else {
      return
    }
    snapshot.wantsPlayback = true
    startClock(generation: generation)
    if ready {
      do { try output.resume() } catch {
        pump?.cancel()
        clock?.cancel()
        clock = nil
        output.stop()
        snapshot.state = .failed
        snapshot.wantsPlayback = false
        snapshot.error = error.localizedDescription
        publish()
        return
      }
      snapshot.state = .playing
    }
    publish()
  }
  /// Explicit queue edits invalidate prepared future audio while preserving the current position.
  public func setRepeatMode(_ mode: PlaybackRepeat) {
    repeatMode = mode
    guard !items.isEmpty else { return }
    refreshPosition()
    restart(index: snapshot.index, seconds: snapshot.elapsed, autoplay: snapshot.wantsPlayback)
  }
  public func updateUpcoming(_ updated: [PlaybackOccurrence]) {
    guard updated.indices.contains(snapshot.index), items.indices.contains(snapshot.index),
      updated[snapshot.index].id == items[snapshot.index].id
    else { return }
    items = updated
    restart(index: snapshot.index, seconds: snapshot.elapsed, autoplay: snapshot.wantsPlayback)
  }
  public func seek(_ seconds: Double) {
    guard !items.isEmpty, seconds.isFinite else { return }
    refreshPosition()
    restart(
      index: snapshot.index,
      seconds: PlaybackSeek.target(
        seconds, duration: snapshot.duration, isEstimate: snapshot.durationIsEstimate),
      autoplay: snapshot.wantsPlayback)
  }
  public func next() {
    refreshPosition()
    guard snapshot.index + 1 < items.count else {
      stop()
      return
    }
    restart(index: snapshot.index + 1, seconds: 0, autoplay: snapshot.wantsPlayback)
  }
  public func previous() {
    refreshPosition()
    let index = snapshot.elapsed > 3 ? snapshot.index : max(0, snapshot.index - 1)
    restart(index: index, seconds: 0, autoplay: snapshot.wantsPlayback)
  }
  @discardableResult public func stop() -> Task<Void, Never>? {
    generation += 1
    pump?.cancel()
    clock?.cancel()
    clock = nil
    output.stop()
    ready = false
    sourceWaiting = false
    pendingFrames = 0
    timeline = []
    snapshot = QueuePlaybackSnapshot()
    items = []
    acquire = nil
    release = nil
    publish()
    return pump
  }
  /// Waits for cancelled decoders and acquired/prefetched source leases to be released.
  public func stopAndWait() async {
    stop()
    let stamp = generation
    let stopped = pump
    await stopped?.value
    if generation == stamp { pump = nil }
  }

  private func restart(index: Int, seconds: Double, autoplay: Bool) {
    generation += 1
    let stamp = generation
    let old = pump
    old?.cancel()
    clock?.cancel()
    clock = nil
    output.stop()
    ready = false
    sourceWaiting = false
    pendingFrames = 0
    peakScheduledFrames = 0
    timeline = []
    // Restarting the same song (a seek) keeps what is known of its length.
    let known = snapshot.occurrenceID == items[index].id && snapshot.duration > 0
    let estimate = known && snapshot.durationIsEstimate
    let duration = known ? snapshot.duration : items[index].track.duration ?? 0
    snapshot = QueuePlaybackSnapshot(
      state: .preparing, index: index, occurrenceID: items[index].id, elapsed: seconds,
      duration: estimate ? max(duration, seconds) : duration, durationIsEstimate: estimate,
      wantsPlayback: autoplay)
    publish()
    let items = items
    let crossfade = crossfade
    let fade = fadeSeconds
    let repeatMode = repeatMode
    guard let acquire, let release else { return }
    // Old cleanup completes before a new source reservation can race the same preparer.
    pump = Task { [weak self] in
      await old?.value
      guard let self, generation == stamp, !Task.isCancelled else { return }
      var stream: QueuePCMStream?
      do {
        let current = try await QueuePCMStream(
          items: items, startingAt: index, startSeconds: seconds,
          crossfade: crossfade, fadeSeconds: fade, repeatMode: repeatMode, sampleRate: rate,
          blockFrames: blockFrames,
          acquire: acquire, release: release)
        stream = current
        try Task.checkCancellation()
        guard generation == stamp else { throw CancellationError() }
        try output.configure(sampleRate: rate, blockFrames: blockFrames)
        var exhausted = false
        var sourceFailure: Error?
        // Audio held back while refilling after the source fell behind.
        var held: [QueuePCMBlock] = []
        var heldFrames = 0
        // A full lookahead needs no polling. Buffer completions wake the pump;
        // buffering the latest wake also covers completions during decoding.
        let (consumed, wake) = AsyncStream<Void>.makeStream(bufferingPolicy: .bufferingNewest(1))
        defer { wake.finish() }
        var completions = consumed.makeAsyncIterator()
        @MainActor func send(_ block: QueuePCMBlock) throws {
          timeline.append(contentsOf: block.markers)
          let frames = Int64(block.pcm.frameCount)
          pendingFrames += frames
          peakScheduledFrames = max(peakScheduledFrames, pendingFrames)
          try output.schedule(block.pcm) { [weak self] in
            Task { @MainActor in
              guard let self, self.generation == stamp else { return }
              self.pendingFrames = max(0, self.pendingFrames - frames)
              wake.yield(())
            }
          }
        }
        @MainActor func sendHeld() throws {
          for block in held { try send(block) }
          held = []
          heldFrames = 0
        }
        if snapshot.wantsPlayback { startClock(generation: stamp) }
        while !exhausted || pendingFrames > 0 || !held.isEmpty {
          try Task.checkCancellation()
          guard generation == stamp else { throw CancellationError() }
          // Start after a short cushion, then build the full one while playing.
          let target = ready ? lookaheadFrames : startFrames
          while !exhausted && pendingFrames <= Int64(target) {
            let decoded: QueuePCMBlock?
            sourceWaiting = true
            do { decoded = try await current.next() } catch {
              sourceWaiting = false
              try Task.checkCancellation()
              sourceFailure = error
              exhausted = true
              break
            }
            sourceWaiting = false
            try Task.checkCancellation()
            guard generation == stamp else { throw CancellationError() }
            guard let block = decoded else {
              // The last song's final length, with no audio left to carry it.
              let markers = await current.takeMarkers()
              guard generation == stamp else { throw CancellationError() }
              timeline.append(contentsOf: markers)
              exhausted = true
              break
            }
            try Task.checkCancellation()
            guard generation == stamp else { throw CancellationError() }
            // Everything scheduled has played: the source fell behind.
            let starved = ready && rebufferFrames > 0 && pendingFrames == 0
            if starved || !held.isEmpty {
              held.append(block)
              heldFrames += block.pcm.frameCount
              if heldFrames < rebufferFrames { continue }
              try sendHeld()
              continue
            }
            try send(block)
          }
          if exhausted, !held.isEmpty { try sendHeld() }
          if !ready {
            ready = true
            if snapshot.wantsPlayback {
              try output.resume()
              snapshot.state = .playing
            } else {
              output.pause()
              snapshot.state = .paused
            }
            refreshPosition()
            publish()
            // Build the larger playing cushion immediately after the short
            // startup cushion, before waiting for the speaker to consume it.
            if !exhausted { continue }
          }
          if exhausted && pendingFrames == 0 && held.isEmpty { break }
          _ = await completions.next()
        }
        try Task.checkCancellation()
        guard generation == stamp else { throw CancellationError() }
        refreshPosition()
        snapshot.state = sourceFailure == nil ? .ended : .failed
        snapshot.error = sourceFailure?.localizedDescription
        snapshot.wantsPlayback = false
        snapshot.elapsed = snapshot.duration
        output.pause()
        publish()
      } catch {
        if generation == stamp, !Task.isCancelled {
          output.stop()
          snapshot.state = .failed
          snapshot.wantsPlayback = false
          snapshot.error = error.localizedDescription
          publish()
        }
      }
      if generation == stamp {
        clock?.cancel()
        clock = nil
      }
      if let stream { await stream.close() }
    }
  }
  /// Rebuild after an interrupted/stopped engine or a route configuration change.
  public func recoverOutput() {
    guard ready, !items.isEmpty, snapshot.state != .ended, snapshot.state != .failed else { return }
    // A configuration change may already have reset the hardware timeline.
    // Use the last published position (captured immediately by pause), rather
    // than overwriting it with the stopped output's zero position.
    restart(index: snapshot.index, seconds: snapshot.elapsed, autoplay: snapshot.wantsPlayback)
  }
  private func startClock(generation stamp: Int) {
    clock?.cancel()
    clock = Task { [weak self] in
      while !Task.isCancelled {
        guard let self, generation == stamp else { return }
        if ready {
          if snapshot.wantsPlayback, sourceWaiting, pendingFrames == 0 {
            snapshot.state = .buffering
          } else if snapshot.wantsPlayback, pendingFrames > 0 {
            snapshot.state = .playing
          }
          refreshPosition()
          publish()
        }
        do { try await Task.sleep(for: .milliseconds(100)) } catch { return }
      }
    }
  }
  private func refreshPosition() {
    guard ready else { return }
    let position = output.positionFrames
    guard let marker = timeline.last(where: { $0.switchFrame <= position }),
      items.indices.contains(marker.index)
    else { return }
    if let current = timeline.lastIndex(where: { $0.switchFrame <= position }), current > 0 {
      timeline.removeFirst(current)
    }
    snapshot.index = marker.index
    snapshot.occurrenceID = marker.occurrenceID ?? items[marker.index].id
    snapshot.duration = Double(marker.durationFrames) / rate
    snapshot.durationIsEstimate = marker.durationIsEstimate
    snapshot.elapsed = min(snapshot.duration, max(0, Double(position - marker.originFrame) / rate))
  }
  private func publish() { onUpdate?(snapshot) }
}
