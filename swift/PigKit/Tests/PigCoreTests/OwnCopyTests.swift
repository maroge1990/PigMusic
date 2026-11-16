import Foundation
@testable import PigDomain
import PigStore
import Testing

/// #211: a song the library holds, opened from Home, a catalogue page,
/// search or a playlist, offered "Request song": the sheet looked the
/// library up by the row's id, which there is a catalogue or recording id.
struct OwnCopyTests {
  private func owned(
    _ id: String, _ title: String, _ artist: String, recording: String? = nil,
    album: String? = nil, duration: Double? = nil, kind: [String]? = nil,
    release: String? = nil
  ) -> Track {
    var track = Track(id: id, title: title, artist: artist, album: album, duration: duration)
    track.musicbrainzRecordingId = recording
    track.musicbrainzReleaseId = release
    track.recordingKind = kind
    return track
  }
  private func store() async throws -> LibraryStore {
    let store = try LibraryStore(path: ":memory:")
    try await store.replace(
      LibrarySnapshot(
        revision: "one",
        tracks: [
          owned(
            "lib-texas", "Choosin' Texas", "Ella Langley", recording: "rec-texas", duration: 200),
          owned("lib-live", "Song", "Band", album: "Live at Leeds"),
          owned("lib-other", "Other Song", "Band", recording: "rec-other"),
          owned(
            "lib-edition", "Edition Song", "Band", recording: "rec-ed", album: "Album A",
            duration: 180, release: "rel-a"),
        ]))
    return store
  }

  @Test func aHomeRowFindsTheLibraryCopyByItsRecording() async throws {
    let store = try await store()
    // Home's popular songs: "catalogue:" + the recording id, never in the library.
    let item = DiscoveryItem(
      id: "rec-texas", name: "Choosin' Texas", artist: "Ella Langley", artistId: "a",
      kind: .songs, releaseId: nil, album: "", reason: nil)
    let home = try #require(item.playbackTrack)
    // What the sheet asked before: nothing under that id.
    #expect(try await store.sourceRevisions(ids: [home.actionTrack.id]).isEmpty)
    #expect(try await store.ownCopy(of: home)?.id == "lib-texas")
  }

  @Test func theSameSongWithoutARecordingIdIsFoundByTitleAndLeadArtist() async throws {
    let store = try await store()
    var song = owned("catalogue:x", "CHOOSIN’ TEXAS", "Ella Langley feat. Someone")
    song.serverState = "not_in_library"
    #expect(try await store.ownCopy(of: song)?.id == "lib-texas")
    // By id and by library id.
    #expect(try await store.ownCopy(of: owned("lib-other", "?", "?"))?.id == "lib-other")
    var found = owned("search-1", "Other Song", "Somebody else")
    found.libraryTrackId = "lib-other"
    #expect(try await store.ownCopy(of: found)?.id == "lib-other")
  }

  @Test func aDifferentSongIsNeverTakenForTheLibraryCopy() async throws {
    let store = try await store()
    // Another artist, another title, a length far off.
    #expect(try await store.ownCopy(of: owned("c1", "Choosin' Texas", "Someone Else")) == nil)
    #expect(try await store.ownCopy(of: owned("c2", "Choosin'", "Ella Langley")) == nil)
    #expect(
      try await store.ownCopy(of: owned("c3", "Choosin' Texas", "Ella Langley", duration: 260))
        == nil)
    // A studio song is not the live one in the library, nor a live one the studio.
    #expect(try await store.ownCopy(of: owned("c4", "Song", "Band", album: "Songs")) == nil)
    let live = owned("c5", "Song", "Band", album: "Live at Leeds")
    #expect(try await store.ownCopy(of: live)?.id == "lib-live")
    #expect(
      try await store.ownCopy(of: owned("c6", "Choosin' Texas", "Ella Langley", kind: ["live"]))
        == nil)
    #expect(try await store.ownCopy(of: owned("c7", "", "Band")) == nil)
  }

  /// Codex round 1 on batch 20 (high): a song the server's matching would
  /// send to review must never stand in for the library's copy, or Remove
  /// and Download would act on another recording.
  @Test func aConflictingRecordingIsNeverTakenForTheLibraryCopy() async throws {
    let store = try await store()
    // Same title, artist and length, but another known recording.
    let other = owned(
      "catalogue:rec-single", "Choosin' Texas", "Ella Langley", recording: "rec-single",
      duration: 200)
    #expect(try await store.ownCopy(of: other) == nil)
  }

  @Test func anExplicitEditionMatchesOnlyThatEdition() async throws {
    let store = try await store()
    // The same recording on another release, asked for as that edition.
    var single = owned(
      "catalogue:ed", "Edition Song", "Band", recording: "rec-ed", album: "Single B",
      duration: 180, release: "rel-b")
    single.explicitEdition = true
    #expect(try await store.ownCopy(of: single) == nil)
    // Another album title with no release id is another edition too.
    single.musicbrainzReleaseId = nil
    #expect(try await store.ownCopy(of: single) == nil)
    // The library's own edition still matches.
    var same = owned(
      "catalogue:ed", "Edition Song", "Band", recording: "rec-ed", album: "Album A",
      duration: 181, release: "rel-a")
    same.explicitEdition = true
    #expect(try await store.ownCopy(of: same)?.id == "lib-edition")
    // Without explicitEdition, any edition of the same recording matches.
    single.explicitEdition = nil
    #expect(try await store.ownCopy(of: single)?.id == "lib-edition")
  }
}
