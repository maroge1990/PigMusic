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
  public func playlists(query: String) async -> [ApplePlaylist] {
    guard await authorize() else { return [] }
    return (try? await find(query)) ?? []
  }
}

/// What the server is asked about an Apple playlist's songs: no new
/// endpoints, only the library matches and the ranked search.
public protocol ApplePlaylistStatusSource: Sendable {
  func lookupSongs(_ tracks: [Track]) async throws -> [Track?]
  func foundResults(query: String) async throws -> FoundResults
}
extension NativeService: ApplePlaylistStatusSource {}

public enum ApplePlaylistStatuses {
  /// The most songs a playlist screen checks against the server.
  public static let limit = 200
  /// Each song's place, in order: one library-matches call per 200 songs,
  /// then the ranked search for each song not in the library, a few at a
  /// time. Opening a playlist only reads; it never requests anything.
  public static func states(
    for songs: [ApplePlaylistSong], source: any ApplePlaylistStatusSource, concurrency: Int = 4
  ) async throws -> [ApplePlaylistSongState] {
    let songs = Array(songs.prefix(limit))
    var library: [Track?] = []
    for start in stride(from: 0, to: songs.count, by: 200) {
      let chunk = songs[start..<min(start + 200, songs.count)].map(\.lookupTrack)
      let found = try await source.lookupSongs(chunk)
      library += (0..<chunk.count).map { found.indices.contains($0) ? found[$0] : nil }
    }
    var states = songs.indices.map { index in
      library[index].map { ApplePlaylistSongState.library($0) }
    }
    let rest = songs.indices.filter { states[$0] == nil }
    try await withThrowingTaskGroup(of: (Int, ApplePlaylistSongState).self) { group in
      var next = 0
      func add() {
        guard next < rest.count else { return }
        let index = rest[next]
        next += 1
        let song = songs[index]
        group.addTask {
          let results = try await source.foundResults(query: song.serverQuery)
          return (
            index,
            ApplePlaylistMatching.state(for: song, libraryMatch: nil, serverResults: results)
          )
        }
      }
      for _ in 0..<max(1, concurrency) { add() }
      while let (index, state) = try await group.next() {
        states[index] = state
        add()
      }
    }
    return states.map { $0 ?? .elsewhere }
  }
}
