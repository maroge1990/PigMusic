import Foundation
import PigDomain
import Testing

/// Codex round 2 on batch G: a refused playlist create stranded its songs in
/// the outbox. The name is checked first with the server's rule, and a
/// create the server refuses anyway leaves with the edits that depend on it.
struct PlaylistNameTests {
  private struct Fixture: Decodable {
    struct Case: Decodable {
      let name: String
      let accepted: String?
    }
    let names: [Case]
  }

  /// tests/fixtures/playlist_names.json, which tests/test_playlist_names.py
  /// checks against the server.
  @Test func everyNameIsKeptAsTheServerKeepsIt() throws {
    let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
      .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
      .deletingLastPathComponent()
    let url = root.appendingPathComponent("tests/fixtures/playlist_names.json")
    let cases = try JSONDecoder().decode(Fixture.self, from: Data(contentsOf: url)).names
    #expect(cases.count >= 15)
    for item in cases {
      let kept = PlaylistName.accepted(item.name)
      // By code point, as the server compares and counts.
      #expect(
        kept.map { Array($0.unicodeScalars) } == item.accepted.map { Array($0.unicodeScalars) },
        "\(item.name.debugDescription)")
    }
  }

  /// A 101-character name made a create the server refused, with every song
  /// queued behind it; now it makes no edits at all.
  @Test func aNameTheServerRefusesMakesNoEdits() {
    let song = Track(id: "s", title: "Song", artist: "Artist")
    #expect(NewPlaylistEdits.make(name: String(repeating: "a", count: 101), songs: [song]).isEmpty)
    #expect(NewPlaylistEdits.make(name: "\u{1C}\u{3000}", songs: [song]).isEmpty)
    #expect(NewPlaylistEdits.make(name: " Mix\u{1F}", songs: [song]).first?.name == "Mix")
  }

  private func pending(_ mutation: OfflineMutation, _ state: String = "queued", sent: Bool = false)
    -> PendingMutation
  {
    var item = PendingMutation(mutation: mutation)
    item.state = state
    item.attempted = sent
    return item
  }

  /// A refused create leaves with every edit to its playlist, dependents
  /// first; another playlist's edits and edits queued in between stay; a
  /// create that only failed to connect (still queued) takes nothing.
  @Test func aRefusedCreateTakesItsDependentsAndNothingElse() {
    let song = Track(id: "s", title: "Song", artist: "Artist")
    let group = NewPlaylistEdits.make(name: "Road trip", songs: [song, song])
    let other = NewPlaylistEdits.make(name: "Other", songs: [song])
    let favourite = OfflineMutation(action: "favourite", track: song, favourite: true)
    let rename = OfflineMutation(action: "rename", playlistId: "existing", name: "New name")
    let refusedCreate = pending(group[0], "rejected", sent: true)
    let outbox = [
      refusedCreate, pending(group[1]), pending(favourite), pending(other[0]),
      pending(rename), pending(group[2]), pending(other[1]),
    ]
    let refused = NewPlaylistGroup.refused(outbox)
    #expect(refused.map { $0.map(\.id) } == [[group[1].id, group[2].id, group[0].id]])
    // In conflict counts as refused too; queued (a failed connection) doesn't.
    var conflicted = outbox
    conflicted[0].state = "conflict"
    #expect(NewPlaylistGroup.refused(conflicted).count == 1)
    var queued = outbox
    queued[0].state = "queued"
    #expect(NewPlaylistGroup.refused(queued).isEmpty)
    // The Playlists tab's create has no id of its own: nothing depends on it.
    let tab = pending(OfflineMutation(action: "create", name: "Tab"), "rejected")
    #expect(NewPlaylistGroup.refused([tab]).isEmpty)
    #expect(NewPlaylistGroup.dependents(of: tab, in: outbox + [tab]).isEmpty)
    // Discarding a create by hand finds the same dependents.
    #expect(
      NewPlaylistGroup.dependents(of: outbox[3], in: outbox).map(\.id) == [other[1].id])
  }
}
