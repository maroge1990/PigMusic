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

  private func filed(_ track: Track, on album: String) -> Track {
    var track = track
    track.albumId = album
    return track
  }
  private func editionStore() async throws -> LibraryStore {
    let store = try LibraryStore(path: ":memory:")
    try await store.replace(
      LibrarySnapshot(
        revision: "one",
        tracks: [
          // Two files with the same edition tags and no content identity.
          owned("lib-twin-1", "Twin Song", "Band", album: "Twin Album", duration: 200),
          owned("lib-twin-2", "Twin Song", "Band", album: "Twin Album", duration: 201),
          // One song on a compilation (sorts first) and on the artist's album.
          filed(
            owned("lib-a-comp", "Rank Song", "Band", album: "Best Of", duration: 150), on: "comp"),
          filed(
            owned("lib-z-own", "Rank Song", "Band", album: "Rank Album", duration: 151), on: "own"),
          owned("lib-song", "Song", "Band", recording: "rec-song", duration: 240),
          owned("lib-way", "Go Your Own Way", "Fleetwood Mac", duration: 220),
          owned("lib-dreams", "Dreams (2004 Remaster)", "Fleetwood Mac", duration: 257),
        ],
        albums: [
          Album(id: "comp", title: "Best Of", artist: "Various Artists", trackCount: 20),
          Album(id: "own", title: "Rank Album", artist: "Band", trackCount: 10),
        ]))
    return store
  }

  /// Codex round 2 on batch 20 (high): an explicit edition the library holds
  /// twice, with nothing proving the files identical, goes to review on the
  /// server: the phone must not pick one of them.
  @Test func anAmbiguousExplicitEditionIsNoMatch() async throws {
    let store = try await editionStore()
    var twin = owned("catalogue:twin", "Twin Song", "Band", album: "Twin Album", duration: 200)
    twin.explicitEdition = true
    #expect(try await store.ownCopy(of: twin) == nil)
    // Any edition: the server picks one copy (its album, then the fullest,
    // then by id), and so does the phone.
    twin.explicitEdition = nil
    #expect(try await store.ownCopy(of: twin)?.id == "lib-twin-1")
  }

  @Test func anyEditionPrefersTheRequestedAlbumAsTheServerDoes() async throws {
    let store = try await editionStore()
    let song = owned("catalogue:rank", "Rank Song", "Band", album: "Rank Album (Deluxe)")
    #expect(try await store.ownCopy(of: song)?.id == "lib-z-own")
  }

  /// Codex confirmation (medium): edition qualifiers ("- 2011 Remaster")
  /// are not part of a song's title, as in the server's `title_key`; live
  /// and remix qualifiers are, and known recordings still must agree.
  @Test func anEditionQualifierDoesNotHideTheLibraryCopy() async throws {
    let store = try await editionStore()
    let remaster = owned(
      "catalogue:rec-song", "Song - 2011 Remaster", "Band", recording: "rec-song", duration: 241)
    #expect(try await store.ownCopy(of: remaster)?.id == "lib-song")
    let way = owned("catalogue:way", "Go Your Own Way - 2004 Remaster", "Fleetwood Mac")
    #expect(try await store.ownCopy(of: way)?.id == "lib-way")
    let dreams = owned("catalogue:dreams", "Dreams", "Fleetwood Mac", duration: 257)
    #expect(try await store.ownCopy(of: dreams)?.id == "lib-dreams")
    // Live and remix stay distinct; a conflicting recording never matches.
    #expect(
      try await store.ownCopy(of: owned("c1", "Go Your Own Way (Live)", "Fleetwood Mac")) == nil)
    #expect(
      try await store.ownCopy(of: owned("c2", "Go Your Own Way - 2004 Remix", "Fleetwood Mac"))
        == nil)
    let conflicting = owned(
      "catalogue:x", "Song - 2011 Remaster", "Band", recording: "rec-other-song", duration: 240)
    #expect(try await store.ownCopy(of: conflicting) == nil)
  }

  /// Codex confirmation (high): the lookup capped its candidates, so with
  /// 199 other artists' "Song" first, only one of two same-edition copies
  /// was seen and taken. Candidates are now the title and lead artist, and
  /// a lookup that may have been cut short takes nothing.
  @Test func twinsPastTheCandidateCapAreStillSeen() async throws {
    let store = try LibraryStore(path: ":memory:")
    var tracks = (0..<199).map { index in
      owned(String(format: "a%03d", index), "Song", "Artist \(index)", album: "Album \(index)")
    }
    tracks += ["z-twin-1", "z-twin-2"].map {
      owned($0, "Song", "Twin Band", album: "Twin Album", duration: 200)
    }
    try await store.replace(LibrarySnapshot(revision: "one", tracks: tracks))
    var twin = owned("catalogue:twin", "Song", "Twin Band", album: "Twin Album", duration: 200)
    twin.explicitEdition = true
    #expect(try await store.ownCopy(of: twin) == nil)
    // One copy of that edition among them is still found.
    var lone = owned("catalogue:a150", "Song", "Artist 150", album: "Album 150")
    lone.explicitEdition = true
    #expect(try await store.ownCopy(of: lone)?.id == "a150")
  }

  @Test func aLookupThatMayBeCutShortTakesNothing() async throws {
    let store = try LibraryStore(path: ":memory:")
    let tracks = (0..<501).map { index in
      owned(String(format: "m%03d", index), "Many Song", "Many Band", album: "Edition \(index)")
    }
    try await store.replace(LibrarySnapshot(revision: "one", tracks: tracks))
    #expect(try await store.ownCopy(of: owned("catalogue:many", "Many Song", "Many Band")) == nil)
  }
}
