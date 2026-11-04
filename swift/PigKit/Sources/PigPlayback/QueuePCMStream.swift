import AVFoundation
import Foundation
import PigDomain

public struct QueueAudioMarker: Sendable, Equatable {
  public let index: Int
  /// The incoming occurrence becomes the principal metadata at the fade midpoint.
  public let switchFrame: Int64
  public let originFrame: Int64
  public let durationFrames: Int64
  public var occurrenceID: UUID? = nil
  /// The duration is an estimate (an MP3 that states no length): a later
  /// marker for the same song corrects it.
  public var durationIsEstimate = false
}
public struct QueuePCMBlock: Sendable {
  public let pcm: PCMBlock
  public let markers: [QueueAudioMarker]
}
/// A song the queue could not play: its queue position, occurrence and why.
public struct QueueSongFailure: Sendable, Equatable {
  public let index: Int
  public let occurrenceID: UUID
  public let cause: String
}

/// A bounded decoder queue. Sources are prefetched one occurrence ahead and
/// decode while their bytes arrive; a slow network shows as buffering.
public actor QueuePCMStream {
  public typealias Acquire = @Sendable (PlaybackOccurrence) async throws -> PreparedAudioSource
  public typealias Release = @Sendable (PreparedAudioSource) async -> Void
  private let items: [PlaybackOccurrence]
  private let acquire: Acquire
  private let release: Release
  private let format: AVAudioFormat
  private let rate: Double
  private let blockFrames: Int
  private let repeatMode: PlaybackRepeat
  private var cycle = 0
  private var upcomingOccurrence: PlaybackOccurrence?
  /// The queue position being prepared to follow; unavailable songs are passed over.
  private var upcomingIndex: Int?
  /// Upcoming songs passed over since the current one started.
  private var passedOver = 0
  private let crossfade: Bool
  private let fadeSeconds: Double
  private var index: Int
  private var lease: PreparedAudioSource?
  private var decoder: StreamingPCMDecoder?
  /// The song that follows, as one value: which queue position and occurrence
  /// the prepared source belongs to. Passing over a failed song changes the
  /// choice during preparation, so the transition must use these together.
  private struct Upcoming: Sendable {
    let index: Int
    let occurrence: PlaybackOccurrence
    let source: PreparedAudioSource
  }
  private var upcoming: Task<Upcoming, Error>?
  /// No song after the playing one could be prepared; `song` was the last tried.
  private struct UpcomingFailure: Error {
    let song: QueueSongFailure
    let error: Error
  }
  /// The prepared following song, from when its source arrives until it plays.
  private var incoming: (index: Int, occurrence: PlaybackOccurrence)?
  private var incomingLease: PreparedAudioSource?
  private var incomingDecoder: StreamingPCMDecoder?
  private var tail: FrameRing
  private var head: FrameRing
  private var holdFrames = 0
  private var ended = false
  private var transitionReady = false
  private var prefix = 0
  private var overlap = 0
  private var mixed = 0
  private var outputFrames: Int64 = 0
  private var pendingMarkers: [QueueAudioMarker] = []
  /// The playing song's marker, re-sent whenever its estimated length changes.
  private var currentMarker: QueueAudioMarker?
  private var incomingMarker: QueueAudioMarker?
  private var closed = false
  private var reading = false
  private var sourceFailure: Error?
  /// The song `sourceFailure` belongs to.
  private var sourceFailureSong: QueueSongFailure?
  /// Songs passed over because they could not be prepared or opened, in order.
  public private(set) var passedOverSongs: [QueueSongFailure] = []
  /// The song whose failure ended the queue, once `next()` has thrown it
  /// (#25: the harness once guessed it from the songs heard).
  public private(set) var endingFailure: QueueSongFailure?

  public init(
    items: [PlaybackOccurrence], startingAt: Int = 0, startSeconds: Double = 0,
    crossfade: Bool = false, fadeSeconds: Double = 3, repeatMode: PlaybackRepeat = .off,
    sampleRate: Double = 48000,
    blockFrames: Int = 2048, acquire: @escaping Acquire, release: @escaping Release
  ) async throws {
    guard items.indices.contains(startingAt), startSeconds.isFinite, startSeconds >= 0,
      fadeSeconds.isFinite, (0...12).contains(fadeSeconds),
      [44100.0, 48000.0, 96000.0].contains(sampleRate), (256...8192).contains(blockFrames),
      let format = AVAudioFormat(standardFormatWithSampleRate: sampleRate, channels: 2)
    else { throw AudioPipelineFailure.invalidConfiguration }
    self.items = items
    self.index = startingAt
    self.acquire = acquire
    self.release = release
    self.format = format
    rate = sampleRate
    self.blockFrames = blockFrames
    self.repeatMode = repeatMode
    self.crossfade = crossfade
    self.fadeSeconds = fadeSeconds
    tail = FrameRing(capacity: Int(fadeSeconds * sampleRate) + blockFrames)
    head = FrameRing(capacity: max(1, Int(fadeSeconds * sampleRate)))
    let source = try await acquire(items[startingAt])
    do {
      try Task.checkCancellation()
      let first = try await Self.decoder(for: source, format: format, startSeconds: startSeconds)
      decoder = first
      lease = source
      // An estimated length may be short; the start is where the decoder began.
      let requested = Int64((startSeconds * rate).rounded())
      let skipped = first.lengthIsExact ? min(first.lengthFrames, requested) : requested
      let marker = QueueAudioMarker(
        index: index, switchFrame: 0, originFrame: -skipped,
        durationFrames: first.lengthFrames, occurrenceID: items[index].id,
        durationIsEstimate: !first.lengthIsExact)
      currentMarker = marker
      pendingMarkers = [marker]
      configureUpcoming()
    } catch {
      await release(source)
      throw error
    }
  }

  private static func decoder(
    for source: PreparedAudioSource, format: AVAudioFormat, startSeconds: Double = 0
  ) async throws -> StreamingPCMDecoder {
    try await StreamingPCMDecoder.open(source, format: format, startSeconds: startSeconds)
  }

  private func following(_ position: Int) -> Int? {
    if repeatMode == .one { return position }
    if position + 1 < items.count { return position + 1 }
    return repeatMode == .all ? 0 : nil
  }
  private func configureUpcoming() {
    passedOver = 0
    prepare(following(index))
  }
  /// Moves the upcoming choice past a song that couldn't be prepared (not on
  /// this device or the server). False once every other song has been tried.
  private func passOver() -> Bool {
    guard !closed, repeatMode != .one, let failed = upcomingIndex,
      passedOver + 1 < items.count, let candidate = following(failed), candidate != index
    else { return false }
    passedOver += 1
    choose(candidate)
    return true
  }
  private func prepare(_ nextIndex: Int?) {
    choose(nextIndex)
    guard upcomingOccurrence != nil else {
      upcoming = nil
      return
    }
    upcoming = Task { try await self.acquireViable() }
  }
  /// The upcoming song's source. A failure moves straight on to the next
  /// viable song while the current one keeps playing, so the gap is never
  /// heard; only when none is left does the failure reach the transition.
  private func acquireViable() async throws -> Upcoming {
    while true {
      guard let position = upcomingIndex, let occurrence = upcomingOccurrence else {
        throw AudioPipelineFailure.stalled
      }
      do {
        let source = try await acquire(occurrence)
        return Upcoming(index: position, occurrence: occurrence, source: source)
      } catch {
        try Task.checkCancellation()
        let song = QueueSongFailure(
          index: position, occurrenceID: occurrence.id, cause: error.localizedDescription)
        guard passOver() else { throw UpcomingFailure(song: song, error: error) }
        passedOverSongs.append(song)
      }
    }
  }
  private func choose(_ nextIndex: Int?) {
    holdFrames = 0
    upcomingIndex = nextIndex
    guard let nextIndex else {
      upcomingOccurrence = nil
      return
    }
    let incoming = items[nextIndex]
    let repeated = cycle > 0 || nextIndex <= index
    let next = repeated ? PlaybackOccurrence(track: incoming.track) : incoming
    upcomingOccurrence = next
    let mode = TransitionPlanner.mode(
      crossfade: crossfade && repeatMode != .one,
      albumOrder: TransitionPlanner.albumOrder(outgoing: items[index].track, incoming: next.track),
      duration: fadeSeconds)
    if case .crossfade(let seconds) = mode { holdFrames = Int(seconds * rate) }
  }

  public func next() async throws -> QueuePCMBlock? {
    guard !closed else { throw CancellationError() }
    guard !reading else { throw AudioSourceFailure.busy }
    reading = true
    defer { reading = false }
    while true {
      try Task.checkCancellation()
      guard !closed else { throw CancellationError() }
      while !ended {
        if tail.count > holdFrames {
          return emit(tail.pop(min(blockFrames, tail.count - holdFrames)))
        }
        guard let decoder else {
          ended = true
          continue
        }
        let decoded: AVAudioPCMBuffer?
        do { decoded = try await decoder.read(maxFrames: blockFrames) } catch {
          // A transfer or decode failing mid-song is the playing song's.
          if !(error is CancellationError), !closed {
            endingFailure = QueueSongFailure(
              index: index, occurrenceID: currentMarker?.occurrenceID ?? items[index].id,
              cause: error.localizedDescription)
          }
          throw error
        }
        guard !closed else { throw CancellationError() }
        remeasure(decoder)
        if let decoded { tail.push(decoded) } else { ended = true }
      }
      guard upcomingIndex != nil || incoming != nil else {
        if tail.count > 0 { return emit(tail.pop(min(blockFrames, tail.count))) }
        return nil
      }
      if let sourceFailure {
        if tail.count > 0 { return emit(tail.pop(min(blockFrames, tail.count))) }
        endingFailure = sourceFailureSong
        throw sourceFailure
      }
      if !transitionReady {
        guard let upcoming else { throw AudioPipelineFailure.stalled }
        let prepared: Upcoming
        do {
          prepared = try await withTaskCancellationHandler {
            try await upcoming.value
          } onCancel: {
            upcoming.cancel()
          }
        } catch let failure as UpcomingFailure {
          // Every remaining song was tried while the current one played.
          try Task.checkCancellation()
          sourceFailure = failure.error
          sourceFailureSong = failure.song
          continue
        } catch {
          try Task.checkCancellation()
          sourceFailure = error
          continue
        }
        // close() also awaits this task; ownership remains here only while the stream is open.
        guard !closed else { throw CancellationError() }
        self.upcoming = nil
        let source = prepared.source
        incomingLease = source
        incoming = (prepared.index, prepared.occurrence)
        try Task.checkCancellation()
        let next: StreamingPCMDecoder
        do { next = try await Self.decoder(for: source, format: format) } catch {
          try Task.checkCancellation()
          guard !closed else { throw CancellationError() }
          self.upcoming = nil
          incomingLease = nil
          incoming = nil
          await release(source)
          // It arrived but cannot be opened: as unplayable as one that never came.
          let song = QueueSongFailure(
            index: prepared.index, occurrenceID: prepared.occurrence.id,
            cause: error.localizedDescription)
          if passOver() {
            passedOverSongs.append(song)
            self.upcoming = Task { try await self.acquireViable() }
          } else {
            sourceFailure = error
            sourceFailureSong = song
          }
          continue
        }
        guard !closed else { throw CancellationError() }
        incomingDecoder = next
        do {
          while head.count < min(holdFrames, tail.count) {
            try Task.checkCancellation()
            guard
              let decoded = try await next.read(
                maxFrames: min(blockFrames, min(holdFrames, tail.count) - head.count))
            else { break }
            guard !closed else { throw CancellationError() }
            head.push(decoded)
          }
        } catch {
          try Task.checkCancellation()
          guard !closed else { throw CancellationError() }
          sourceFailure = error
          sourceFailureSong = QueueSongFailure(
            index: prepared.index, occurrenceID: prepared.occurrence.id,
            cause: error.localizedDescription)
          continue
        }
        guard let incoming else { throw AudioPipelineFailure.stalled }
        overlap = min(tail.count, head.count)
        prefix = tail.count - overlap
        let origin = outputFrames + Int64(prefix)
        let marker = QueueAudioMarker(
          index: incoming.index,
          switchFrame: origin + Int64(overlap / 2), originFrame: origin,
          durationFrames: next.lengthFrames, occurrenceID: incoming.occurrence.id,
          durationIsEstimate: !next.lengthIsExact)
        incomingMarker = marker
        pendingMarkers.append(marker)
        transitionReady = true
      }
      if prefix > 0 {
        let count = min(blockFrames, prefix)
        prefix -= count
        return emit(tail.pop(count))
      }
      if mixed < overlap {
        let count = min(blockFrames, overlap - mixed)
        var outgoing = tail.pop(count)
        let incoming = head.pop(count)
        for frame in 0..<count {
          let progress = overlap == 1 ? 0.5 : Double(mixed + frame) / Double(overlap - 1)
          let gains = TransitionPlanner.gains(progress: progress)
          for channel in 0..<2 {
            outgoing[channel][frame] =
              outgoing[channel][frame] * gains.outgoing + incoming[channel][frame] * gains.incoming
          }
        }
        mixed += count
        return emit(outgoing)
      }
      guard let followingIndex = incoming?.index else { throw AudioPipelineFailure.stalled }
      incoming = nil
      let old = lease
      lease = incomingLease
      decoder = incomingDecoder
      currentMarker = incomingMarker
      incomingLease = nil
      incomingDecoder = nil
      incomingMarker = nil
      if followingIndex <= index { cycle += 1 }
      index = followingIndex
      ended = false
      transitionReady = false
      prefix = 0
      overlap = 0
      mixed = 0
      // Release outgoing encoded storage before reserving the following source.
      if let old { await release(old) }
      guard !closed else { throw CancellationError() }
      configureUpcoming()
    }
  }
  /// Re-sends the playing song's marker when its estimated length changed.
  private func remeasure(_ decoder: StreamingPCMDecoder) {
    guard let marker = currentMarker, marker.durationIsEstimate,
      decoder.lengthFrames != marker.durationFrames || decoder.lengthIsExact
    else { return }
    let updated = QueueAudioMarker(
      index: marker.index, switchFrame: marker.switchFrame, originFrame: marker.originFrame,
      durationFrames: decoder.lengthFrames, occurrenceID: marker.occurrenceID,
      durationIsEstimate: !decoder.lengthIsExact)
    currentMarker = updated
    pendingMarkers.append(updated)
  }
  /// Markers no block has carried. At the end of the queue the last song's
  /// measured length can arrive with no audio left to travel with.
  public func takeMarkers() -> [QueueAudioMarker] {
    defer { pendingMarkers.removeAll() }
    return pendingMarkers
  }
  private func emit(_ channels: [[Float]]) -> QueuePCMBlock {
    let markers = pendingMarkers
    pendingMarkers.removeAll()
    outputFrames += Int64(channels[0].count)
    return QueuePCMBlock(pcm: PCMBlock(sampleRate: rate, channels: channels), markers: markers)
  }

  /// The owner cancels/awaits its pump before closing, so no decoder is still in use.
  public func close() async {
    guard !closed else { return }
    closed = true
    let pending = upcoming
    upcoming = nil
    pending?.cancel()
    decoder = nil
    incomingDecoder = nil
    if let lease {
      self.lease = nil
      await release(lease)
    }
    if let incomingLease {
      self.incomingLease = nil
      await release(incomingLease)
    }
    if let pending, let prepared = try? await pending.value { await release(prepared.source) }
  }
}
