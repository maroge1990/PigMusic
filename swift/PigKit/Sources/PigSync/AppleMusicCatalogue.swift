#if os(iOS) && canImport(MusicKit)
  import Foundation
  import MusicKit
  import PigDomain

  /// Apple's MusicKit on the iPhone (D-66): iOS supplies the developer token
  /// once the app's identifier has the MusicKit service, and each person
  /// allows Apple Music access once. Names are qualified: MusicKit's `Track`
  /// and `Playlist` are not PigMusic's.
  public enum AppleMusicCatalogue {
    /// Search's Apple Music playlists, asking permission on the first search
    /// that would show them.
    public static let playlistSearch = ApplePlaylistSearch(
      authorize: { await authorized(asking: true) }, search: { try await playlists(query: $0) })

    /// Whether MusicKit may be used; asks once when nobody has been asked.
    public static func authorized(asking: Bool) async -> Bool {
      switch MusicAuthorization.currentStatus {
      case .authorized: return true
      case .notDetermined: return asking ? await MusicAuthorization.request() == .authorized : false
      default: return false
      }
    }

    static func playlists(query: String) async throws -> [ApplePlaylist] {
      var request = MusicCatalogSearchRequest(term: query, types: [MusicKit.Playlist.self])
      request.limit = 10
      return try await request.response().playlists.map { playlist in
        ApplePlaylist(
          id: playlist.id.rawValue, name: playlist.name, curator: playlist.curatorName,
          artworkURL: playlist.artwork?.url(width: 300, height: 300))
      }
    }

    /// A playlist and its songs, in Apple's order (music videos left out), up
    /// to the most a playlist screen checks.
    public static func songs(playlistId: String) async throws -> (
      ApplePlaylist, [ApplePlaylistSong]
    )? {
      guard await authorized(asking: false) else { return nil }
      let request = MusicCatalogResourceRequest<MusicKit.Playlist>(
        matching: \.id, equalTo: MusicItemID(playlistId))
      guard let found = try await request.response().items.first else { return nil }
      let playlist = try await found.with([.tracks])
      var tracks: [MusicKit.Track] = []
      var batch = playlist.tracks
      while let current = batch {
        tracks += current
        guard current.hasNextBatch, tracks.count < ApplePlaylistStatuses.limit else { break }
        batch = try await current.nextBatch()
      }
      var songs: [ApplePlaylistSong] = []
      for track in tracks.prefix(ApplePlaylistStatuses.limit) {
        guard case .song(let song) = track else { continue }
        songs.append(
          ApplePlaylistSong(
            position: songs.count, appleId: Int(song.id.rawValue), title: song.title,
            artist: song.artistName, album: song.albumTitle, duration: song.duration,
            artworkURL: song.artwork?.url(width: 120, height: 120)))
      }
      let info = ApplePlaylist(
        id: playlist.id.rawValue, name: playlist.name, curator: playlist.curatorName,
        artworkURL: playlist.artwork?.url(width: 600, height: 600))
      return (info, songs)
    }
  }
#endif
