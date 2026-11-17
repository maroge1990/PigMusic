import Foundation

public enum HomeSection: String, CaseIterable, Sendable {
  case recent, popular, recommended, mixes
  /// The sections drawn from the person's library: a sync that changed the
  /// library or its artwork makes the app's kept answer for them stale, so
  /// the next Home visit asks again. Recently added is one of them (#168:
  /// it kept placeholder covers for half an hour after the library had
  /// them). Popular is the catalogue's chart and keeps its answer.
  public static let followsLibrary: Set<HomeSection> = [.recent, .recommended, .mixes]
}

public struct DiscoveryItem: Codable, Sendable, Identifiable, Hashable {
  public enum Kind: String, Codable, Sendable { case artists, albums, songs }
  public let id: String
  public let name: String
  public let artist: String
  public let artistId: String
  public let kind: Kind
  public let releaseId: String?
  public let album: String
  public let reason: String?

  // Catalogue recording identity must never be used as a saved-library file ID.
  public var playbackTrack: Track? {
    guard kind == .songs else { return nil }
    var track = Track(id: "catalogue:" + id, title: name, artist: artist, album: album)
    track.serverState = "not_in_library"
    track.musicbrainzRecordingId = id
    track.musicbrainzReleaseId = releaseId
    return track
  }
}

public struct DiscoveryFeed: Codable, Sendable {
  public enum State: String, Codable, Sendable {
    case ready
    case noSeeds = "no_seeds"
    case unavailable, preparing
  }
  public let artists: [DiscoveryItem]
  public let albums: [DiscoveryItem]
  public let songs: [DiscoveryItem]
  public let source: String
  public let unavailable: Bool
  public let title: String?
  public let state: State?
}

public struct HomeContent: Codable, Sendable {
  public let albums: [Album]
  public let feed: DiscoveryFeed?
  public let mixes: [PersonalMix]?
  public let mixesPreparing: Bool?
  public let radio: RadioCard?
  public init(
    albums: [Album] = [], feed: DiscoveryFeed? = nil, mixes: [PersonalMix]? = nil,
    mixesPreparing: Bool? = nil, radio: RadioCard? = nil
  ) {
    self.albums = albums
    self.feed = feed
    self.mixes = mixes
    self.mixesPreparing = mixesPreparing
    self.radio = radio
  }
}

/// The person's endless station, shown first among their mixes.
public struct RadioCard: Codable, Sendable, Hashable {
  public let title: String
  public let ready: Int
}

/// The next prepared radio songs; ids are ordinary `mix:` entries.
public struct RadioBatch: Codable, Sendable {
  public let id: String
  public let title: String
  public let tracks: [Track]
  public let preparing: Bool
}

public struct PersonalMix: Codable, Sendable, Identifiable {
  public let id: String
  public let kind: String
  public let day: String
  public let title: String
  public let reason: String
  public let tracks: [Track]
}

/// When Home may fetch catalogue albums ahead of a tap. Prefetching is a
/// nicety, so it waits for Wi-Fi or wired networks the person hasn't asked to
/// save data on, and for normal power.
public struct PrefetchConditions: Sendable, Equatable {
  public var lowPowerMode = false
  /// Low Data Mode (`NWPath.isConstrained`).
  public var lowDataMode = false
  /// Cellular or a personal hotspot (`NWPath.isExpensive` or a cellular interface).
  public var cellular = false
  public var connected = true
  public init(
    lowPowerMode: Bool = false, lowDataMode: Bool = false, cellular: Bool = false,
    connected: Bool = true
  ) {
    self.lowPowerMode = lowPowerMode
    self.lowDataMode = lowDataMode
    self.cellular = cellular
    self.connected = connected
  }
  public var allowsPrefetch: Bool { connected && !lowPowerMode && !lowDataMode && !cellular }
}
