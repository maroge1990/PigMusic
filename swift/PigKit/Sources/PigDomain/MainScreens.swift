import Foundation

/// The figures under a collection's title (D-120): "24 songs · 1 hr 32 min".
public enum CollectionFacts {
  /// "1 song", "1,284 songs".
  public static func songs(_ count: Int) -> String {
    count == 1 ? "1 song" : "\(count.formatted()) songs"
  }
  /// "14 min", "1 hr 32 min", "3 days 14 hr"; nil for no length.
  public static func length(_ seconds: Double) -> String? {
    guard seconds.isFinite, seconds > 0 else { return nil }
    let minutes = max(1, Int((seconds / 60).rounded()))
    if minutes < 60 { return "\(minutes) min" }
    let hours = minutes / 60
    if hours < 24 {
      let rest = minutes % 60
      return rest == 0 ? "\(hours) hr" : "\(hours) hr \(rest) min"
    }
    let days = hours / 24
    let rest = hours % 24
    let lead = days == 1 ? "1 day" : "\(days) days"
    return rest == 0 ? lead : "\(lead) \(rest) hr"
  }
  /// The count, then the length when the songs have one.
  public static func line(count: Int, seconds: Double) -> String {
    [songs(count), count == 0 ? nil : length(seconds)].compactMap { $0 }
      .joined(separator: " · ")
  }
}

extension Playlist {
  /// Its songs as the library has them, in playlist order.
  public var songs: [Track] { (entries ?? []).sorted { $0.position < $1.position }.map(\.playbackTrack) }
  /// "24 songs · 1 hr 32 min" (D-120). A list without its songs counts them
  /// from `trackCount` and leaves the length out.
  public var facts: String {
    guard let entries, !entries.isEmpty else {
      return CollectionFacts.songs(trackCount ?? 0)
    }
    return CollectionFacts.line(
      count: entries.count, seconds: entries.reduce(0) { $0 + ($1.duration ?? 0) })
  }
}

/// The cover of a playlist (D-120): four different album covers as a
/// mosaic, else the one cover it has, else none (a symbol is drawn).
public enum CoverMosaic {
  public static func pick<Item>(_ items: [Item], key: (Item) -> String?) -> [Item] {
    var seen = Set<String>()
    var found: [Item] = []
    for item in items {
      guard let key = key(item), seen.insert(key).inserted else { continue }
      found.append(item)
      if found.count == 4 { return found }
    }
    return Array(found.prefix(1))
  }
  /// Songs whose covers make the mosaic: one per album with artwork.
  public static func songs(_ tracks: [Track]) -> [Track] {
    pick(tracks) { track in
      guard track.coverSha != nil || track.libraryArtwork != nil || track.musicbrainzReleaseId != nil
      else { return nil }
      return track.albumId ?? track.coverSha ?? track.musicbrainzReleaseId ?? track.id
    }
  }
}

/// The Playlists page (D-120): Favourites always first, then the rest by name.
public enum PlaylistOrder {
  public static func shown(_ playlists: [Playlist]) -> [Playlist] {
    playlists.filter { $0.favourites == true }
      + playlists.filter { $0.favourites != true }.sorted {
        $0.name.sortingCompare($1.name) == .orderedAscending
      }
  }
}

/// A Home shelf shows only when it has something (D-120): one quiet
/// placeholder row while its first answer loads, otherwise nothing, never
/// loading or "Finding…" text, an explanation or an error.
public enum ShelfState: Equatable, Sendable {
  case shown, placeholder, hidden
  public init(hasItems: Bool, loading: Bool) {
    self = hasItems ? .shown : loading ? .placeholder : .hidden
  }
}

/// What an empty page offers (D-120): an icon, one short line and one action.
public enum EmptyPage: Equatable, Sendable {
  /// "Your library is empty" + "Find music".
  case library
  /// "No songs yet" + "Add songs".
  case playlist
  /// "Every song here is hidden" + "Show hidden songs".
  case mix
  /// "No playlists yet" + "New playlist", under Favourites.
  case playlists

  public var line: String {
    switch self {
    case .library: "Your library is empty"
    case .playlist: "No songs yet"
    case .mix: "Every song here is hidden"
    case .playlists: "No playlists yet"
    }
  }
  public var action: String {
    switch self {
    case .library: "Find music"
    case .playlist: "Add songs"
    case .mix: "Show hidden songs"
    case .playlists: "New playlist"
    }
  }
  /// A loaded library with no songs. Loading or unreachable is not empty.
  public static func library(songs: Int, loading: Bool, failed: Bool) -> EmptyPage? {
    songs == 0 && !loading && !failed ? .library : nil
  }
  /// A playlist read from the server with nothing in it.
  public static func playlist(songs: Int, loaded: Bool) -> EmptyPage? {
    songs == 0 && loaded ? .playlist : nil
  }
  /// A mix that has songs, every one of them hidden.
  public static func mix(songs: Int, shown: Int) -> EmptyPage? {
    songs > 0 && shown == 0 ? .mix : nil
  }
  /// No playlists besides Favourites.
  public static func playlists(_ playlists: [Playlist]) -> EmptyPage? {
    playlists.contains { $0.favourites != true } ? nil : .playlists
  }
}

