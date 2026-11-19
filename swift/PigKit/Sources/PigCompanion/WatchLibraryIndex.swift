import Foundation
import PigDomain

/// An album on the Watch: its songs in disc and track order.
public struct WatchAlbum: Identifiable, Equatable, Sendable {
  public let id: String
  public let title: String
  public let artist: String
  public let ids: [String]
  public init(id: String, title: String, artist: String, ids: [String]) {
    self.id = id
    self.title = title
    self.artist = artist
    self.ids = ids
  }
}

/// An album artist on the Watch, with their albums.
public struct WatchArtist: Identifiable, Equatable, Sendable {
  public let id: String
  public let name: String
  public let albums: [WatchAlbum]
  public var ids: [String] { albums.flatMap(\.ids) }
}

/// The Watch library's lists (D-120, #252): albums, artists and songs A to
/// Z the way the iPhone files them, ignoring case and a leading "The".
public enum WatchLibraryIndex {
  public static func albums(_ songs: [CompanionSong]) -> [WatchAlbum] {
    var grouped: [String: [CompanionSong]] = [:]
    var order: [String] = []
    for song in songs {
      let artist = song.track.albumArtist ?? song.track.artist
      let title = song.track.album ?? "Singles"
      let key = song.track.albumId ?? (artist + "\u{0}" + title)
      if grouped[key] == nil { order.append(key) }
      grouped[key, default: []].append(song)
    }
    let result: [WatchAlbum] = order.compactMap { key in
      guard let songs = grouped[key], let first = songs.first else { return nil }
      let ordered = songs.sorted { left, right in
        let leftDisc = left.track.discNumber ?? 1
        let rightDisc = right.track.discNumber ?? 1
        if leftDisc != rightDisc { return leftDisc < rightDisc }
        let leftTrack = left.track.trackNumber ?? 0
        let rightTrack = right.track.trackNumber ?? 0
        if leftTrack != rightTrack { return leftTrack < rightTrack }
        return left.id < right.id
      }
      return WatchAlbum(
        id: key, title: first.track.album ?? "Singles",
        artist: first.track.albumArtist ?? first.track.artist, ids: ordered.map(\.id))
    }
    return result.sorted {
      WatchListOrder.precedes(($0.title, $0.artist, $0.id), ($1.title, $1.artist, $1.id))
    }
  }

  /// Album artists, each with their albums in the same order.
  public static func artists(_ albums: [WatchAlbum]) -> [WatchArtist] {
    Dictionary(grouping: albums, by: \.artist).map { name, albums in
      WatchArtist(id: name, name: name, albums: albums)
    }.sorted { WatchListOrder.precedes(($0.name, "", $0.id), ($1.name, "", $1.id)) }
  }

  /// Every song by title, then artist.
  public static func songs(_ songs: [CompanionSong]) -> [String] {
    songs.sorted {
      WatchListOrder.precedes(
        ($0.track.title, $0.track.artist, $0.id), ($1.track.title, $1.track.artist, $1.id))
    }.map(\.id)
  }
}

extension WatchLibraryStatus {
  /// The status of the Watch's library as its archive reports it.
  public init(
    manifest: CompanionManifest, receipt: CompanionReceipt?, removing: Set<String> = [],
    problems: [String: WatchSaveProblem] = [:], iPhoneReachable: Bool? = nil
  ) {
    self.init(
      songs: manifest.songs.map {
        WatchSongState(
          id: $0.id, checksum: $0.checksum, bytes: $0.bytes, seconds: $0.track.duration ?? 0)
      },
      stored: receipt?.ready, usedBytes: receipt?.usedBytes ?? 0,
      limitBytes: receipt?.budgetBytes ?? WatchStorageFigures.gigabyte, removing: removing,
      problems: problems, iPhoneReachable: iPhoneReachable,
      manifestApplied: receipt?.manifestApplied ?? false)
  }
}

extension CompanionSong {
  /// Ready on the Watch, by the one rule every count uses.
  public func isReady(in receipt: CompanionReceipt?) -> Bool {
    WatchSongState.isReady(id: id, checksum: checksum, stored: receipt?.ready)
  }
}
