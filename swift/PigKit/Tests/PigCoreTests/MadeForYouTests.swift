import Foundation
import PigDomain
import Testing

/// Batch 11: similar songs when a queue ends (#202) and Rewind (#203).
struct MadeForYouTests {
  @Test func aQueueEndContinuesOnlyWhenWantedAndNotOnRepeatOrRadio() {
    #expect(QueueContinuation.continues(keepPlaying: true, repeating: false, radio: false))
    #expect(!QueueContinuation.continues(keepPlaying: false, repeating: false, radio: false))
    #expect(!QueueContinuation.continues(keepPlaying: true, repeating: true, radio: false))
    #expect(!QueueContinuation.continues(keepPlaying: true, repeating: false, radio: true))
  }

  @Test func songsArrivingAfterTheQueueEndedPlayAtOnce() {
    // Codex round 1: the last song ended or was skipped while they loaded.
    #expect(QueueContinuation.playsAtOnce(ended: true, atLastSong: true, playing: false))
    // Still playing the last song: they wait in Up next.
    #expect(!QueueContinuation.playsAtOnce(ended: false, atLastSong: true, playing: true))
    #expect(!QueueContinuation.playsAtOnce(ended: true, atLastSong: true, playing: true))
    // The person went back in the queue meanwhile.
    #expect(!QueueContinuation.playsAtOnce(ended: true, atLastSong: false, playing: false))
  }

  @Test func theLastFiveSongsAreSeedsAndTheWholeQueueIsLeftOut() {
    let ids = (0..<8).map { "s\($0)" } + ["s1"]
    let (seeds, exclude) = QueueContinuation.request(queue: ids, current: 6)
    #expect(seeds == ["s2", "s3", "s4", "s5", "s6"])
    #expect(exclude == (0..<8).map { "s\($0)" })
    #expect(QueueContinuation.request(queue: ["a"], current: 0).seeds == ["a"])
    #expect(QueueContinuation.request(queue: [], current: 0).seeds.isEmpty)
  }

