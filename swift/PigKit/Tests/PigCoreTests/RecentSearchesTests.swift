import Foundation
import PigDomain
import PigStore
import Testing

private func song(_ id: String) -> Track {
  try! LibraryCodec.decoder().decode(
    Track.self, from: Data(#"{"id":"\#(id)","title":"Song \#(id)","artist":"Artist"}"#.utf8))
}

@Test func recentPicksAreNewestFirstOnceEachAndCapped() throws {
  var recent = RecentSearches()
  recent.add(.song(song("a")))
  recent.add(.catalogueArtist(id: "mb", name: "U2"))
  recent.add(.song(song("a")))  // Picked again: moves to the top, not repeated.
  #expect(recent.picks.map(\.id) == ["song:a", "catalogue-artist:mb"])
  for index in 0..<30 { recent.add(.song(song("\(index)"))) }
  #expect(recent.picks.count == RecentSearches.limit)
  #expect(recent.picks.first?.id == "song:29")
  // Saved and read back the way the phone keeps them.
  let encoded = try JSONEncoder().encode(recent)
  #expect(try JSONDecoder().decode(RecentSearches.self, from: encoded) == recent)
  recent.clear()
  #expect(recent.picks.isEmpty)
}
