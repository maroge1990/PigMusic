import Foundation
import PigDomain
import Testing

private func decode<T: Decodable>(_ json: String) -> T {
  try! LibraryCodec.decoder().decode(T.self, from: Data(json.utf8))
}

private func playlist(_ id: String, _ name: String, favourites: Bool = false, entries: String = "")
  -> Playlist
{
  decode(
    #"{"id":"\#(id)","name":"\#(name)","revision":1,"favourites":\#(favourites),"entries":[\#(entries)]}"#
  )
}

private func entry(_ id: String, album: String, seconds: Double, position: Int) -> String {
  #"{"entry_id":"\#(id)","title":"\#(id)","artist":"A","position":\#(position),"duration":\#(seconds),"#
    + #""saved_track":{"id":"\#(id)","title":"\#(id)","artist":"A","album_id":"\#(album)","cover_sha":"\#(album)"}}"#
}

/// D-120: Favourites is always first among playlists, the rest by name.
@Test func favouritesIsAlwaysTheFirstPlaylist() {
  let shown = PlaylistOrder.shown([
    playlist("b", "Road Trip"), playlist("f", "Favourites", favourites: true),
    playlist("a", "Kitchen Disco"),
  ])
  #expect(shown.map(\.id) == ["f", "a", "b"])
  // Only Favourites: the page offers New playlist.
  #expect(EmptyPage.playlists([playlist("f", "Favourites", favourites: true)]) == .playlists)
  #expect(EmptyPage.playlists(shown) == nil)
  #expect(EmptyPage.playlists.action == "New playlist")
}

/// D-120: a playlist row and page read "24 songs · 1 hr 32 min".
@Test func aCollectionSaysHowManySongsAndHowLong() {
  #expect(CollectionFacts.line(count: 24, seconds: 92 * 60) == "24 songs · 1 hr 32 min")
  #expect(CollectionFacts.line(count: 3, seconds: 14 * 60) == "3 songs · 14 min")
  #expect(CollectionFacts.line(count: 1, seconds: 200) == "1 song · 3 min")
  #expect(
    CollectionFacts.line(count: 1284, seconds: (3 * 24 + 14) * 3600) == "1,284 songs · 3 days 14 hr")
  #expect(CollectionFacts.line(count: 0, seconds: 0) == "0 songs")
  #expect(CollectionFacts.length(3600) == "1 hr")
  let mix = playlist(
    "p", "Mix",
    entries: [
      entry("1", album: "x", seconds: 600, position: 1),
      entry("2", album: "y", seconds: 240, position: 0),
    ].joined(separator: ","))
  #expect(mix.facts == "2 songs · 14 min")
  #expect(mix.songs.map(\.id) == ["2", "1"])
}

/// D-120: a playlist's cover is four different album covers, else its one cover.
@Test func aPlaylistCoverIsAMosaicOfFourAlbumsOrItsOneCover() {
  let four = ["a", "a", "b", "c", "d", "e"].enumerated().map { index, album in
    entry(String(index), album: album, seconds: 1, position: index)
  }
  #expect(
    CoverMosaic.songs(playlist("p", "P", entries: four.joined(separator: ",")).songs).map(\.albumId)
      == ["a", "b", "c", "d"])
  let two = [entry("1", album: "a", seconds: 1, position: 0), entry("2", album: "b", seconds: 1, position: 1)]
  #expect(
    CoverMosaic.songs(playlist("p", "P", entries: two.joined(separator: ",")).songs).map(\.albumId)
      == ["a"])
  #expect(CoverMosaic.songs([]).isEmpty)
}

/// D-120: a Home shelf shows one placeholder row while loading, then its
/// music or nothing at all.
@Test func aHomeShelfShowsOnlyWhenItHasSomething() {
  #expect(ShelfState(hasItems: true, loading: true) == .shown)
  #expect(ShelfState(hasItems: false, loading: true) == .placeholder)
  #expect(ShelfState(hasItems: false, loading: false) == .hidden)
}

/// D-120: each empty page offers its one action, only once it's truly empty.
@Test func anEmptyPageOffersItsOneAction() {
  #expect(EmptyPage.library(songs: 0, loading: false, failed: false) == .library)
  #expect(EmptyPage.library(songs: 0, loading: true, failed: false) == nil)
  #expect(EmptyPage.library(songs: 0, loading: false, failed: true) == nil)
  #expect(EmptyPage.library.line == "Your library is empty")
  #expect(EmptyPage.library.action == "Find music")
  #expect(EmptyPage.playlist(songs: 0, loaded: true) == .playlist)
  #expect(EmptyPage.playlist(songs: 0, loaded: false) == nil)
  #expect(EmptyPage.playlist.action == "Add songs")
  #expect(EmptyPage.mix(songs: 5, shown: 0) == .mix)
  #expect(EmptyPage.mix(songs: 0, shown: 0) == nil)
  #expect(EmptyPage.mix.action == "Show hidden songs")
}

