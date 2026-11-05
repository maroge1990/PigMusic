import Foundation
import PigDomain
import Testing

// Share → PigMusic from the Shazam app or Apple Music (D-70, option C).

private let shared = Date(timeIntervalSince1970: 1_000_000)

@Test func aShazamShareGivesItsIdTitleAndArtist() throws {
  let share = try #require(
    ShazamShare.parse(
      urls: [URL(string: "https://www.shazam.com/track/53941049/yellow?referrer=share")!],
      texts: ["I used Shazam to discover Yellow by Coldplay. https://www.shazam.com/track/53941049/yellow"],
      at: shared))
  #expect(share.source == .shazam)
  #expect(share.shazamId == "53941049")
  #expect(share.appleId == nil)
  #expect(share.title == "Yellow")
  #expect(share.artist == "Coldplay")
  #expect(share.inboxId == "53941049")
  // The link inside the words is enough on its own; the last " by " splits.
  let text = try #require(
    ShazamShare.parse(
      urls: [],
      texts: [
        "I used Shazam to discover Stand by Me by Ben E. King. https://shazam.com/track/208470/stand-by-me"
      ], at: shared))
  #expect(text.shazamId == "208470")
  #expect(text.title == "Stand by Me")
  #expect(text.artist == "Ben E. King")
}

@Test func anAppleMusicSongLinkGivesItsAppleId() throws {
  let onAlbum = try #require(
    ShazamShare.parse(
      urls: [URL(string: "https://music.apple.com/au/album/yellow/1122782080?i=1122782283")!],
      texts: [], at: shared))
  #expect(onAlbum.source == .appleMusic)
  #expect(onAlbum.appleId == 1_122_782_283)
  #expect(onAlbum.shazamId == nil)
  #expect(onAlbum.inboxId == "apple:1122782283")
  #expect(onAlbum.wordsSong == nil)
  let song = ShazamShare.parse(
    urls: [], texts: ["https://music.apple.com/us/song/yellow/1122782283"], at: shared)
  #expect(song?.appleId == 1_122_782_283)
  // A Shazam link wins over an Apple one in the same share.
  let both = ShazamShare.parse(
    urls: [
      URL(string: "https://music.apple.com/us/song/yellow/1122782283")!,
      URL(string: "https://www.shazam.com/track/53941049/yellow")!,
    ], texts: [], at: shared)
  #expect(both?.shazamId == "53941049")
}

@Test func linksThatNameNoSongAreNotShares() {
  for link in [
    "https://music.apple.com/au/album/parachutes/1122782080",  // an album
    "https://music.apple.com/au/artist/coldplay/471744",
    "https://www.shazam.com/artist/coldplay/1234",
    "https://www.shazam.com/track/not-a-number",
    "https://example.com/track/53941049",
    "https://music.apple.com.example.net/au/song/x/1",
  ] {
    #expect(ShazamShare.parse(urls: [URL(string: link)!], texts: [], at: shared) == nil, "\(link)")
  }
  #expect(ShazamShare.parse(urls: [], texts: ["Listen to this!"], at: shared) == nil)
}

private func folder() -> URL {
  FileManager.default.temporaryDirectory.appendingPathComponent(
    "shazam-share-\(UUID().uuidString)", isDirectory: true)
}

@Test func theQueueKeepsEachShareUntilRemoved() throws {
  let root = folder()
  defer { try? FileManager.default.removeItem(at: root) }
  let queue = ShazamShareQueue(directory: ShazamShareFolder.queue(in: root))
  #expect(queue.pending().isEmpty)
  let first = ShazamShare(
    source: .shazam, shazamId: "1", appleId: nil, title: "Yellow", artist: "Coldplay",
    shared: shared)
  let second = ShazamShare(
    source: .appleMusic, shazamId: nil, appleId: 11, shared: shared.addingTimeInterval(60))
  try queue.append(second)
  try queue.append(first)
  // A file that isn't a share is listed for removal.
  try Data("junk".utf8).write(to: queue.directory.appendingPathComponent("0-bad.json"))
  let pending = queue.pending()
  #expect(pending.map(\.share) == [nil, first, second])
  queue.remove([pending[0].file, pending[1].file])
  #expect(queue.pending().map(\.share) == [second])
}

