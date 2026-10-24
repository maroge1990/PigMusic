import Foundation
import PigAPI
import PigDomain

public enum NativeFailure: Error, LocalizedError, Sendable, Equatable {
  case authentication, resync, changed
  case unavailable(String)
  /// The server answered that this one song cannot play (gone, no stream
  /// matched, remote streaming off). Other songs may still play.
  case songUnavailable(String)
  case invalidMediaURL
  public var errorDescription: String? {
    switch self {
    case .authentication: "Please sign in again. This device session is no longer available."
    case .resync: "Your library needs a fresh sync."
    case .changed: "Your library changed during sync. Please try again."
    case .unavailable(let message), .songUnavailable(let message): message
    case .invalidMediaURL: "The server returned an unsupported playback address."
    }
  }
}

extension Error {
  /// Whether this failure belongs to the song alone, so a queue may pass
  /// over it. An unreachable server or a server error fails every song:
  /// passing over then ran through the whole queue in seconds (22 Oct).
  public var isSongUnavailable: Bool {
    switch self as? NativeFailure {
    case .songUnavailable, .invalidMediaURL: true
    default: false
    }
  }
}

public protocol LibrarySource: Sendable {
  func bootstrap() async throws -> LibrarySnapshot
  func changes(since: String) async throws -> LibraryDelta
}

