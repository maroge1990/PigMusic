import Foundation
import PigDomain
import Testing

/// #264: Add no longer resumes a song the person paused; the answer says
/// `paused`, and the iPhone says where to resume it, never "excluded" nor
/// "Already in your library".
struct PausedAddTests {
  private func decode(_ json: String) throws -> AcquisitionResult {
    try LibraryCodec.decoder().decode(AcquisitionResult.self, from: Data(json.utf8))
  }

  @Test func anAlbumWhoseSongsWerePausedSaysWhereToResumeThem() throws {
    let result = try decode(
      #"{"created":0,"existing":0,"skipped":0,"paused":3,"downloads_enabled":true}"#)
    #expect(result.paused == 3)
    #expect(result.onlyPaused)
    #expect(result.sentence == "Paused. Resume it in Downloads.")
  }

  @Test func pausedSongsBesideNewOnesAreCounted() throws {
    let result = try decode(
      #"{"created":2,"existing":0,"skipped":0,"paused":1,"downloads_enabled":true}"#)
    #expect(!result.onlyPaused)
    // The same sentence as the web's, after what was added.
    #expect(
      result.sentence == "Adding 2 songs to your library. Paused. Resume it in Downloads.")
  }

  /// An older server sends no `paused`.
  @Test func anAnswerWithoutPausedStillReads() throws {
    let result = try decode(
      #"{"created":0,"existing":0,"skipped":0,"downloads_enabled":true}"#)
    #expect(result.paused == 0)
    #expect(result.sentence == "Already in your library.")
  }

  private func song(_ id: String, _ title: String) -> Track {
    Track(id: id, title: title, artist: "Artist", album: "Album")
  }

  @Test func aPausedSongIsMarkedPausedUntilTheRequestsChange() {
    var marks = RequestMarks()
    marks.queued("song")
    marks.pausedOnServer([song("song", "Song")])
    #expect(marks.paused == ["song"])
    #expect(marks.contains("song"))
    marks.requestsChanged()
    #expect(!marks.contains("song"))
  }

  /// Codex round 1 on batch D: resuming one request clears only that
  /// song's paused mark; other paused songs and sent marks stay.
  @Test func resumingOneSongKeepsTheOtherMarks() {
    var marks = RequestMarks()
    for id in ["one", "two", "sent"] { marks.queued(id) }
    marks.sent(["sent"])
    marks.pausedOnServer([song("one", "One"), song("two", "Two")])
    marks.resumed(title: " one ", artist: "artist", album: "Album")
    #expect(marks.paused == ["two"])
    #expect(!marks.contains("one"))
    #expect(marks.contains("two") && marks.contains("sent"))
  }
}
