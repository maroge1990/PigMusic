#if os(iOS) && canImport(ShazamKit)
  import AVFAudio
  import Foundation
  import MusicKit
  import PigDomain
  import Observation
  import ShazamKit

  /// ShazamKit records through the app's shared audio session, which
  /// PigMusic sets to `.playback` for its own songs and which otherwise
  /// starts as `.soloAmbient`: neither has an input, so a managed session
  /// hears nothing (#124). While listening it is `.playAndRecord`.
  private struct SharedAudioSession: ListeningAudioSession {
    struct Configuration {
      var category: AVAudioSession.Category
      var mode: AVAudioSession.Mode
      var policy: AVAudioSession.RouteSharingPolicy
      var options: AVAudioSession.CategoryOptions
    }
    private var audio: AVAudioSession { .sharedInstance() }
    func configuration() -> Configuration {
      Configuration(
        category: audio.category, mode: audio.mode, policy: audio.routeSharingPolicy,
        options: audio.categoryOptions)
    }
    func describe(_ configuration: Configuration) -> String {
      "\(configuration.category.rawValue) \(configuration.mode.rawValue), "
        + "input available \(audio.isInputAvailable)"
    }
    /// As PlayerController's `configureAudioSession` sets it.
    func playbackConfiguration() -> Configuration {
      Configuration(category: .playback, mode: .default, policy: .longFormAudio, options: [])
    }
    func record() throws {
      try audio.setCategory(.playAndRecord, mode: .default, options: [.defaultToSpeaker])
    }
    func activate() throws { try audio.setActive(true) }
    func restore(_ configuration: Configuration) throws {
      try audio.setCategory(
        configuration.category, mode: configuration.mode, policy: configuration.policy,
        options: configuration.options)
    }
    func release() throws { try audio.setActive(false, options: .notifyOthersOnDeactivation) }
  }

  /// ShazamKit's managed session as the listening steps see it (#124).
  private final class ManagedShazam: ShazamListeningSession {
    let session: SHManagedSession
    init(_ session: SHManagedSession) { self.session = session }
    func prepare() async { await session.prepare() }
    func result() async -> SHSession.Result { await session.result() }
    func cancel() { session.cancel() }
    @MainActor var stateName: String {
      switch session.state {
      case .idle: "idle"
      case .prerecording: "prerecording"
      case .matching: "matching"
      }
    }
  }

  /// ShazamKit on the iPhone (D-70): listens only when asked, and keeps each
  /// identified song in the person's Shazam library, which the "Shazamed"
  /// list reads back. ShazamKit lets an app read only the songs it added
  /// itself, so songs found with the Shazam app don't appear.
  @MainActor @Observable public final class ShazamListener {
    public static let shared = ShazamListener()
    /// Songs identified since the app opened, newest first, in case the
    /// Shazam library hasn't caught up.
    public private(set) var identified: [ShazamSong] = []
    /// The last song couldn't be saved to the Shazam library.
    public private(set) var saveFailed = false
    /// Songs shared to PigMusic from the Shazam app or Apple Music (option C).
    public private(set) var shared: [ShazamSong] = []
    @ObservationIgnored private var session: SHManagedSession?
    @ObservationIgnored private var attempts = ShazamAttempts()
    @ObservationIgnored private var receiving = false
    /// The App Group shared with the share extension; nil until the App
    /// Groups capability is set up for both.
    @ObservationIgnored private let container = FileManager.default.containerURL(
      forSecurityApplicationGroupIdentifier: ShazamShareFolder.appGroup)

    public init() {
      if let container {
        shared = ShazamSharedSongs(file: ShazamShareFolder.songs(in: container)).load()
      }
    }

    /// Moves songs shared with the share extension into the "Shazamed" list:
    /// each looked up by its Shazam id in Shazam's catalogue, or by its Apple
    /// id in Apple Music (when the person allowed it) or through the server's
    /// `/search/resolve` (`server`, nil while signed out). Nothing is
    /// requested or downloaded.
    public func receiveShares(server: (@Sendable (Int) async throws -> ResolvedApple?)?) async {
      guard !receiving, let container else { return }
      receiving = true
      defer { receiving = false }
      shared = await ShazamShares.drain(
        queue: ShazamShareQueue(directory: ShazamShareFolder.queue(in: container)),
        store: ShazamSharedSongs(file: ShazamShareFolder.songs(in: container))
      ) { share in
        await Self.lookUp(share, server: server)
      }
    }

    private static func lookUp(
      _ share: ShazamShare, server: (@Sendable (Int) async throws -> ResolvedApple?)?
    ) async -> ShazamShareLookup {
      if let shazamId = share.shazamId {
        do {
          let item = try await SHMediaItem.fetch(shazamID: shazamId)
          return .song(await detailedSong(item, found: share.shared))
        } catch {
          if failure(error) == .offline { return .later }
          if share.appleId == nil { return .notFound }
        }
      }
      guard let appleId = share.appleId else { return .notFound }
      if let song = try? await AppleMusicCatalogue.song(appleId: appleId) {
        return .song(song)
      }
      guard let server else { return .later }
      do {
        guard let resolved = try await server(appleId) else { return .notFound }
        return .song(
          ShazamSong(
            shazamId: share.inboxId, appleId: appleId, title: resolved.name,
            artist: resolved.artist, album: resolved.album.flatMap { $0.isEmpty ? nil : $0 }))
      } catch {
        return .later
      }
    }

    /// Listens once, for at most `ShazamListening.limit`. The microphone
    /// permission is asked on the first try. `playbackHolds`: PigMusic's own
    /// audio session is active (playing or paused), asked when listening
    /// ends, so the session stays PigMusic's (#124).
    public func identify(
      playbackHolds: @escaping @MainActor @Sendable () -> Bool = { false }
    ) async -> Result<ShazamSong, ShazamFailure> {
      session?.cancel()
      let attempt = attempts.begin()
      let log: @MainActor @Sendable (String) -> Void = { line in
        DiagnosticsLog.shared.record("shazam", line)
      }
      // Reserved before the permission prompt: Play while it is up claims
      // this attempt, which then never records (Codex, #124).
      let gate = ListeningAudioGate.shared
      let reservation = gate.reserve()
      defer { gate.end(reservation) }
      switch AVAudioApplication.shared.recordPermission {
      case .granted: log("microphone allowed")
      case .undetermined:
        log("asking for the microphone")
        guard await AVAudioApplication.requestRecordPermission() else {
          log("microphone refused")
          return .failure(.microphone)
        }
        log("microphone allowed")
      default:
        log("microphone not allowed")
        return .failure(.microphone)
      }
      // The screen may have closed while the permission prompt was up: then
      // the microphone is never opened (Codex, build 21).
      guard !Task.isCancelled, attempts.isCurrent(attempt) else {
        log("closed before listening")
        return .failure(.cancelled)
      }
      let session = SHManagedSession()
      self.session = session
      defer { if self.session === session { self.session = nil } }
      let audio = ShazamAudioHooks.recording(
        SharedAudioSession(), playbackHolds: playbackHolds, log: log)
      let end = await ShazamListening.listen(
        ManagedShazam(session), audio: audio, gate: gate, reservation: reservation, log: log)
      guard !Task.isCancelled, attempts.isCurrent(attempt) else {
        session.cancel()
        return .failure(.cancelled)
      }
      let outcome: SHSession.Result
      switch end {
      case .finished(let result): outcome = result
      case .timedOut: return .failure(.timedOut)
      case .cancelled: return .failure(.cancelled)
      case .audioUnavailable: return .failure(.unavailable)
      case .playbackStarted: return .failure(.playbackStarted)
      }
      switch outcome {
      case .match(let match):
        guard let item = match.mediaItems.first else {
          log("match without a song")
          return .failure(.noMatch)
        }
        log("match")
        let song = await Self.detailedSong(item, found: Date())
        guard !Task.isCancelled, attempts.isCurrent(attempt) else { return .failure(.cancelled) }
        identified = ShazamInbox.merge([song] + identified)
        // Only songs with a Shazam id can be saved.
        if item.shazamID != nil {
          do {
            try await SHLibrary.default.addItems([item])
            saveFailed = false
          } catch {
            log("saving to the Shazam library failed: \(ShazamListening.describe(error))")
            saveFailed = true
          }
        }
        return .success(song)
      case .noMatch:
        log("no match")
        return .failure(.noMatch)
      case .error(let error, _):
        log("error \(ShazamListening.describe(error))")
        if Task.isCancelled || !attempts.isCurrent(attempt) { return .failure(.cancelled) }
        return .failure(Self.failure(error))
      }
    }

    public func cancel() {
      attempts.cancel()
      session?.cancel()
      session = nil
    }

    /// The "Shazamed" list: this session's songs, the shared ones and the
    /// Shazam library's, one row each, newest first. Reading it in a view
    /// follows the library.
    public var inbox: [ShazamSong] {
      let saved = SHLibrary.default.items.map { item in
        Self.song(item, found: item.creationDate)
      }
      return ShazamInbox.merge(identified + shared + saved)
    }

    static func failure(_ error: any Error) -> ShazamFailure {
      var network = false
      var current: NSError? = error as NSError
      var depth = 0
      while let found = current, depth < 8 {
        if found.domain == NSURLErrorDomain { network = true }
        current = found.userInfo[NSUnderlyingErrorKey] as? NSError
        depth += 1
      }
      let code = (error as NSError).domain == SHErrorDomain ? (error as NSError).code : nil
      return ShazamFailure(shazamCode: code, network: network)
    }

    private static func song(_ item: SHMediaItem, found: Date?) -> ShazamSong {
      ShazamSong(
        shazamId: item.shazamID ?? item.id.uuidString,
        appleId: item.appleMusicID.flatMap { Int($0) }, title: item.title ?? "",
        artist: item.artist ?? "", artworkURL: item.artworkURL, found: found)
    }

    /// With the album and length of Apple's song when ShazamKit has it, so
    /// the server tells versions apart as for an Apple playlist song.
    private static func detailedSong(_ item: SHMediaItem, found: Date?) async
      -> ShazamSong
    {
      var apple: MusicKit.Song?
      if #available(iOS 27.0, *) {
        apple = try? await item.songs().first
      } else {
        apple = item.songs.first
      }
      let base = song(item, found: found)
      return ShazamSong(
        shazamId: base.shazamId, appleId: base.appleId, title: base.title, artist: base.artist,
        album: apple?.albumTitle, duration: apple?.duration, artworkURL: base.artworkURL,
        found: found)
    }
  }
#endif