public actor NativeService: LibrarySource {
  let client: Client
  public let server: URL
  let vault: any DeviceTokenStore
  private let artworkTransport: ArtworkTransport
  private var playbackHistory: [String: String] = [:]
  /// Signed file addresses fetched ahead for the songs a page shows, so a tap
  /// goes straight to the file.
  private var addresses: [String: (url: URL, until: ContinuousClock.Instant)] = [:]
  /// Songs that stream from a remote source rather than a server file.
  private static let remoteStates: Set<String> = ["not_in_library", "requested", "failed"]
  public func qualifiedHistoryTrackID(_ id: String) -> String? {
    id.hasPrefix("mix:") ? id : playbackHistory[id]
  }
  public init(server: String, vault: any DeviceTokenStore) throws {
    self.server = try ServerAddress.validate(server)
    self.vault = vault
    artworkTransport = ArtworkTransport(server: self.server, vault: vault)
    client = try PigClient.make(server: server, vault: vault)
  }
  // Injectable generated client for isolated transport/fixture tests, not an app URL bypass.
  init(server: URL, client: Client, vault: any DeviceTokenStore) {
    self.server = server
    self.client = client
    self.vault = vault
    artworkTransport = ArtworkTransport(server: server, vault: vault)
  }
  public func artwork(_ request: ArtworkRequest) async throws -> Data {
    try await artworkTransport.fetch(request)
  }
  public func signIn(username: String, password: String, deviceName: String) async throws -> Person
  {
    let response = try await client.device_api_v1_auth_device_post(
      .init(
        body: .json(
          .init(device_name: deviceName, password: password, platform: .ios, username: username))))
    switch response {
    case .ok(let success):
      let session = try success.body.json
      try await vault.save(session.token)
      return try LibraryCodec.convert(session.user)
    case .unauthorized:
      throw NativeFailure.unavailable("The username or password was not accepted.")
    case .tooManyRequests:
      throw NativeFailure.unavailable("Too many sign-in attempts. Please wait and try again.")
    case .serviceUnavailable:
      throw NativeFailure.unavailable(
        "The administrator needs to finish server setup, or the server is unavailable.")
    default:
      throw NativeFailure.unavailable("Sign-in failed. Check the server address and try again.")
    }
  }
  public func me() async throws -> Person {
    switch try await client.me_api_v1_me_get(.init()) {
    case .ok(let value): return try LibraryCodec.convert(value.body.json)
    case .unauthorized: throw NativeFailure.authentication
    default:
      throw NativeFailure.unavailable(
        "Can't reach your server. Check your connection and try again.")
    }
  }
  /// Nil from a server older than this endpoint.
  public func serverInfo() async throws -> ServerInfo? {
    switch try await client.server_info_api_v1_server_get(.init()) {
    case .ok(let value): return try LibraryCodec.convert(value.body.json)
    case .unauthorized: throw NativeFailure.authentication
    default: return nil
    }
  }
  public func signOut() async throws {
    let result = try await client.logout_api_v1_auth_logout_post(.init())
    switch result {
    case .ok, .unauthorized: try await vault.clear()
    default:
      throw NativeFailure.unavailable("Couldn't revoke this session. Reconnect and try again.")
    }
  }

  public func home(_ section: HomeSection) async throws -> HomeContent {
    switch section {
    case .mixes:
      switch try await client.home_mixes_api_v1_home_mixes_get(.init()) {
      case .ok(let response):
        let value = try response.body.json
        return HomeContent(
          mixes: try value.mixes.map { try LibraryCodec.convert($0, to: PersonalMix.self) },
          mixesPreparing: value.preparing,
          radio: try value.radio.map { try LibraryCodec.convert($0, to: RadioCard.self) })
      case .unauthorized: throw NativeFailure.authentication
      default:
        throw NativeFailure.unavailable(
          "Your mixes are unavailable. Your library is still accessible.")
      }
    case .recent:
      switch try await client.recent_api_v1_home_recent_get(.init()) {
      case .ok(let response):
        return HomeContent(
          albums: try response.body.json.albums.map {
            try LibraryCodec.convert($0, to: Album.self)
          })
      case .unauthorized: throw NativeFailure.authentication
      default:
        throw NativeFailure.unavailable(
          "Recently added albums are unavailable. Your cached library is still accessible.")
      }
    case .popular:
      switch try await client.popular_api_v1_home_popular_get(.init()) {
      case .ok(let response): return HomeContent(feed: try LibraryCodec.convert(response.body.json))
      case .unauthorized: throw NativeFailure.authentication
      default: throw NativeFailure.unavailable("Popular music is unavailable. Try again shortly.")
      }
    case .recommended:
      switch try await client.recommended_api_v1_home_recommended_get(.init()) {
      case .ok(let response): return HomeContent(feed: try LibraryCodec.convert(response.body.json))
      case .unauthorized: throw NativeFailure.authentication
      default:
        throw NativeFailure.unavailable(
          "Recommendations are unavailable. Your library is still accessible.")
      }
    }
  }

  /// `name` lets the server mark the releases you own without a catalogue lookup.
  public func catalogueGroups(
    artistId: String, offset: Int = 0, kind: String? = nil, name: String = ""
  ) async throws -> CatalogueGroups {
    switch try await client.groups_api_v1_catalogue_artists__artist_id__releases_get(
      .init(
        path: .init(artist_id: artistId),
        query: .init(offset: offset, kind: kind, name: String(name.prefix(200)))))
    {
    case .ok(let response): return try LibraryCodec.convert(response.body.json)
    case .unauthorized: throw NativeFailure.authentication
    default:
      throw NativeFailure.unavailable("The artist catalogue is unavailable. Try again shortly.")
    }
  }
  public func cataloguePopularity(artistId: String) async throws -> CataloguePopularity {
    switch try await client.popular_artist_api_v1_catalogue_artists__artist_id__popularity_get(
      .init(path: .init(artist_id: artistId)))
    {
    case .ok(let value): return try LibraryCodec.convert(value.body.json)
    case .unauthorized: throw NativeFailure.authentication
    default: throw NativeFailure.unavailable("Popular music is unavailable.")
    }
  }
  public func catalogueProfile(artistId: String) async throws -> CatalogueProfile {
    switch try await client.artist_profile_api_v1_catalogue_artists__artist_id__profile_get(
      .init(path: .init(artist_id: artistId)))
    {
    case .ok(let value): return try LibraryCodec.convert(value.body.json)
    case .unauthorized: throw NativeFailure.authentication
    default: throw NativeFailure.unavailable("Artist details are unavailable.")
    }
  }
  public func artistIdentity(id: String) async throws -> ArtistIdentity {
    switch try await client.saved_identity_api_v1_catalogue_library_artists__artist_id__get(
      .init(path: .init(artist_id: id)))
    {
    case .ok(let value): return try LibraryCodec.convert(value.body.json)
    case .unauthorized: throw NativeFailure.authentication
    case .conflict:
      throw NativeFailure.unavailable(
        "Search the catalogue to choose between artists with the same name.")
    default: throw NativeFailure.unavailable("Artist identification is unavailable.")
    }
  }
  public func lookupSongs(_ tracks: [Track]) async throws -> [Track?] {
    guard !tracks.isEmpty, tracks.count <= 200 else { return [] }
    let songs = tracks.map {
      Components.Schemas.LookupSong(
        artist: $0.artist, musicbrainz_recording_id: $0.musicbrainzRecordingId, title: $0.title)
    }
    switch try await client.matches_api_v1_library_matches_post(
      .init(body: .json(.init(songs: songs))))
    {
    case .ok(let value):
      return try value.body.json.matches.map { item in
        try item.track.map { try LibraryCodec.convert($0, to: Track.self) }
      }
    case .unauthorized: throw NativeFailure.authentication
    default: throw NativeFailure.unavailable("Library matches are unavailable.")
    }
  }
  public func artistArtwork(id: String, request: ArtworkRequest) async throws -> Data {
    guard let uuid = UUID(uuidString: id) else { throw ArtworkFailure.invalidRequest }
    return try await artworkTransport.fetch(request, alias: .artist(uuid))
  }
  /// Catalogue release ("release") or release-group cover, fetched by the server.
  public func catalogueArtwork(kind: CatalogueArtworkKind, id: String, request: ArtworkRequest)
    async throws -> Data
  {
    guard let uuid = UUID(uuidString: id) else { throw ArtworkFailure.invalidRequest }
    return try await artworkTransport.fetch(
      request, alias: kind == .release ? .release(uuid) : .releaseGroup(uuid))
  }
  /// The artist's wide Fanart background; unavailable without a Fanart key.
  public func artistBackground(id: String, request: ArtworkRequest) async throws -> Data {
    guard let uuid = UUID(uuidString: id) else { throw ArtworkFailure.invalidRequest }
    return try await artworkTransport.fetch(request, alias: .background(uuid))
  }
  public func artistLogo(id: String) async throws -> Data {
    guard let uuid = UUID(uuidString: id) else { throw ArtworkFailure.invalidRequest }
    return try await artworkTransport.logo(artist: uuid)
  }
  public func catalogueEditions(groupId: String, offset: Int = 0) async throws -> CatalogueEditions
  {
    guard
      case .fresh(let value, _) = try await catalogueEditions(
        groupId: groupId, offset: offset, etag: nil)
    else { throw NativeFailure.unavailable("Album editions are unavailable. Try again shortly.") }
    return value
  }
  /// Sends `etag` as `If-None-Match`; `.notModified` means the saved copy is current.
  public func catalogueEditions(groupId: String, offset: Int, etag: String?) async throws
    -> Conditional<CatalogueEditions>
  {
    switch try await client.editions_api_v1_catalogue_groups__group_id__editions_get(
      .init(
        path: .init(group_id: groupId), query: .init(offset: offset),
        headers: .init(if_hyphen_none_hyphen_match: etag)))
    {
    case .ok(let response):
      return .fresh(try LibraryCodec.convert(response.body.json), etag: response.headers.ETag)
    case .notModified: return .notModified
    case .unauthorized: throw NativeFailure.authentication
    default: throw NativeFailure.unavailable("Album editions are unavailable. Try again shortly.")
    }
  }
  public func catalogueRelease(releaseId: String) async throws -> CatalogueRelease {
    guard case .fresh(let value, _) = try await catalogueRelease(releaseId: releaseId, etag: nil)
    else { throw NativeFailure.unavailable("This album is unavailable. Try again shortly.") }
    return value
  }
  /// Sends `etag` as `If-None-Match`; `.notModified` means the saved copy is current.
  public func catalogueRelease(releaseId: String, etag: String?) async throws
    -> Conditional<CatalogueRelease>
  {
    switch try await client.release_api_v1_catalogue_releases__release_id__get(
      .init(path: .init(release_id: releaseId), headers: .init(if_hyphen_none_hyphen_match: etag)))
    {
    case .ok(let response):
      let value = try response.body.json
      let songs = try value.tracks.map { item in
        var track: Track
        if let saved = item.saved_track {
          track = try LibraryCodec.convert(saved)
        } else if let household = item.household_track {
          track = try LibraryCodec.convert(household)
        } else {
          track = try LibraryCodec.convert(item)
          track.serverState = "not_in_library"
        }
        track.trackNumber = item.track_number
        track.discNumber = item.disc_number
        track.explicitEdition = item.explicit_edition == true ? true : nil
        track.recordingKind = item.recording_kind?.isEmpty == false ? item.recording_kind : nil
        return CatalogueSong(
          id: item.id, track: track, availability: item.server_state?.rawValue ?? "not_in_library")
      }
      let artistId = value.artist_id ?? ""
      return .fresh(
        CatalogueRelease(
          id: value.id, title: value.title, artist: value.artist,
          artistId: artistId.isEmpty ? nil : artistId, songs: songs),
        etag: response.headers.ETag)
    case .notModified: return .notModified
    case .unauthorized: throw NativeFailure.authentication
    default: throw NativeFailure.unavailable("This album is unavailable. Try again shortly.")
    }
  }

  public func searchServer(query: String, offset: Int = 0, revision: String? = nil) async throws
    -> ServerSearchPage
  {
    switch try await client.search_api_v1_search_get(
      .init(
        query: .init(q: SearchText.clean(query), offset: offset, limit: 100, revision: revision)))
    {
    case .ok(let response): return try LibraryCodec.convert(response.body.json)
    case .unauthorized: throw NativeFailure.authentication
    case .conflict: throw NativeFailure.changed
    default:
      throw NativeFailure.unavailable(
        "Server search is unavailable. Your cached results are still accessible.")
    }
  }
  public func catalogueArtists(query: String, offset: Int = 0) async throws -> CatalogueArtistPage {
    switch try await client.artists_api_v1_catalogue_artists_get(
      .init(query: .init(q: SearchText.clean(query), offset: offset, limit: 20)))
    {
    case .ok(let response): return try LibraryCodec.convert(response.body.json)
    case .unauthorized: throw NativeFailure.authentication
    default:
      throw NativeFailure.unavailable(
        "Catalogue search is unavailable. Your library is still accessible.")
    }
  }
  func input(_ track: Track) -> Components.Schemas.TrackInput {
    .init(
      album: track.album,
      album_artist: (track.albumArtist?.isEmpty == false ? track.albumArtist : track.artist),
      artist: track.artist, disc_number: track.discNumber, duration: track.duration,
      explicit_edition: track.explicitEdition,
      musicbrainz_recording_id: track.musicbrainzRecordingId,
      musicbrainz_release_id: track.musicbrainzReleaseId,
      recording_kind: track.recordingKind,
      title: track.title, track_number: track.trackNumber, year: track.year?.text)
  }
  public func favourite(_ track: Track, value: Bool? = nil) async throws -> Bool {
    switch try await client.favourite_api_v1_favourites_post(
      .init(body: .json(.init(favourite: value, track: input(track)))))
    {
    case .ok(let response): return try response.body.json.favourited
    case .unauthorized: throw NativeFailure.authentication
    default:
      throw NativeFailure.unavailable(
        "The favourite change could not be confirmed. Refresh before trying again.")
    }
  }
  public func addMembership(trackIds: [String]) async throws -> Int {
    switch try await client.add_api_v1_library_membership_post(
      .init(body: .json(.init(track_ids: trackIds))))
    {
    case .ok(let response): return try response.body.json.added
    case .unauthorized: throw NativeFailure.authentication
    default:
      throw NativeFailure.unavailable(
        "The library change could not be confirmed. Refresh before trying again.")
    }
  }
  public func requestMusic(_ tracks: [Track]) async throws -> AcquisitionResult {
    guard !tracks.isEmpty, tracks.count <= 500 else {
      throw NativeFailure.unavailable("Choose between one and 500 songs per request.")
    }
    switch try await client.request_tracks_api_v1_requests_post(
      .init(body: .json(.init(tracks: tracks.map(input)))))
    {
    case .ok(let response): return try LibraryCodec.convert(response.body.json)
    case .unauthorized: throw NativeFailure.authentication
    default:
      throw NativeFailure.unavailable(
        "The request could not be confirmed. Refresh Requests before trying again.")
    }
  }
  public func requests(offset: Int = 0, limit: Int = 200) async throws -> AcquisitionPage {
    switch try await client.requests_api_v1_requests_get(
      .init(query: .init(offset: offset, limit: limit)))
    {
    case .ok(let response): return try LibraryCodec.convert(response.body.json)
    case .unauthorized: throw NativeFailure.authentication
    default: throw NativeFailure.unavailable("Your requests are unavailable. Try again shortly.")
    }
  }
  /// Requests one per album request (lone songs alone), unfinished first.
  public func requestGroups(offset: Int = 0, limit: Int = 50) async throws -> RequestGroupPage {
    switch try await client.request_groups_api_v1_requests_groups_get(
      .init(query: .init(offset: offset, limit: limit)))
    {
    case .ok(let response): return try LibraryCodec.convert(response.body.json)
    case .unauthorized: throw NativeFailure.authentication
    default: throw NativeFailure.unavailable("Your requests are unavailable. Try again shortly.")
    }
  }
  public func clearFinishedRequests() async throws -> Int {
    switch try await client.clear_requests_api_v1_requests_delete(.init()) {
    case .ok(let response): return try response.body.json.cleared
    case .unauthorized: throw NativeFailure.authentication
    default:
      throw NativeFailure.unavailable(
        "The request history change could not be confirmed. Refresh before trying again.")
    }
  }

  public func bootstrap() async throws -> LibrarySnapshot {
    var snapshot = LibrarySnapshot(revision: "")
    var cursor: String?
    repeat {
      try Task.checkCancellation()
      switch try await client.tracks_api_v1_library_tracks_get(
        .init(query: .init(cursor: cursor, limit: 1000)))
      {
      case .ok(let success):
        let page = try success.body.json
        try check(page.revision, snapshot: &snapshot)
        snapshot.tracks += try page.items.map { try LibraryCodec.convert($0, to: Track.self) }
        cursor = page.next_cursor
      case .unauthorized: throw NativeFailure.authentication
      case .conflict: throw NativeFailure.changed
      default: throw NativeFailure.unavailable("Unable to sync songs.")
      }
    } while cursor != nil
    repeat {
      try Task.checkCancellation()
      switch try await client.albums_api_v1_library_albums_get(
        .init(query: .init(cursor: cursor, limit: 1000)))
      {
      case .ok(let success):
        let page = try success.body.json
        try check(page.revision, snapshot: &snapshot)
        snapshot.albums += try page.items.map { try LibraryCodec.convert($0, to: Album.self) }
        cursor = page.next_cursor
      case .unauthorized: throw NativeFailure.authentication
      case .conflict: throw NativeFailure.changed
      default: throw NativeFailure.unavailable("Unable to sync albums.")
      }
    } while cursor != nil
    repeat {
      try Task.checkCancellation()
      switch try await client.artists_api_v1_library_artists_get(
        .init(query: .init(cursor: cursor, limit: 1000)))
      {
      case .ok(let success):
        let page = try success.body.json
        try check(page.revision, snapshot: &snapshot)
        snapshot.artists += try page.items.map { try LibraryCodec.convert($0, to: Artist.self) }
        cursor = page.next_cursor
      case .unauthorized: throw NativeFailure.authentication
      case .conflict: throw NativeFailure.changed
      default: throw NativeFailure.unavailable("Unable to sync artists.")
      }
    } while cursor != nil
    switch try await client.playlists_api_v1_playlists_get(.init()) {
    case .ok(let success):
      for summary in try success.body.json.playlists {
        switch try await client.playlist_api_v1_playlists__playlist_id__get(
          .init(path: .init(playlist_id: summary.id)))
        {
        case .ok(let detail): snapshot.playlists.append(try LibraryCodec.convert(detail.body.json))
        case .notFound: throw NativeFailure.changed
        case .unauthorized: throw NativeFailure.authentication
        default: throw NativeFailure.unavailable("Unable to sync playlists.")
        }
      }
    case .unauthorized: throw NativeFailure.authentication
    default: throw NativeFailure.unavailable("Unable to sync playlists.")
    }
    // Close the read/mutation window before publishing any bootstrap data.
    let delta = try await changes(since: snapshot.revision)
    snapshot.tracks = merge(
      snapshot.tracks, upserts: delta.upserts.tracks, deletes: delta.deletes.tracks)
    snapshot.albums = merge(
      snapshot.albums, upserts: delta.upserts.albums, deletes: delta.deletes.albums)
    snapshot.artists = merge(
      snapshot.artists, upserts: delta.upserts.artists, deletes: delta.deletes.artists)
    snapshot.playlists = merge(
      snapshot.playlists, upserts: delta.upserts.playlists, deletes: delta.deletes.playlists)
    snapshot.revision = delta.revision
    return snapshot
  }
  private func check(_ revision: String, snapshot: inout LibrarySnapshot) throws {
    if snapshot.revision.isEmpty { snapshot.revision = revision }
    guard snapshot.revision == revision else { throw NativeFailure.changed }
  }
  private func merge<T: Identifiable>(_ current: [T], upserts: [T], deletes: [String]) -> [T]
  where T.ID == String {
    var values: [String: T] = [:]
    for value in current { values[value.id] = value }
    for id in deletes { values.removeValue(forKey: id) }
    for value in upserts { values[value.id] = value }
    return Array(values.values)
  }
  public func changes(since: String) async throws -> LibraryDelta {
    switch try await client.changes_api_v1_library_changes_get(.init(query: .init(since: since))) {
    case .ok(let success): return try LibraryCodec.convert(success.body.json)
    case .gone: throw NativeFailure.resync
    case .unauthorized: throw NativeFailure.authentication
    default: throw NativeFailure.unavailable("Unable to refresh your library.")
    }
  }
  /// A playback address on this server, or nil for anything else.
  private func serverMediaURL(_ value: String) -> URL? {
    guard let url = URL(string: value, relativeTo: server)?.absoluteURL,
      url.scheme == server.scheme, url.host == server.host, url.port == server.port,
      url.user == nil, url.password == nil
    else { return nil }
    return url
  }
  /// An address fetched ahead, while it has at least half an hour left.
  private func preparedAddress(_ trackId: String) -> URL? {
    guard let entry = addresses[trackId], entry.until > .now + .seconds(30 * 60) else {
      return nil
    }
    return entry.url
  }
  /// Signs the server files among `tracks` in one request (best effort), so
  /// playing any of them skips asking for its address.
  public func preparePlayback(_ tracks: [Track]) async {
    var seen = Set<String>()
    let wanted = tracks.filter {
      !$0.id.hasPrefix("mix:") && !Self.remoteStates.contains($0.serverState ?? "")
        && preparedAddress($0.id) == nil && seen.insert($0.id).inserted
    }.prefix(200).map(\.id)
    guard !wanted.isEmpty else { return }
    let asked = ContinuousClock.now
    guard
      case .ok(let success) = try? await client.playback_urls_api_v1_playback_urls_post(
        .init(body: .json(.init(track_ids: Array(wanted))))),
      let result = try? success.body.json
    else { return }
    let until = asked + .seconds(result.expires_in)
    if addresses.count > 4000 { addresses = addresses.filter { $0.value.until > .now } }
    for (id, value) in result.urls.additionalProperties {
      if let url = serverMediaURL(value) { addresses[id] = (url, until) }
    }
  }
  public func playbackURL(trackId: String) async throws -> URL {
    if let prepared = preparedAddress(trackId) { return prepared }
    let asked = ContinuousClock.now
    switch try await client.playback_url_api_v1_playback__track_id__url_post(
      .init(path: .init(track_id: trackId)))
    {
    case .ok(let success):
      let result = try success.body.json
      guard let url = serverMediaURL(result.url) else { throw NativeFailure.invalidMediaURL }
      // Mix songs are signed for each play: their address renews the mix's lease.
      if !trackId.hasPrefix("mix:") {
        addresses[trackId] = (url, asked + .seconds(result.expires_in))
      }
      return url
    case .unauthorized: throw NativeFailure.authentication
    case .notFound:
      throw NativeFailure.songUnavailable("This song is no longer available on the server.")
    default: throw NativeFailure.unavailable("Unable to prepare this song for playback.")
    }
  }
  private func remoteInput(_ track: Track) -> Components.Schemas.TrackInput {
    .init(
      album: track.album, album_artist: track.albumArtist, artist: track.artist,
      disc_number: track.discNumber, duration: track.duration,
      musicbrainz_recording_id: track.musicbrainzRecordingId,
      musicbrainz_release_id: track.musicbrainzReleaseId, title: track.title,
      track_number: track.trackNumber)
  }
  /// Songs that need remote streaming soon; the server fetches slow sources
  /// ahead so they start at once. Best effort: failures are ignored.
  public func prepareRemote(_ tracks: [Track]) async {
    let wanted = tracks.filter {
      !$0.id.hasPrefix("mix:")
        && ["not_in_library", "requested", "failed"].contains($0.serverState ?? "")
    }.prefix(3)
    guard !wanted.isEmpty else { return }
    _ = try? await client.prepare_remote_api_v1_remote_playback_prepare_post(
      .init(body: .json(.init(tracks: wanted.map(remoteInput)))))
  }
  public func resolve(_ track: Track) async throws -> URL {
    if track.id.hasPrefix("mix:") { return try await playbackURL(trackId: track.id) }
    // Household files stream like your own; playing never adds them to your library.
    // Requested songs have no file yet and stream like the catalogue.
    if !Self.remoteStates.contains(track.serverState ?? "") {
      return try await playbackURL(trackId: track.id)
    }
    let response = try await client.playback_remote_api_v1_remote_playback_post(
      .init(body: .json(remoteInput(track))))
    struct Remote: Decodable {
      let url: String
      let historyTrackId: String?
    }
    switch response {
    case .ok(let success):
      let remote: Remote = try LibraryCodec.convert(success.body.json)
      // JioSaavn answers with its own HTTPS address; other sources are relayed
      // by the server as a signed path on this origin.
      guard let url = URL(string: remote.url, relativeTo: server)?.absoluteURL,
        url.scheme == "https", url.host != nil, url.user == nil, url.password == nil
      else { throw NativeFailure.invalidMediaURL }
      if let identity = remote.historyTrackId, identity.hasPrefix("mix:") {
        if playbackHistory.count >= 2048 { playbackHistory.removeAll() }
        playbackHistory[track.id] = identity
      }
      return url
    case .unauthorized: throw NativeFailure.authentication
    case .conflict:
      throw NativeFailure.songUnavailable(
        "Remote streaming is disabled. You can still play saved songs or request this song in Song actions."
      )
    case .notFound:
      throw NativeFailure.songUnavailable(
        "No reliable remote stream matched this song. Saved music is still available.")
    default:
      throw NativeFailure.unavailable(
        "Remote streaming is unavailable. Try again or play a saved song.")
    }
  }
}
