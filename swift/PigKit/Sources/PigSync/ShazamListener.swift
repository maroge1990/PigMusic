#if os(iOS) && canImport(ShazamKit)
  import AVFAudio
  import Foundation
  import MusicKit
  import PigDomain
  import Observation
  import ShazamKit

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

    /// Listens once. The microphone permission is asked on the first try.
    public func identify() async -> Result<ShazamSong, ShazamFailure> {
      switch AVAudioApplication.shared.recordPermission {
      case .granted: break
      case .undetermined:
        guard await AVAudioApplication.requestRecordPermission() else {
          return .failure(.microphone)
        }
      default: return .failure(.microphone)
      }
      let session = SHManagedSession()
      self.session = session
      defer { if self.session === session { self.session = nil } }
      switch await session.result() {
      case .match(let match):
        guard let item = match.mediaItems.first else { return .failure(.noMatch) }
        let song = await Self.detailedSong(item, found: Date())
        guard !Task.isCancelled, self.session === session else { return .failure(.cancelled) }
        identified = ShazamInbox.merge([song] + identified)
        // Only songs with a Shazam id can be saved.
        if item.shazamID != nil {
          do {
            try await SHLibrary.default.addItems([item])
            saveFailed = false
          } catch {
            saveFailed = true
          }
        }
        return .success(song)
      case .noMatch:
        return .failure(.noMatch)
      case .error(let error, _):
        if Task.isCancelled || self.session !== session { return .failure(.cancelled) }
        return .failure(Self.failure(error))
      }
    }

    public func cancel() {
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
