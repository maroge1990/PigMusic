import Foundation
import PigDomain
import PigSync
import Testing

// Shazam in the iPhone app (D-70): "What's playing?" and the "Shazamed" list.

private func decode<T: Decodable>(_ json: String) -> T {
  try! LibraryCodec.decoder().decode(T.self, from: Data(json.utf8))
}

private func shazam(
  _ id: String, _ title: String, appleId: Int? = nil, artist: String = "Coldplay",
  album: String? = nil, at minute: Double? = nil
) -> ShazamSong {
  ShazamSong(
    shazamId: id, appleId: appleId, title: title, artist: artist, album: album,
    duration: 240, found: minute.map { Date(timeIntervalSince1970: $0 * 60) })
}

@Test func theInboxKeepsOneRowPerSongNewestFirst() {
  let merged = ShazamInbox.merge([
    // This session's copy (with Apple's album) comes first...
    shazam("1", "Yellow", appleId: 11, album: "Parachutes", at: 5),
    shazam("2", "Clocks", appleId: 12, at: 9),
    // ...the Shazam library's copy of the same song, found again later.
    shazam("1", "Yellow", appleId: 11, at: 20),
    shazam("3", "Fix You", at: nil),
    shazam("4", "Paradise", at: 9),
  ])
  #expect(merged.map(\.shazamId) == ["1", "2", "4", "3"])
  // The first copy's details with the latest date any copy was found.
  #expect(merged[0].album == "Parachutes")
  #expect(merged[0].found == Date(timeIntervalSince1970: 20 * 60))
  // An earlier duplicate never moves a song back.
  let again = ShazamInbox.merge([shazam("2", "Clocks", at: 9), shazam("2", "Clocks", at: 1)])
  #expect(again.map(\.found) == [Date(timeIntervalSince1970: 9 * 60)])
}

private actor Calls {
  var asked: [[Track]] = []
  var resolved: [Int] = []
  var requested: [[Track]] = []
  func ask(_ tracks: [Track]) { asked.append(tracks) }
  func resolve(_ id: Int) { resolved.append(id) }
  func request(_ tracks: [Track]) { requested.append(tracks) }
}

