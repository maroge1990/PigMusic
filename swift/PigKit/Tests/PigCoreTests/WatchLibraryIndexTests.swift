import Foundation
import PigCompanion
import PigDomain
import Testing

/// The Watch's Albums, Artists and Songs lists (#252, #255).
struct WatchLibraryIndexTests {
  private func song(
    _ id: String, _ title: String, artist: String, album: String, number: Int = 1
  ) -> CompanionSong {
    var track = Track(id: id, title: title, artist: artist, album: album, duration: 100)
    track.albumArtist = artist
    track.trackNumber = number
    return CompanionSong(track: track, checksum: "sum-" + id, bytes: 10)
  }

  private var library: [CompanionSong] {
    [
      song("1", "Sara", artist: "Fleetwood Mac", album: "Tusk", number: 2),
      song("2", "Come Together", artist: "the beatles", album: "abbey road"),
      song("3", "Over & Over", artist: "Fleetwood Mac", album: "Tusk", number: 1),
      song("4", "Rehab", artist: "Amy Winehouse", album: "Back to Black"),
      song("5", "another one", artist: "The Orchard", album: "The Sunrise"),
    ]
  }

  /// Albums sorted case-sensitively put "abbey road" after "Tusk" (#255).
  @Test func albumsSortIgnoringCaseAndALeadingThe() {
    let albums = WatchLibraryIndex.albums(library)
    #expect(albums.map(\.title) == ["abbey road", "Back to Black", "The Sunrise", "Tusk"])
    #expect(albums.last?.ids == ["3", "1"])
  }

  @Test func artistsSortTheSameWay() {
    let artists = WatchLibraryIndex.artists(WatchLibraryIndex.albums(library))
    #expect(artists.map(\.name) == ["Amy Winehouse", "the beatles", "Fleetwood Mac", "The Orchard"])
  }

  /// The Songs list is by title, not in the order songs arrived (#255).
  @Test func songsSortByTitle() {
    #expect(WatchLibraryIndex.songs(library) == ["5", "2", "3", "4", "1"])
  }

  /// Every count uses one rule for ready.
  @Test func oneRuleForReady() {
    let songs = Array(library.prefix(3))
    let manifest = CompanionManifest(
      context: CompanionContext(revision: 1, account: "a"), songs: songs)
    let receipt = CompanionReceipt(
      context: manifest.context, ready: ["1": "sum-1", "2": "stale"], usedBytes: 10,
      budgetBytes: WatchStorageFigures.gigabyte)
    #expect(songs.map { $0.isReady(in: receipt) } == [true, false, false])
    let status = WatchLibraryStatus(manifest: manifest, receipt: receipt, removing: ["3"])
    #expect(status.ready == 1)
    #expect(status.total == 2)
    #expect(status.readySeconds == 100)
  }
}