/// Sort names that fit their tab (D-120): artists sort by name, albums and
/// songs by title; only albums sort by year.
public enum LibrarySortChoice {
  public static func options(_ collection: LibraryCollection) -> [LibrarySort] {
    switch collection {
    case .artists: [.title, .titleDescending]
    case .albums: [.title, .titleDescending, .artist, .year]
    case .tracks, .playlists: [.title, .titleDescending, .artist]
    }
  }
  public static func name(_ sort: LibrarySort, in collection: LibraryCollection) -> String {
    let noun = collection == .artists ? "Name" : "Title"
    switch sort {
    case .title: return noun + " A–Z"
    case .titleDescending: return noun + " Z–A"
    case .artist: return "Artist"
    case .year: return "Year"
    }
  }
}

/// A search result's one state mark (D-120): in your library, adding,
/// paused, or add (not on the server yet). Music on the server that streams
/// has none (D-116).
public enum FoundMark: Equatable, Sendable {
  case inLibrary, adding, paused, add, none
  public var label: String? {
    switch self {
    case .inLibrary: "In your library"
    case .adding: "Adding"
    case .paused: "Paused"
    case .add: "Not in your library"
    case .none: nil
    }
  }
}

extension FoundItem {
  public var mark: FoundMark {
    switch self {
    case .song(let track):
      switch track.serverState {
      case "requested": .adding
      case "paused": .paused
      case "household_available": .none
      case "not_in_library": .add
      default: .inLibrary
      }
    case .album(_, let mine), .artist(_, let mine): mine ? .inLibrary : .none
    case .playlist, .mix, .applePlaylist: .none
    case .appleSong, .appleAlbum, .catalogueArtist: .add
    }
  }
}

/// Search results grouped (D-120): one Top result, then Songs, Albums,
/// Artists and Playlists, each item once (the top result isn't repeated in
/// its group), in the screen's order.
public struct FoundGroups: Sendable {
  public static let songLimit = 6
  public let top: FoundItem?
  public let songs: [FoundItem]
  public let albums: [FoundItem]
  public let artists: [FoundItem]
  public let playlists: [FoundItem]
  public init(_ screen: FoundScreen) {
    let top = screen.top.first
    let rest = { (items: [FoundItem]) in items.filter { $0.id != top?.id } }
    self.top = top
    songs = rest(screen.songs)
    albums = rest(screen.albums)
    artists = rest(screen.artists)
    playlists = rest(screen.playlists)
  }
  public var isEmpty: Bool {
    top == nil && songs.isEmpty && albums.isEmpty && artists.isEmpty && playlists.isEmpty
  }
}

/// The "Include Apple Music results" row (D-120): shown while nobody has
/// been asked, unless this PigMusic account said Not now in the last 30
/// days. Another account on the iPhone still sees it.
public struct AppleMusicAsk: Codable, Sendable, Equatable {
  public static let quiet: TimeInterval = 30 * 24 * 60 * 60
  public private(set) var notNow: [String: Date] = [:]
  public init() {}
  public mutating func dismiss(account: String, now: Date = Date()) { notNow[account] = now }
  /// The Search menu's switch brings the row back.
  public mutating func reopen(account: String) { notNow[account] = nil }
  public func shows(account: String?, undetermined: Bool, now: Date = Date()) -> Bool {
    guard undetermined, let account else { return false }
    guard let dismissed = notNow[account] else { return true }
    return now.timeIntervalSince(dismissed) >= Self.quiet
  }
}

/// Songs' Play, Shuffle and a row's tap (D-120) play the whole library in
/// the chosen sort, not only the pages loaded so far, less songs waiting
/// for their removal's Undo.
public enum LibraryQueue {
  public static func make(
    _ all: [Track], removing: (Track) -> Bool, from trackId: String? = nil,
    shuffled: Bool = false
  ) -> (songs: [Track], index: Int) {
    let songs = all.filter { !removing($0) }
    if shuffled { return (songs.shuffled(), 0) }
    let index = trackId.flatMap { id in songs.firstIndex { $0.id == id } } ?? 0
    return (songs, index)
  }
}
