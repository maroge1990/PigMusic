import PigDomain
import Testing

/// D-118 (#248): one song row with fixed slots.
struct SongRowFactsTests {
  private var track: Track {
    var track = Track(id: "s1", title: "Song", artist: "Artist", album: "Album")
    track.duration = 187.4
    return track
  }

  @Test func aListRowShowsCoverAndArtistAndAlbum() {
    let facts = SongRowFacts(track, favourite: true)
    #expect(facts.lead == .cover)
    #expect(facts.caption == "Artist · Album")
    #expect(facts.star)
    #expect(facts.length == nil)
  }

  /// The popular chart's numbered rows no longer hide the artist.
  @Test func aChartRowShowsTheArtistAndTheLength() {
    let facts = SongRowFacts(track, place: .chart(4))
    #expect(facts.lead == .number(4))
    #expect(facts.caption == "Artist · Album")
    #expect(facts.length == "3:07")
    #expect(!facts.star)
  }

  /// On an album page the album is the page: the artist always shows.
  @Test func anAlbumRowShowsTheArtistAndTheLength() {
    let facts = SongRowFacts(track, place: .album(2))
    #expect(facts.lead == .number(2))
    #expect(facts.caption == "Artist")
    #expect(facts.length == "3:07")
  }

  @Test func anUnknownLengthIsLeftOut() {
    var short = track
    short.duration = nil
    #expect(SongRowFacts(short, place: .album(1)).length == nil)
    #expect(SongRowFacts.length(.infinity) == nil)
    #expect(SongRowFacts.caption(artist: "Artist", album: nil) == "Artist")
  }
}
