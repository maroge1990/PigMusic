import Foundation

/// A song identified with Shazam in PigMusic (D-70): what ShazamKit names,
/// before the server is asked whether it has it.
public struct ShazamSong: Codable, Sendable, Hashable, Identifiable {
  /// Shazam's own id for the song; the inbox keeps one row per id.
  public let shazamId: String
  /// Apple's catalogue id, the one the server resolves (`/search/resolve`).
  public let appleId: Int?
  public let title: String
  public let artist: String
  public let album: String?
  public let duration: Double?
  public let artworkURL: URL?
  /// When it was identified; unknown dates sort last.
  public let found: Date?
  public var id: String { shazamId }
  public init(
    shazamId: String, appleId: Int?, title: String, artist: String, album: String? = nil,
    duration: Double? = nil, artworkURL: URL? = nil, found: Date? = nil
  ) {
    self.shazamId = shazamId
    self.appleId = appleId
    self.title = title
    self.artist = artist
    self.album = album
    self.duration = duration
    self.artworkURL = artworkURL
    self.found = found
  }
  /// The same song with another date, for the inbox's merge.
  func found(_ date: Date?) -> ShazamSong {
    ShazamSong(
      shazamId: shazamId, appleId: appleId, title: title, artist: artist, album: album,
      duration: duration, artworkURL: artworkURL, found: date)
  }
  /// Asked of the server like an Apple playlist song, so both share the
  /// server's matching policy and the states keyed by the ordered Apple ids.
  public func playlistSong(position: Int) -> ApplePlaylistSong {
    ApplePlaylistSong(
      position: position, appleId: appleId, title: title, artist: artist, album: album,
      duration: duration, artworkURL: artworkURL)
  }
  /// Whether the server can be asked about it: it refuses a blank title or artist.
  public var askable: Bool {
    !title.trimmingCharacters(in: .whitespaces).isEmpty
      && !artist.trimmingCharacters(in: .whitespaces).isEmpty
  }
  /// The request for this song once the server has resolved its Apple id to
  /// the exact MusicBrainz recording (`/search/resolve`): that recording and
  /// release, never a guess from the title.
  public func requestTrack(_ resolved: ResolvedApple) -> Track {
    var track = Track(
      id: "shazam:" + shazamId, title: resolved.name, artist: resolved.artist,
      album: resolved.album.flatMap { $0.isEmpty ? nil : $0 } ?? album, duration: duration)
    track.musicbrainzRecordingId = resolved.id
    track.musicbrainzReleaseId = resolved.releaseId
    track.serverState = "not_in_library"
    return track
  }
}

/// The "Shazamed" list: songs identified in PigMusic, newest first.
public enum ShazamInbox {
  /// One row per song (by Shazam id, or Apple id, so a song shared from
  /// Apple Music joins its Shazam row): the first copy's details, which the
  /// caller puts first (this session's, with album and length), and the
  /// latest date any copy was found; newest first, undated last.
  public static func merge(_ songs: [ShazamSong]) -> [ShazamSong] {
    var merged: [ShazamSong] = []
    var index: [String: Int] = [:]
    for song in songs {
      let keys = ["shazam:" + song.shazamId] + (song.appleId.map { ["apple:\($0)"] } ?? [])
      guard let at = keys.lazy.compactMap({ index[$0] }).first else {
        for key in keys { index[key] = merged.count }
        merged.append(song)
        continue
      }
      for key in keys where index[key] == nil { index[key] = at }
      let earlier = merged[at]
      if let date = song.found, earlier.found.map({ date > $0 }) ?? true {
        merged[at] = earlier.found(date)
      }
    }
    // Sorting by place breaks date ties in the given order.
    return merged.indices.sorted { a, b in
      switch (merged[a].found, merged[b].found) {
      case (let x?, let y?): x != y ? x > y : a < b
      case (.some, nil): true
      case (nil, .some): false
      case (nil, nil): a < b
      }
    }.map { merged[$0] }
  }
  /// The songs as the server is asked about them, in the inbox's order.
  public static func playlistSongs(_ songs: [ShazamSong]) -> [ApplePlaylistSong] {
    songs.enumerated().map { $0.element.playlistSong(position: $0.offset) }
  }
  /// The songs "Download all missing" requests: checked and not on the
  /// server, with an Apple id the server can resolve to an exact version.
  /// Nothing while the states don't belong to exactly these songs.
  public static func missing(_ songs: [ShazamSong], states: ApplePlaylistStates?)
    -> [ShazamSong]
  {
    guard let states = states?.states(for: playlistSongs(songs)) else { return [] }
    return zip(songs, states).compactMap { song, state in
      state == .elsewhere && song.appleId != nil && song.askable ? song : nil
    }
  }
}

/// Why "What's playing?" found nothing, in words for the person.
public enum ShazamFailure: Error, Sendable, Hashable {
  case microphone
  case noMatch
  /// Nothing came back within the listening limit (#124).
  case timedOut
  case offline
  case unavailable
  case cancelled
  /// PigMusic started playing, which stops listening (#124).
  case playbackStarted
  public var message: String {
    switch self {
    case .microphone:
      "PigMusic can't use the microphone. Allow it in Settings › Apps › PigMusic to identify songs."
    case .noMatch: "No match. Try again closer to the music."
    case .timedOut: "Couldn't identify the song. Try again closer to the music."
    case .offline: "Identifying a song needs the internet. Check your connection and try again."
    case .unavailable: "Song recognition isn't working right now. Try again shortly."
    case .cancelled: "Stopped listening."
    case .playbackStarted: "Stopped listening because PigMusic started playing."
    }
  }
  /// A failed attempt, from ShazamKit's error code (`SHError.Code`, nil for
  /// another kind of error) and whether a network error is anywhere in the
  /// error's chain.
  public init(shazamCode: Int?, network: Bool) {
    if network {
      self = .offline
    } else if shazamCode == 200 || shazamCode == 201 {
      // signatureInvalid (usually silence) or too short: nothing to match.
      self = .noMatch
    } else {
      self = .unavailable
    }
  }
}

/// One "What's playing?" attempt at a time. Closing the listening screen
/// ends the current attempt, so a microphone permission granted afterwards,
/// or a result arriving late, never starts or reports listening the person
/// no longer sees.
public struct ShazamAttempts: Sendable {
  private var current = 0
  public init() {}
  /// Starts an attempt and returns its token.
  public mutating func begin() -> Int {
    current += 1
    return current
  }
  /// Ends whatever attempt is running.
  public mutating func cancel() { current += 1 }
  /// Whether `token`'s attempt is still the one the person is waiting on.
  public func isCurrent(_ token: Int) -> Bool { token == current }
}