/// `/server/matches`: the person's copy of "Clocks", a household "Yellow".
private struct Server: ApplePlaylistStatusSource, ShazamRequestSource {
  let calls: Calls
  var failResolve = false
  func serverMatches(_ tracks: [Track]) async throws -> [Track?] {
    await calls.ask(tracks)
    return tracks.map { track -> Track? in
      var copy = Track(id: "s-" + track.title, title: track.title, artist: track.artist)
      switch track.title {
      case "Clocks": copy.serverState = "in_library"
      case "Yellow": copy.serverState = "household_available"
      default: return nil
      }
      return copy
    }
  }
  /// Apple id 13 and 15 are one recording; 14 has no catalogue match.
  func resolveApple(kind: String, appleId: Int) async throws -> ResolvedApple? {
    await calls.resolve(appleId)
    struct Down: Error {}
    if failResolve { throw Down() }
    switch appleId {
    case 13, 15:
      return decode(
        #"{"id":"rec-fix","name":"Fix You","artist":"Coldplay","artist_id":"a1","release_id":"rel-x&y","album":"X&Y"}"#
      )
    case 16:
      return decode(
        #"{"id":"rec-viva","name":"Viva la Vida","artist":"Coldplay","artist_id":"a1","release_id":null,"album":""}"#
      )
    default: return nil
    }
  }
  func requestMusic(_ tracks: [Track]) async throws -> AcquisitionResult {
    await calls.request(tracks)
    return decode(
      #"{"created":\#(tracks.count),"existing":0,"skipped":0,"downloads_enabled":true}"#)
  }
}

@Test func onlyCheckedSongsMissingFromTheServerAreDownloadedAndOnlyAsExactVersions() async throws
{
  let inbox = [
    shazam("1", "Clocks", appleId: 10),
    shazam("2", "Yellow", appleId: 11),
    shazam("3", "Fix You", appleId: 13),
    shazam("4", "Lost", appleId: 14),
    shazam("5", "No Apple id"),
    shazam("6", "Blank artist", appleId: 17, artist: " "),
    shazam("7", "Fix You", appleId: 15, album: "Live"),
    shazam("8", "Viva", appleId: 16, album: "Viva la Vida"),
  ]
  let calls = Calls()
  let server = Server(calls: calls)
  // The inbox asks the server like an Apple playlist: one call, in order,
  // leaving out the song it would refuse.
  let states = try await ApplePlaylistStatuses.states(
    for: ShazamInbox.playlistSongs(inbox), source: server)
  #expect(await calls.asked.first?.map(\.title).count == 7)
  let checked = try #require(states.states(for: ShazamInbox.playlistSongs(inbox)))
  #expect(checked.prefix(2).map(\.track?.id) == ["s-Clocks", "s-Yellow"])

  // In the library or on the server is never missing; neither is a song
  // without an Apple id or one the server can't be asked about.
  let missing = ShazamInbox.missing(inbox, states: states)
  #expect(missing.map(\.shazamId) == ["3", "4", "7", "8"])
  // States for another list (the inbox changed since) select nothing.
  #expect(ShazamInbox.missing(Array(inbox.reversed()), states: states).isEmpty)
  #expect(ShazamInbox.missing(inbox, states: nil).isEmpty)
  // Recognising alone requested nothing.
  #expect(await calls.requested.isEmpty)

  let outcome = try await ShazamRequests.requestMissing(missing, source: server)
  #expect(await calls.resolved == [13, 14, 15, 16])
  let sent = try #require(await calls.requested.first)
  #expect(await calls.requested.count == 1)
  // One request per recording, as MusicBrainz names it; no match, no request.
  #expect(sent.map(\.musicbrainzRecordingId) == ["rec-fix", "rec-viva"])
  #expect(sent[0].musicbrainzReleaseId == "rel-x&y" && sent[0].album == "X&Y")
  #expect(sent[1].musicbrainzReleaseId == nil && sent[1].album == "Viva la Vida")
  #expect(sent.allSatisfy { $0.serverState == "not_in_library" && $0.duration == 240 })
  #expect(outcome.created == 2 && outcome.unmatched == ["4"])

  // A lookup failing part-way requests nothing at all.
  let failing = Calls()
  await #expect(throws: (any Error).self) {
    _ = try await ShazamRequests.requestMissing(
      missing, source: Server(calls: failing, failResolve: true))
  }
  #expect(await failing.requested.isEmpty)
}

@Test func failedRecognitionSaysWhyInPlainWords() {
  #expect(ShazamFailure(shazamCode: 202, network: true) == .offline)
  #expect(ShazamFailure(shazamCode: nil, network: true) == .offline)
  #expect(ShazamFailure(shazamCode: 200, network: false) == .noMatch)
  #expect(ShazamFailure(shazamCode: 201, network: false) == .noMatch)
  #expect(ShazamFailure(shazamCode: 202, network: false) == .unavailable)
  #expect(ShazamFailure(shazamCode: 500, network: false) == .unavailable)
  #expect(ShazamFailure(shazamCode: nil, network: false) == .unavailable)
  #expect(ShazamFailure.microphone.message.contains("Settings"))
}

@Test func closingTheListeningScreenEndsItsAttempt() {
  var attempts = ShazamAttempts()
  let first = attempts.begin()
  #expect(attempts.isCurrent(first))
  // The sheet closes while the microphone prompt is still up.
  attempts.cancel()
  #expect(!attempts.isCurrent(first))
  // A new tap starts a new attempt; the old one stays ended.
  let second = attempts.begin()
  #expect(attempts.isCurrent(second) && !attempts.isCurrent(first))
}
