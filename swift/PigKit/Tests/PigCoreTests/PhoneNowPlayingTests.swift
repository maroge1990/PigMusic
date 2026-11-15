import Foundation
import PigCompanion
import Testing

/// D-104 (#220): the Watch's home shows what the iPhone plays, from a small
/// message the phone sends on song and play/pause changes.
@Suite struct PhoneNowPlayingTests {
  private let jpeg = Data([0xFF, 0xD8, 0xFF, 0xE0]) + Data(repeating: 7, count: 2000)
  private let now = Date(timeIntervalSince1970: 1_800_000_000)

  private func json(_ value: PhoneNowPlaying) throws -> Data { try JSONEncoder().encode(value) }
  /// The JSON of `value` with one field changed, as another sender could write it.
  private func tampered(_ value: PhoneNowPlaying, _ change: (inout [String: Any]) -> Void) throws
    -> Data
  {
    let original = try JSONSerialization.jsonObject(with: json(value))
    var object = try #require(original as? [String: Any])
    change(&object)
    return try JSONSerialization.data(withJSONObject: object)
  }

  @Test func aSongAndAStopRoundTrip() throws {
    let song = PhoneNowPlaying(
      title: "First Light", artist: "The Orchard", playing: true, cover: jpeg, sentAt: now)
    let songData = try json(song)
    #expect(PhoneNowPlaying.decode(songData) == song)
    let stopped = PhoneNowPlaying.stopped(at: now)
    #expect(stopped.isStopped && !stopped.playing)
    let stoppedData = try json(stopped)
    #expect(PhoneNowPlaying.decode(stoppedData) == stopped)
  }

  @Test func theSenderCutsLongTextAndDropsAnOversizedOrForeignCover() throws {
    let long = String(repeating: "a", count: PhoneNowPlaying.maximumTextBytes + 50)
    let big = Data([0xFF, 0xD8]) + Data(repeating: 1, count: PhoneNowPlaying.maximumCoverBytes)
    let value = PhoneNowPlaying(title: long, artist: long, playing: false, cover: big, sentAt: now)
    #expect(value.title?.utf8.count == PhoneNowPlaying.maximumTextBytes)
    #expect(value.artist?.utf8.count == PhoneNowPlaying.maximumTextBytes)
    #expect(value.cover == nil)
    // Cut between characters, never inside one; a character can be many bytes.
    let accents = PhoneNowPlaying(
      title: String(repeating: "é", count: 300), artist: "e\u{301}" + String(repeating: "👍🏽", count: 200),
      playing: true, cover: nil, sentAt: now)
    #expect(accents.title == String(repeating: "é", count: PhoneNowPlaying.maximumTextBytes / 2))
    #expect((accents.artist?.utf8.count ?? 0) <= PhoneNowPlaying.maximumTextBytes)
    #expect(accents.artist?.hasPrefix("e\u{301}👍🏽") == true)
    let png = PhoneNowPlaying(
      title: "A", artist: "B", playing: true, cover: Data([0x89, 0x50, 0x4E, 0x47]), sentAt: now)
    #expect(png.cover == nil)
    // The largest message the phone can send still fits and decodes.
    let largest = try json(
      PhoneNowPlaying(
        title: long, artist: long, playing: true,
        cover: Data([0xFF, 0xD8])
          + Data(repeating: 1, count: PhoneNowPlaying.maximumCoverBytes - 2), sentAt: now))
    #expect(largest.count <= PhoneNowPlaying.maximumBytes)
    #expect(PhoneNowPlaying.decode(largest) != nil)
  }

  @Test func theWatchRefusesAnythingOutsideTheLimits() throws {
    let song = PhoneNowPlaying(
      title: "Song", artist: "Artist", playing: true, cover: nil, sentAt: now)
    let long = String(repeating: "x", count: PhoneNowPlaying.maximumTextBytes + 1)
    let oversized = Data([0xFF, 0xD8]) + Data(repeating: 1, count: PhoneNowPlaying.maximumCoverBytes)
    let cases: [(inout [String: Any]) -> Void] = [
      { $0["kind"] = "diagnostics" },
      { $0["version"] = 2 },
      { $0["title"] = long },
      { $0["artist"] = long },
      { _ = $0.removeValue(forKey: "artist") },
      { $0["cover"] = Data(repeating: 0xFF, count: 64).base64EncodedString() },
      { $0["cover"] = oversized.base64EncodedString() },
    ]
    for change in cases {
      let data = try tampered(song, change)
      #expect(PhoneNowPlaying.decode(data) == nil)
    }
    // A stop names no song and is never playing.
    let stopped = PhoneNowPlaying.stopped(at: now)
    let playingStop = try tampered(stopped) { $0["playing"] = true }
    #expect(PhoneNowPlaying.decode(playingStop) == nil)
    let namedStop = try tampered(stopped) { $0["artist"] = "Someone" }
    #expect(PhoneNowPlaying.decode(namedStop) == nil)
    // Other companion messages are not mistaken for it.
    let diagnostics = try JSONEncoder().encode(CompanionDiagnostics())
    #expect(PhoneNowPlaying.decode(diagnostics) == nil)
    #expect(
      PhoneNowPlaying.decode(Data(repeating: 32, count: PhoneNowPlaying.maximumBytes + 1)) == nil)
  }

  @Test func theRowShowsOnlyForARecentPhoneSongWhileTheWatchIsQuiet() {
    let playing = PhoneNowPlaying(
      title: "Song", artist: "Artist", playing: true, cover: nil, sentAt: now)
    var paused = playing
    paused.playing = false
    let quietPause = now.addingTimeInterval(PhoneNowPlaying.pausedLifetime + 1)
    let quietPlay = now.addingTimeInterval(PhoneNowPlaying.playingLifetime + 1)
    #expect(PhoneNowPlaying.showsRow(playing, watchPlaying: false, now: now))
    #expect(PhoneNowPlaying.showsRow(paused, watchPlaying: false, now: now.addingTimeInterval(60)))
    // The Watch playing its own song keeps its own row.
    #expect(!PhoneNowPlaying.showsRow(playing, watchPlaying: true, now: now))
    // Stopped, or never heard from.
    #expect(!PhoneNowPlaying.showsRow(.stopped(at: now), watchPlaying: false, now: now))
    #expect(!PhoneNowPlaying.showsRow(nil, watchPlaying: false, now: now))
    // A phone that went quiet without saying so.
    #expect(!PhoneNowPlaying.showsRow(paused, watchPlaying: false, now: quietPause))
    #expect(PhoneNowPlaying.showsRow(playing, watchPlaying: false, now: quietPause))
    #expect(!PhoneNowPlaying.showsRow(playing, watchPlaying: false, now: quietPlay))
  }

  @Test func anOlderStateArrivingLateNeverReplacesANewerOne() {
    let older = PhoneNowPlaying(title: "Old", artist: "A", playing: true, cover: nil, sentAt: now)
    let newer = PhoneNowPlaying.stopped(at: now.addingTimeInterval(5))
    #expect(older.supersedes(nil))
    #expect(newer.supersedes(older))
    #expect(!older.supersedes(newer))
  }
}
