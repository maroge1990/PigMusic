import Foundation
import PigDomain

/// Apple Music's catalogue playlists, searched on the phone (D-66). The app
/// supplies MusicKit's authorization and search; anything short of an
/// authorized, working search (denied, restricted, no MusicKit service for
/// the app yet, a simulator, an error) is simply no playlists.
public struct ApplePlaylistSearch: Sendable {
  private let authorize: @Sendable () async -> Bool
  private let find: @Sendable (String) async throws -> [ApplePlaylist]
  /// `authorize` is asked before each search, so permission is requested on
  /// the first search that would show Apple playlists, never at launch.
  public init(
    authorize: @escaping @Sendable () async -> Bool,
    search: @escaping @Sendable (String) async throws -> [ApplePlaylist]
  ) {
    self.authorize = authorize
    self.find = search
  }
  public func playlists(query: String) async -> ApplePlaylistAnswer {
    guard await authorize() else { return .notAuthorized }
    do {
      return .found(try await find(query))
    } catch {
      if error is CancellationError || Task.isCancelled { return .cancelled }
      let error = error as NSError
      return .failed("\(error.domain) \(error.code)")
    }
  }
}

/// One Apple Music playlist search. Only playlists found are kept for the
/// words: none, a refusal or a failure is asked again next time (#125: a
/// failed or cancelled answer hid "rock"'s playlists for minutes).
public enum ApplePlaylistAnswer: Sendable, Hashable {
  case found([ApplePlaylist])
  case notAuthorized
  case failed(String)
  case cancelled
  public var playlists: [ApplePlaylist] {
    if case .found(let playlists) = self { playlists } else { [] }
  }
  /// Worth reusing for the same words.
  public var keep: Bool { !playlists.isEmpty }
  /// For the diagnostics: never the words themselves, only their length.
  public func diagnostics(termLength: Int) -> String {
    let outcome =
      switch self {
      case .found(let playlists): "\(playlists.count) found"
      case .notAuthorized: "Apple Music not allowed"
      case .failed(let error): "failed: \(error)"
      case .cancelled: "cancelled"
      }
    return "apple playlists, \(termLength) characters: \(outcome)"
  }
}

/// What the server is asked about an Apple playlist's songs: whether each is
/// in the person's library, elsewhere on the server, or not there, decided
/// by the server's shared matching policy (`/server/matches`).
public protocol ApplePlaylistStatusSource: Sendable {
  func serverMatches(_ tracks: [Track]) async throws -> [Track?]
}
extension NativeService: ApplePlaylistStatusSource {}

public enum ApplePlaylistStatuses {
  /// The most songs a playlist screen shows and checks.
  public static let limit = 200
  /// The server's answer per request (`/server/matches` takes 1–200 songs).
  public static let chunk = 200
  /// Each song's state, in order, from one server call per 200 songs, kept
  /// with the songs they belong to. Opening a playlist only reads; it never
  /// requests anything.
  public static func states(
    for songs: [ApplePlaylistSong], source: any ApplePlaylistStatusSource
  ) async throws -> ApplePlaylistStates {
    // The server refuses an empty title or artist: such a song isn't asked.
    let asked = songs.indices.filter {
      !songs[$0].title.trimmingCharacters(in: .whitespaces).isEmpty
        && !songs[$0].artist.trimmingCharacters(in: .whitespaces).isEmpty
    }
    var states = [ApplePlaylistSongState](repeating: .elsewhere, count: songs.count)
    for start in stride(from: 0, to: asked.count, by: chunk) {
      let indices = Array(asked[start..<min(start + chunk, asked.count)])
      let found = try await source.serverMatches(indices.map { songs[$0].lookupTrack })
      // A short or long answer is not trusted to line up with the songs.
      guard found.count == indices.count else {
        throw NativeFailure.unavailable("The server's song matches didn't line up.")
      }
      for (index, match) in zip(indices, found) {
        states[index] = ApplePlaylistSongState(match: match)
      }
    }
    return ApplePlaylistStates(songs: songs, states: states)
  }
}

/// Where "Add to my playlists" goes (#121): the server's Apple Music import,
/// which makes or updates the person's own copy (D-69).
public protocol AppleImportSource: Sendable {
  func importApplePlaylist(_ request: AppleImportRequest) async throws -> (
    Playlist, AppleImportCounts
  )
}
extension NativeService: AppleImportSource {}

public enum AppleImports {
  /// Adds the playlist as shown (at most `AppleImport.limit` songs, in
  /// Apple's order). Missing songs are requested only with `downloadMissing`,
  /// which the screen asks the person to confirm first.
  public static func add(
    _ playlist: ApplePlaylist, songs: [ApplePlaylistSong], moreThanShown: Bool = false,
    downloadMissing: Bool, source: any AppleImportSource
  ) async throws -> AppleImportResult {
    let request = AppleImport.request(
      playlist: playlist, songs: songs, moreThanShown: moreThanShown,
      downloadMissing: downloadMissing)
    guard !request.songs.isEmpty else {
      throw NativeFailure.unavailable("This playlist has no songs PigMusic can add.")
    }
    let (added, counts) = try await source.importApplePlaylist(request)
    return AppleImportResult(playlist: added, counts: counts, capped: request.capped)
  }
}