  @Test func preferencesSavedBeforeKeepPlayingMeanOn() throws {
    let old = Data(#"{"appearance":"dark","default_tab":"albums"}"#.utf8)
    let decoded = try LibraryCodec.decoder().decode(AccountPreferences.self, from: old)
    #expect(decoded.appearance == "dark" && decoded.defaultTab == "albums" && decoded.keepPlaying)
    let off = Data(#"{"appearance":"system","default_tab":"artists","keep_playing":false}"#.utf8)
    #expect(try !LibraryCodec.decoder().decode(AccountPreferences.self, from: off).keepPlaying)
    // Round trip through the phone's own cache keeps the choice.
    let again = try LibraryCodec.decoder().decode(
      AccountPreferences.self,
      from: LibraryCodec.encoder().encode(AccountPreferences(keepPlaying: false)))
    #expect(!again.keepPlaying)
  }

  @Test func rewindKeepsSongsThatCanNoLongerPlayOutOfPlayTopSongs() throws {
    let json = Data(
      #"""
      {"period":"week","start":1,"end":2,"timezone":"Australia/Melbourne","plays":3,
       "minutes":9,
       "top_songs":[{"track":{"id":"a","title":"A","artist":"X","available":true},"plays":2},
                    {"track":{"id":"mix:e:1","title":"B","artist":"Y","available":false},"plays":1}],
       "top_artists":[{"name":"X","plays":2,"minutes":6}],
       "top_albums":[{"title":"First","artist":"X","plays":2,"cover_url":null}],
       "new_artists":["Y"],"top_mixes":[{"title":"Daily blend","plays":1}]}
      """#.utf8)
    let rewind = try LibraryCodec.decoder().decode(Rewind.self, from: json)
    #expect(rewind.minutes == 9 && rewind.topSongs.count == 2)
    #expect(rewind.playable.map(\.id) == ["a"])
    #expect(rewind.topArtists.first?.minutes == 6 && rewind.newArtists == ["Y"])
    #expect(rewind.topMixes.first?.title == "Daily blend")
  }

  // --- #216: Rewind's Home card and page ---------------------------------

  private func song(_ id: String, available: Bool = true) -> RewindSong {
    RewindSong(track: Track(id: id, title: id, artist: "X"), plays: 1, available: available)
  }

  @Test func tappingATopSongPlaysItThenTheOnesAfterItSkippingUnavailable() {
    let rewind = Rewind(
      period: "week", plays: 5, minutes: 20,
      topSongs: [song("1"), song("2"), song("3", available: false), song("4"), song("5")])
    #expect(rewind.queue(from: 1)?.map(\.id) == ["2", "4", "5"])
    #expect(rewind.queue(from: 0)?.map(\.id) == ["1", "2", "4", "5"])
    #expect(rewind.queue(from: 4)?.map(\.id) == ["5"])
    // An unavailable song doesn't play; nor does a song that isn't there.
    #expect(rewind.queue(from: 2) == nil)
    #expect(rewind.queue(from: 5) == nil && rewind.queue(from: -1) == nil)
  }

  @Test func theWeekHasSevenBarsMondayToSundayWithDaysToComeEmpty() {
    let rewind = Rewind(
      period: "week", plays: 4, minutes: 63,
      days: [
        RewindDay(start: 0, minutes: 40, plays: 2), RewindDay(start: 1, minutes: 0, plays: 0),
        RewindDay(start: 2, minutes: 20, plays: 1), RewindDay(start: 3, minutes: 1, plays: 1),
      ])
    let bars = rewind.weekBars
    #expect(bars.map(\.letter) == ["M", "T", "W", "T", "F", "S", "S"])
    #expect(bars.first?.day == "Monday" && bars.last?.day == "Sunday")
    #expect(bars.map(\.minutes) == [40, 0, 20, 1, nil, nil, nil])
    #expect(bars.map(\.height) == [1, 0, 0.5, RewindBar.least, 0, 0, 0])
    // An older server sends no days: seven empty days, nothing divided by zero.
    let none = Rewind(period: "week", plays: 0, minutes: 0).weekBars
    #expect(none.count == 7 && none.allSatisfy { $0.minutes == nil && $0.height == 0 })
    #expect(RewindBar.heights([0, 0]) == [0, 0])
  }

  @Test func albumsAndArtistsOpenTheLibraryElseTheCatalogueElseNothing() {
    #expect(
      RewindAlbum(
        title: "A", artist: "X", plays: 1, libraryId: "requested-1", musicbrainzReleaseId: "r"
      ).link == .libraryAlbum(id: "requested-1", release: "r"))
    #expect(
      RewindAlbum(title: "A", artist: "X", plays: 1, musicbrainzReleaseId: "r").link
        == .release("r"))
    #expect(RewindAlbum(title: "A", artist: "X", plays: 1, musicbrainzReleaseId: "").link == nil)
    #expect(
      RewindArtist(name: "X", plays: 1, minutes: 2, libraryId: "a1").link
        == .libraryArtist(id: "a1", musicbrainz: nil))
    #expect(
      RewindArtist(name: "X", plays: 1, minutes: 2, musicbrainzArtistId: "m").link
        == .catalogueArtist("m"))
    #expect(RewindArtist(name: "X", plays: 1, minutes: 2).link == nil)
  }

  @Test func rewindReadsDaysLinksCoversAndMixKinds() throws {
    let json = Data(
      #"""
      {"period":"week","start":1,"end":2,"timezone":"Australia/Melbourne","plays":3,
       "minutes":9,"top_songs":[],
       "top_artists":[{"name":"X","plays":2,"minutes":6,"id":"art-1",
         "musicbrainz_artist_id":"mb-a","new":true,
         "cover_url":"/api/v1/catalogue/artwork/release/rel-1"}],
       "top_albums":[{"title":"First","artist":"X","plays":2,
         "cover_url":"/api/v1/covers/abc.jpg","id":"alb-1","musicbrainz_release_id":"rel-1"}],
       "new_artists":["X"],
       "top_mixes":[{"title":"Daily blend","plays":1,"id":"e1","kind":"radio"},
                    {"title":"Saved","plays":1,"id":"e2","kind":"saved:k"},
                    {"title":"Old","plays":1}],
       "days":[{"start":100,"minutes":9,"plays":3}]}
      """#.utf8)
    let rewind = try LibraryCodec.decoder().decode(Rewind.self, from: json)
    #expect(rewind.days == [RewindDay(start: 100, minutes: 9, plays: 3)])
    let artist = try #require(rewind.topArtists.first)
    #expect(artist.libraryId == "art-1" && artist.musicbrainzArtistId == "mb-a" && artist.new)
    #expect(RewindCover(artist.coverUrl) == .release("rel-1"))
    let album = try #require(rewind.topAlbums.first)
    #expect(album.link == .libraryAlbum(id: "alb-1", release: "rel-1"))
    #expect(RewindCover(album.coverUrl) == nil)  // not a content hash
    #expect(rewind.topMixes.map(\.coverKind) == ["radio", "daily", "daily"])
    #expect(rewind.topMixes.first?.edition == "e1")
  }

  @Test func anOlderServersRewindStillReads() throws {
    let json = Data(
      #"""
      {"period":"year","start":1,"end":2,"timezone":"UTC","plays":1,"minutes":3,
       "top_songs":[],"top_artists":[{"name":"X","plays":1,"minutes":3}],
       "top_albums":[{"title":"T","artist":"X","plays":1,"cover_url":null}],
       "new_artists":[],"top_mixes":[{"title":"M","plays":1}]}
      """#.utf8)
    let rewind = try LibraryCodec.decoder().decode(Rewind.self, from: json)
    #expect(rewind.days.isEmpty)
    #expect(rewind.topArtists.first?.link == nil && rewind.topArtists.first?.new == false)
    #expect(rewind.topAlbums.first?.link == nil)
  }

  @Test func everyCoverAddressRewindSendsBecomesArtwork() {
    let sha = String(repeating: "ab", count: 32)
    #expect(RewindCover("/api/v1/covers/\(sha).jpg") == .stored(sha))
    #expect(RewindCover("/api/v1/covers/\(sha).jpg?v=3") == .stored(sha))
    #expect(RewindCover("/api/artwork/\(sha).jpg") == .stored(sha))
    #expect(RewindCover("/api/v1/catalogue/artwork/release/rel-1") == .release("rel-1"))
    #expect(RewindCover("/api/catalogue/artwork/release/rel-1") == .release("rel-1"))
    // Library albums and artists without a stored cover: their own address.
    #expect(
      RewindCover("/api/v1/library-artwork/albums/requested-7") == .libraryAlbum("requested-7"))
    #expect(RewindCover("/api/library-artwork/albums/a1?v=2") == .libraryAlbum("a1"))
    #expect(RewindCover("/api/v1/library-artwork/artists/x1") == .libraryArtist("x1"))
    // Cached names that aren't content hashes, and anything else: none.
    #expect(RewindCover("/api/v1/covers/catalogue-cover-abc.jpg") == nil)
    #expect(RewindCover("/api/v1/covers/\(sha.uppercased()).jpg") == nil)
    #expect(RewindCover("/api/v1/library-artwork/tracks/t1") == nil)
    #expect(RewindCover("https://example.com/a.jpg") == nil && RewindCover(nil) == nil)
  }
}
