import Foundation
import PigDomain
import Testing

/// Codex round 1 on batch G: the iPhone's favourite test must give the
/// server's answers. tests/fixtures/favourite_same_song.json holds pairs
/// that tests/test_favourite_matching.py checks against the server and
/// this test against FavouriteMatch.
struct FavouriteMatchTests {
  private struct Fixture: Decodable {
    struct Pair: Decodable {
      let note: String
      let same: Bool
      let left: Track
      let right: Track
    }
    let pairs: [Pair]
  }

  private static let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
    .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
    .deletingLastPathComponent()

  private func fixture() throws -> Fixture {
    let url = Self.root.appendingPathComponent("tests/fixtures/favourite_same_song.json")
    return try LibraryCodec.decoder().decode(Fixture.self, from: Data(contentsOf: url))
  }

  private struct Normalised: Decodable {
    struct Case: Decodable {
      let text: String
      let normalised: String
    }
    let cases: [Case]
  }

  /// Codex round 2 on batch G: case folding and word characters differed
  /// from Python's (ı, ẞ). tests/fixtures/favourite_normalise.json, made by
  /// scripts/generate_favourite_folding.py from the server's own normalise,
  /// holds every character case folding changes and every mark, punctuation,
  /// symbol and separator below U+3000; each must normalise as on the server.
  @Test func everyTextNormalisesAsTheServerNormalisesIt() throws {
    let url = Self.root.appendingPathComponent("tests/fixtures/favourite_normalise.json")
    let cases = try JSONDecoder().decode(Normalised.self, from: Data(contentsOf: url)).cases
    #expect(cases.count > 5000)
    var wrong: [String] = []
    for item in cases
    where FavouriteMatch.normalise(item.text) != item.normalised.unicodeScalars.map(\.value) {
      wrong.append(item.text.unicodeScalars.map { String($0.value, radix: 16) }.joined(separator: " "))
    }
    #expect(wrong.isEmpty, "\(wrong.count) of \(cases.count) differ: \(wrong.prefix(20))")
  }

  @Test func everyPairIsAnsweredAsTheServerAnswersIt() throws {
    let pairs = try fixture().pairs
    #expect(pairs.count >= 20)
    for pair in pairs {
      #expect(FavouriteMatch.same(pair.left, pair.right) == pair.same, "\(pair.note)")
      #expect(FavouriteMatch.same(pair.right, pair.left) == pair.same, "\(pair.note)")
    }
  }

  /// The Favourites playlist answers through the same test.
  @Test func theFavouritesPlaylistUsesTheServersTest() throws {
    let json = #"""
      [{"id":"fav","name":"Favourites","revision":1,"favourites":true,
        "entries":[{"entry_id":"f:0","title":"Maße","artist":"Artist","album":"Album",
          "position":0}]}]
      """#
    let playlists = try LibraryCodec.decoder().decode([Playlist].self, from: Data(json.utf8))
    let masse = Track(id: "m", title: "MASSE", artist: "artist", album: "ALBUM")
    #expect(FavouriteStates.listed(masse, in: playlists) == true)
    let underscored = Track(id: "u", title: "Ma_e", artist: "Artist", album: "Album")
    #expect(FavouriteStates.listed(underscored, in: playlists) == false)
  }

  private func favourites(count: Int, revision: Int) throws -> [Playlist] {
    let entries = (0..<count).map { index in
      #"{"entry_id":"f:\#(index)","title":"Song \#(index)","artist":"Artist \#(index % 300)","album":"Album \#(index % 900)","position":\#(index)}"#
    }
    let json =
      #"[{"id":"pig-favourites:me","name":"Favourites","revision":\#(revision),"favourites":true,"entries":["#
      + entries.joined(separator: ",") + "]}]"
    return try LibraryCodec.decoder().decode([Playlist].self, from: Data(json.utf8))
  }

  /// Codex round 2 on batch G: each menu lookup and each sync rebuilt the
  /// whole index (1.61 s for 20 first-time lookups with 10,000 favourites).
  /// It is built once per version of the playlist and reused.
  @Test func theIndexIsBuiltOncePerVersionOfThePlaylist() throws {
    var cache = FavouriteIndexCache()
    let first = try favourites(count: 10_000, revision: 1)
    for index in 0..<20 {
      let song = Track(
        id: "s\(index)", title: "Song \(index)", artist: "Artist \(index % 300)",
        album: "Album \(index % 900)")
      #expect(cache.index(first)?.contains(song) == true)
    }
    #expect(cache.builds == 1)
    // The same version read again from the store: still one build.
    #expect(cache.index(try favourites(count: 10_000, revision: 1)) != nil)
    #expect(cache.builds == 1)
    // A new revision (a favourite added elsewhere) builds it again, once.
    let next = try favourites(count: 10_001, revision: 2)
    let added = Track(id: "n", title: "Song 10000", artist: "Artist 100", album: "Album 100")
    #expect(cache.index(next)?.contains(added) == true)
    #expect(cache.index(next) != nil && cache.builds == 2)
    // No Favourites playlist: nothing, and a later one is built afresh.
    #expect(cache.index([]) == nil)
    #expect(cache.index(next) != nil && cache.builds == 3)
  }

  /// Waiting favourite edits are gathered once per sync, queued ones only.
  @Test func waitingFavouritesAreGatheredOnce() {
    let song = Track(id: "s", title: "Song", artist: "Artist")
    let other = Track(id: "o", title: "Other", artist: "Artist")
    var refused = PendingMutation(
      mutation: OfflineMutation(action: "favourite", track: other, favourite: true))
    refused.state = "rejected"
    let pending = [
      PendingMutation(mutation: OfflineMutation(action: "favourite", track: song, favourite: true)),
      refused,
      PendingMutation(mutation: OfflineMutation(action: "append", playlistId: "p", track: other)),
    ]
    #expect(FavouriteStates.waiting(in: pending) == ["s"])
  }
}
