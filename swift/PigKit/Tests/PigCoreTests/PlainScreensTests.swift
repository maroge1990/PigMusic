import Foundation
@testable import PigDomain
import PigStore
import Testing

/// D-120 (#251): the iPhone's plain screens say things in plain words.
struct PlainScreensTests {
  private func song(_ id: String, _ seconds: Double? = 240) -> Track {
    var track = Track(id: id, title: "Song " + id, artist: "Artist")
    track.duration = seconds
    return track
  }

  /// Changes not sent yet showed "Append" and "queued"; now each change reads
  /// as what it does, and its state as Waiting to send or Couldn't send.
  @Test func aPendingChangeReadsInPlainWords() {
    var append = PendingMutation(
      mutation: OfflineMutation(action: "append", playlistId: "p1", track: song("1")))
    let waiting = PendingChangeWords(append, playlistName: "Road Trip")
    #expect(waiting.title == "Add to playlist Road Trip")
    #expect(waiting.detail == "Song 1 · Artist")
    #expect(waiting.status == "Waiting to send")
    #expect(!waiting.failed)
    append.state = "rejected"
    let refused = PendingChangeWords(append)
    #expect(refused.title == "Add to playlist")
    #expect(refused.status == "Couldn't send. Try again.")
    #expect(refused.failed)
    let unfavourite = PendingMutation(
      mutation: OfflineMutation(action: "favourite", track: song("2"), favourite: false))
    #expect(PendingChangeWords(unfavourite).title == "Remove from favourites")
    let favourite = PendingMutation(
      mutation: OfflineMutation(action: "favourite", track: song("2"), favourite: true))
    #expect(PendingChangeWords(favourite).title == "Favourite")
    let create = PendingMutation(mutation: OfflineMutation(action: "create", name: "Gym"))
    #expect(PendingChangeWords(create).title == "New playlist")
    #expect(PendingChangeWords(create).detail == "Gym")
    #expect(waiting.title != "Append")
    #expect(PendingChangeWords(PendingMutation(mutation: OfflineMutation(action: "x"))).status
      != "queued")
    #expect(PendingChangeWords.sendLabel(count: 3) == "Send 3 changes now")
    #expect(PendingChangeWords.sendLabel(count: 1) == "Send 1 change now")
  }

  /// Try again puts a refused change back in line, without its old refusal.
  @Test func tryingAgainQueuesTheChangeOnceMore() {
    var pending = PendingMutation(mutation: OfflineMutation(action: "append", playlistId: "p"))
    pending.state = "conflict"
    pending.message = "This playlist changed on the server."
    let retried = pending.retried()
    #expect(retried.state == "queued")
    #expect(retried.message == nil)
    #expect(retried.id == pending.id)
  }

  /// "Household default: mp3_320" and a Save button became a list with a
  /// tick whose default says "MP3 320 kbps"; a tap saves the row's value.
  @Test func yourMusicQualityListsTheDefaultInWords() {
    let settings = QualitySettings(
      preferences: [QualityItem(scope: "system", itemId: "*", preferred: "aac_256")],
      options: [
        QualityOption(value: "lossless", label: "Lossless (FLAC)"),
        QualityOption(value: "mp3_320", label: "MP3 320 kbps"),
        QualityOption(value: "aac_256", label: "AAC 256 kbps"),
      ], householdDefault: "mp3_320")
    let choices = QualityChoices(settings)
    #expect(choices.rows.map(\.title) == [
      "Household default", "Lossless (FLAC)", "MP3 320 kbps", "AAC 256 kbps",
    ])
    #expect(choices.rows[0].detail == "MP3 320 kbps")
    #expect(choices.rows[0].value == nil)
    #expect(choices.selected == "aac_256")
    #expect(choices.summary == "AAC 256 kbps")
    let inherited = QualityChoices(
      QualitySettings(preferences: [], options: settings.options, householdDefault: "mp3_320"))
    #expect(inherited.selected == nil)
    #expect(inherited.summary == "MP3 320 kbps")
    // An album's own choice is not the account's.
    let elsewhere = QualityChoices(
      QualitySettings(
        preferences: [QualityItem(scope: "album", itemId: "a", preferred: "lossless")],
        options: settings.options, householdDefault: "mp3_320"))
    #expect(elsewhere.selected == nil)
  }

  /// Up next: your songs with their count and time, then similar songs as
  /// their own section; an empty queue says Nothing next.
  @Test func upNextSplitsYourSongsFromSimilarOnes() {
    let upcoming = [song("a"), song("b"), song("s1"), song("s2")]
    let layout = UpNextLayout(upcoming, keepPlaying: true, similarIDs: ["s1", "s2"])
    #expect(layout.chosen.map(\.id) == ["a", "b"])
    #expect(layout.similar.map(\.id) == ["s1", "s2"])
    #expect(!layout.nothingNext)
    #expect(layout.showsSimilarHeading)
    #expect(!layout.offersShuffle)
    #expect(SongSpan.summary(layout.chosen) == "2 songs · 8 min")

    let empty = UpNextLayout([Track](), keepPlaying: false, similarIDs: [])
    #expect(empty.nothingNext)
    #expect(!empty.showsSimilarHeading)
    #expect(empty.offersShuffle)
    // Keep playing on: similar songs follow once the queue ends.
    let keep = UpNextLayout([Track](), keepPlaying: true, similarIDs: [])
    #expect(keep.nothingNext)
    #expect(keep.showsSimilarHeading)
    let onlySimilar = UpNextLayout([song("s1")], keepPlaying: true, similarIDs: ["s1"])
    #expect(onlySimilar.nothingNext)
    #expect(!onlySimilar.offersShuffle)
    #expect(UpNextLayout<Track>.nothingNextTitle == "Nothing next")
    #expect(UpNextLayout<Track>.similarTitle == "Then similar songs")
  }

  /// Seamless playback and Crossfade sit with Keep playing similar songs.
  @Test func preferencesHoldEveryPlaybackSwitch() {
    #expect(
      PlaybackSwitch.shown(seamless: true).map(\.title) == [
        "Keep playing similar songs", "Seamless playback", "Crossfade",
      ])
    #expect(PlaybackSwitch.shown(seamless: false) == [.keepPlaying, .seamless])
  }

  @Test func aSpanReadsInSongsAndMinutes() {
    #expect(SongSpan.summary(count: 1, seconds: 0) == "1 song")
    #expect(SongSpan.summary(count: 4, seconds: 16 * 60) == "4 songs · 16 min")
    #expect(SongSpan.summary(count: 2, seconds: 20) == "2 songs · 1 min")
    #expect(SongSpan.summary(count: 30, seconds: 125 * 60) == "30 songs · 2 h 5 min")
    #expect(SongSpan.summary(count: 30, seconds: 120 * 60) == "30 songs · 2 h")
  }

  /// The Downloads tab's cards: a gauge and what is moving now.
  @Test func theDownloadsCardsShowStorageAndCounts() {
    let gigabyte: Int64 = 1024 * 1024 * 1024
    let phone = StorageGauge(used: gigabyte * 41 / 10, limit: 10 * gigabyte)
    #expect(phone.text == "4.1 GB of 10 GB Storage limit")
    #expect(abs(phone.fraction - 0.41) < 0.001)
    #expect(StorageGauge(used: 450 * 1024 * 1024, limit: 2 * gigabyte).text
      == "450 MB of 2 GB Storage limit")
    #expect(StorageGauge(used: 3 * gigabyte, limit: gigabyte).fraction == 1)
    #expect(StorageGauge(used: 1, limit: 0).fraction == 0)
    #expect(DownloadPlace.phone.hubSummary(songs: 312, active: 2) == "312 songs · 2 downloading")
    #expect(DownloadPlace.phone.hubSummary(songs: 1, active: 0) == "1 song")
    #expect(
      DownloadPlace.watch.hubSummary(songs: 70, active: 4)
        == "70 songs · 4 waiting for Apple Watch")
    #expect(
      DownloadPlace.server.hubSummary(songs: 1284, active: 3)
        == "\(1284.formatted()) songs in your library · 3 adding")
  }

  /// Shuffle your library reaches past the first page by title.
  @Test func shuffleYourLibraryDrawsFromTheWholeLibrary() async throws {
    let store = try LibraryStore(path: ":memory:")
    let tracks = (0..<300).map { Track(id: String($0), title: String(format: "%03d", $0), artist: "A") }
    try await store.replace(LibrarySnapshot(revision: "one", tracks: tracks))
    var seen = Set<String>()
    for _ in 0..<5 { seen.formUnion(try await store.shuffledTracks(limit: 200).map(\.id)) }
    #expect(seen.contains { Int($0)! >= 200 })
    #expect(try await store.shuffledTracks(limit: 10).count == 10)
  }
}
