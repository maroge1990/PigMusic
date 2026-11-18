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

  @Test func aPausedSongIsMarkedPausedUntilTheRequestsChange() {
    var marks = RequestMarks()
    marks.queued("song")
    marks.pausedOnServer(["song"])
    #expect(marks.paused == ["song"])
    #expect(marks.contains("song"))
    marks.requestsChanged()
    #expect(!marks.contains("song"))
  }
}
