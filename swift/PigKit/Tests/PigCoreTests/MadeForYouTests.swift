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
}