private func song(_ id: String, _ title: String, appleId: Int? = nil, at minute: Double)
  -> ShazamSong
{
  ShazamSong(
    shazamId: id, appleId: appleId, title: title, artist: "Coldplay",
    found: Date(timeIntervalSince1970: minute * 60))
}

private actor Lookups {
  var asked: [String] = []
  func ask(_ id: String) { asked.append(id) }
}

@Test func drainingLooksUpEachNewShareOnceAndKeepsWaitingOnes() async throws {
  let root = folder()
  defer { try? FileManager.default.removeItem(at: root) }
  let queue = ShazamShareQueue(directory: ShazamShareFolder.queue(in: root))
  let store = ShazamSharedSongs(file: ShazamShareFolder.songs(in: root))
  try store.save([song("9", "Clocks", appleId: 19, at: 1)])
  let minute = { (m: Double) in Date(timeIntervalSince1970: m * 60) }
  // Shazam id 1: found by Shazam's catalogue, with its Apple id.
  try queue.append(ShazamShare(source: .shazam, shazamId: "1", appleId: nil, shared: minute(10)))
  // Apple id 12: Apple's names.
  try queue.append(ShazamShare(source: .appleMusic, shazamId: nil, appleId: 12, shared: minute(11)))
  // Shazam id 3: nothing names it, so the shared words are kept.
  try queue.append(
    ShazamShare(
      source: .shazam, shazamId: "3", appleId: nil, title: "Fix You", artist: "Coldplay",
      shared: minute(12)))
  // Apple id 19 is "Clocks", already shared: dated again, not looked up.
  try queue.append(ShazamShare(source: .appleMusic, shazamId: nil, appleId: 19, shared: minute(13)))
  // Offline: waits for the next time.
  try queue.append(ShazamShare(source: .shazam, shazamId: "4", appleId: nil, shared: minute(14)))
  // Nothing names it and no words: dropped.
  try queue.append(ShazamShare(source: .shazam, shazamId: "5", appleId: nil, shared: minute(15)))
  let lookups = Lookups()
  let songs = await ShazamShares.drain(queue: queue, store: store, now: minute(16)) { share in
    await lookups.ask(share.inboxId)
    switch share.inboxId {
    case "1":
      return .song(
        ShazamSong(shazamId: "1", appleId: 11, title: "Yellow", artist: "Coldplay", album: "Parachutes"))
    case "apple:12":
      return .song(ShazamSong(shazamId: "", appleId: 12, title: "Paradise", artist: "Coldplay"))
    case "4": return .later
    default: return .notFound
    }
  }
  #expect(await lookups.asked == ["1", "apple:12", "3", "4", "5"])
  #expect(songs.map(\.shazamId) == ["9", "3", "apple:12", "1"])
  #expect(songs.map(\.title) == ["Clocks", "Fix You", "Paradise", "Yellow"])
  #expect(songs[0].found == minute(13))
  #expect(songs[3].album == "Parachutes")
  #expect(songs[3].found == minute(10))
  // Saved for the next start; only the waiting share stays queued.
  #expect(store.load() == songs)
  #expect(queue.pending().map(\.share?.shazamId) == ["4"])

  // A week later the waiting share uses its words, if any; without, it is dropped.
  let later = await ShazamShares.drain(
    queue: queue, store: store, now: minute(14).addingTimeInterval(ShazamShares.patience + 1)
  ) { _ in .later }
  #expect(later == songs)
  #expect(queue.pending().isEmpty)
}

@Test func theInboxJoinsASharedAppleSongToItsShazamRow() {
  let merged = ShazamInbox.merge([
    song("1", "Yellow", appleId: 11, at: 5),
    // The same song shared from Apple Music later.
    song("apple:11", "Yellow", appleId: 11, at: 30),
    song("apple:12", "Paradise", appleId: 12, at: 20),
  ])
  #expect(merged.map(\.shazamId) == ["1", "apple:12"])
  #expect(merged[0].found == Date(timeIntervalSince1970: 30 * 60))
}