/// D-120: the sort control reads for its tab.
@Test func theSortNamesFitTheirTab() {
  #expect(LibrarySortChoice.name(.title, in: .artists) == "Name A–Z")
  #expect(LibrarySortChoice.name(.title, in: .albums) == "Title A–Z")
  #expect(LibrarySortChoice.options(.artists) == [.title, .titleDescending])
  #expect(LibrarySortChoice.options(.albums).contains(.year))
  #expect(!LibrarySortChoice.options(.tracks).contains(.year))
}

private let serverJSON = #"""
  {"top":[{"kind":"album","id":"night"},{"kind":"song","id":"glass"}],
   "songs":[{"id":"glass","title":"Glasshouse","artist":"Mara Quill"},
            {"id":"live","title":"Night Garden (Live)","artist":"Mara Quill","server_state":"not_in_library"}],
   "albums":[{"id":"night","title":"Night Garden","artist":"Mara Quill","in_library":true},
             {"id":"exchange","title":"Live at the Exchange","artist":"Mara Quill","in_library":true}],
   "artists":[{"id":"mara","name":"Mara Quill","in_library":true}],
   "playlists":[]}
  """#
private let onlineJSON = #"""
  {"songs":[{"apple_id":11,"title":"glasshouse","artist":"Mara Quill"}],
   "albums":[{"apple_id":21,"title":"Live at the Exchange","artist":"Mara Quill"},
             {"apple_id":22,"title":"Seedlings EP","artist":"Mara Quill"}],
   "state":"ready"}
  """#

/// Codex round 1 on batch E: an Apple song or album named like a server one
/// may be another recording or edition; nothing proves it the same, so both
/// stay.
@Test func searchKeepsADistinctRecordingWithTheSameName() {
  let screen = FoundScreen(decode(serverJSON), online: decode(onlineJSON))
  #expect(
    screen.albums.map(\.id) == ["album:night", "album:exchange", "apple-album:21", "apple-album:22"])
  #expect(screen.songs.map(\.id) == ["song:glass", "song:live", "apple-song:11"])
}

/// D-120: one Top result, then the groups, the top result not repeated.
@Test func searchResultsAreGroupedWithOneTopResult() {
  let groups = FoundGroups(FoundScreen(decode(serverJSON), online: decode(onlineJSON)))
  #expect(groups.top?.id == "album:night")
  #expect(groups.albums.map(\.id) == ["album:exchange", "apple-album:21", "apple-album:22"])
  #expect(groups.songs.map(\.id) == ["song:glass", "song:live", "apple-song:11"])
  #expect(groups.artists.map(\.id) == ["artist:mara"])
  // One mark each.
  #expect(groups.top?.mark == .inLibrary)
  #expect(groups.songs.map(\.mark) == [.inLibrary, .add, .add])
  #expect(groups.albums.map(\.mark) == [.inLibrary, .add, .add])
  #expect(FoundGroups(FoundScreen(nil)).isEmpty)
}

/// Codex round 1 on batch E: Not now hides the Apple Music row for this
/// account only, and for 30 days; the switch brings it back.
@Test func notNowHidesTheAppleMusicRowForOneAccountFor30Days() {
  let now = Date(timeIntervalSince1970: 1_000_000)
  var ask = AppleMusicAsk()
  #expect(ask.shows(account: "a", undetermined: true, now: now))
  #expect(!ask.shows(account: "a", undetermined: false, now: now))
  ask.dismiss(account: "a", now: now)
  #expect(!ask.shows(account: "a", undetermined: true, now: now.addingTimeInterval(86_400)))
  #expect(ask.shows(account: "b", undetermined: true, now: now))
  #expect(ask.shows(account: "a", undetermined: true, now: now.addingTimeInterval(AppleMusicAsk.quiet)))
  ask.reopen(account: "a")
  #expect(ask.shows(account: "a", undetermined: true, now: now))
  let kept = try! JSONDecoder().decode(AppleMusicAsk.self, from: JSONEncoder().encode(ask))
  #expect(kept == ask)
}

/// Codex round 1 on batch E: a row's tap starts at its song in the whole
/// list, songs waiting for removal left out.
@Test func theLibraryQueueStartsAtTheTappedSong() {
  let all = (0..<150).map { Track(id: String($0), title: String($0), artist: "A") }
  let queue = LibraryQueue.make(all, removing: { $0.id == "3" }, from: "120")
  #expect(queue.songs.count == 149)
  #expect(queue.songs[queue.index].id == "120")
  #expect(LibraryQueue.make(all, removing: { _ in false }).index == 0)
}
