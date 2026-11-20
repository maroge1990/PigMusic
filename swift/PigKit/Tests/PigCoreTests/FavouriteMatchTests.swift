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

  private func fixture() throws -> Fixture {
    let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
      .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
      .deletingLastPathComponent()
    let url = root.appendingPathComponent("tests/fixtures/favourite_same_song.json")
    return try LibraryCodec.decoder().decode(Fixture.self, from: Data(contentsOf: url))
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
}
