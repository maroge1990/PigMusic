import Foundation
import PigDomain
import Testing

/// #228, D-109/D-110: the three Downloads pages group transfers by album, and
/// the mark on the right of a song or an album is its control.
struct TransferMarksTests {
  private func record(
    _ id: String, state: String = "queued", paused: Bool = false, album: String? = "Album",
    track: Int? = nil, origin: DownloadOrigin? = nil
  ) -> DownloadRecord {
    var song = Track(id: id, title: id, artist: "Artist", album: album)
    song.trackNumber = track
    var record = DownloadRecord(track: song, quality: "original")
    record.state = state
    record.paused = paused ? true : nil
    record.origins = origin.map { [$0] }
    return record
  }

  /// #246, D-116: one mark for each state; a failed song shows the
  /// exclamation circle, not the retry arrows, and a waiting one the clock.
  @Test func eachStateHasOneMark() {
    #expect(TransferMark.waiting.symbol == "clock")
    #expect(TransferMark.downloading(0.5).symbol == nil)
    #expect(TransferMark.paused.symbol == "pause.circle")
    #expect(TransferMark.failed.symbol == "exclamationmark.circle")
    #expect(TransferMark.done.symbol == "checkmark.circle.fill")
  }

  @Test func aPhoneSongsMarkPausesResumesAndRetries() {
    #expect(
      TransferControl.phone(record("a"), progress: nil) == TransferControl(.waiting, tap: .pause))
    #expect(
      TransferControl.phone(record("a", state: "downloading"), progress: 0.4)
        == TransferControl(.downloading(0.4), tap: .pause))
    #expect(
      TransferControl.phone(record("a", paused: true), progress: nil)
        == TransferControl(.paused, tap: .resume))
    // Paused while downloading: still the pause circle, and a tap resumes.
    #expect(
      TransferControl.phone(record("a", state: "downloading", paused: true), progress: 0.4)
        == TransferControl(.paused, tap: .resume))
    #expect(
      TransferControl.phone(record("a", state: "failed"), progress: nil)
        == TransferControl(.failed, tap: .retry))
    #expect(
      TransferControl.phone(record("a", state: "ready"), progress: nil)
        == TransferControl(.done, tap: nil))
    // A ready song's leftover pause flag doesn't make it look paused.
    #expect(
      TransferControl.phone(record("a", state: "ready", paused: true), progress: nil).mark == .done)
    #expect(TransferControl(.waiting, tap: .pause).label == "Pause")
    #expect(TransferControl(.paused, tap: .resume).label == "Resume")
    #expect(TransferControl(.failed, tap: .retry).label == "Try again")
    #expect(TransferControl(.done, tap: nil).label == nil)
  }

  /// TestFlight 1012: most Watch transfers wait for the Watch, ready on the
  /// iPhone, and their clock mark did nothing, so Mark couldn't pause them.
  @Test func aWatchSongWaitingForTheWatchPausesAndResumes() {
    #expect(
      TransferControl.watch(record("a", state: "ready"), onWatch: false, progress: nil)
        == TransferControl(.waiting, tap: .pause))
    var held = record("a", state: "ready")
    held.fileName = "a.m4a"
    held.checksum = "c-a"
    held.held = true
    #expect(held.isHeld)
    #expect(
      TransferControl.watch(held, onWatch: false, progress: nil)
        == TransferControl(.paused, tap: .resume))
    // A ready song's leftover #127 pause flag is no hold.
    #expect(
      TransferControl.watch(record("a", state: "ready", paused: true), onWatch: false, progress: nil)
        == TransferControl(.waiting, tap: .pause))
    // On the Watch (or confirmed there), nothing is left to pause.
    #expect(
      TransferControl.watch(held, onWatch: true, progress: nil)
        == TransferControl(.done, tap: nil))
    var delivered = held
    delivered.fileName = nil
    #expect(!delivered.isHeld)
    #expect(
      TransferControl.watch(record("a", state: "ready"), onWatch: true, progress: nil)
        == TransferControl(.done, tap: nil))
    // An album of songs waiting for the Watch pauses them all, then resumes them.
    let waiting = TransferGroupControl(
      ["a", "b"].map {
        ($0, TransferControl.watch(record($0, state: "ready"), onWatch: false, progress: nil))
      })
    #expect(waiting.tap == .pause)
    #expect(waiting.ids == ["a", "b"])
    let paused = TransferGroupControl([
      ("a", TransferControl.watch(held, onWatch: false, progress: nil)),
      ("b", TransferControl.watch(held, onWatch: false, progress: nil)),
      ("c", TransferControl.watch(record("c", state: "ready"), onWatch: true, progress: nil)),
    ])
    #expect(paused.mark == .paused)
    #expect(paused.tap == .resume)
    #expect(paused.ids == ["a", "b"])
    #expect(
      TransferControl.watch(record("a"), onWatch: false, progress: nil)
        == TransferControl(.waiting, tap: .pause))
    #expect(
      TransferControl.watch(record("a", state: "downloading"), onWatch: false, progress: 2)
        == TransferControl(.downloading(1), tap: .pause))
    #expect(
      TransferControl.watch(record("a", paused: true), onWatch: false, progress: nil)
        == TransferControl(.paused, tap: .resume))
    #expect(
      TransferControl.watch(record("a", state: "failed"), onWatch: false, progress: nil)
        == TransferControl(.failed, tap: .retry))
  }

  @Test func aServerRequestsMarkFollowsItsStatus() {
    for status in ["waiting_for_sources", "queued", "searching", "validating"] {
      #expect(
        TransferControl.server(status: status, progress: nil)
          == TransferControl(.waiting, tap: .pause), "\(status)")
    }
    #expect(
      TransferControl.server(status: "downloading", progress: 0.25)
        == TransferControl(.downloading(0.25), tap: .pause))
    #expect(
      TransferControl.server(status: "paused", progress: nil)
        == TransferControl(.paused, tap: .resume))
    #expect(
      TransferControl.server(status: "failed", progress: nil)
        == TransferControl(.failed, tap: .retry))
    #expect(
      TransferControl.server(status: "cancelled", progress: nil)
        == TransferControl(.failed, tap: .retry))
    #expect(
      TransferControl.server(status: "completed", progress: nil) == TransferControl(.done, tap: nil))
    // Adding to the library can't be paused.
    #expect(
      TransferControl.server(status: "importing", progress: nil)
        == TransferControl(.waiting, tap: nil))
  }

  @Test func anAlbumsMarkRetriesEveryFailedSongFirst() {
    let album = TransferGroupControl([
      ("a", TransferControl(.failed, tap: .retry)),
      ("b", TransferControl(.downloading(0.5), tap: .pause)),
      ("c", TransferControl(.failed, tap: .retry)),
      ("d", TransferControl(.done, tap: nil)),
    ])
    #expect(album.mark == .failed)
    #expect(album.tap == .retry)
    #expect(album.ids == ["a", "c"])
    #expect(album.label() == "Try again")
    #expect(album.progress == 0.375)
    #expect(
      album.summary(done: "on your iPhone")
        == "1 of 4 songs on your iPhone · 1 downloading · 2 couldn't download")
  }

  @Test func anAlbumsMarkPausesWhatIsStillGoingThenResumesIt() {
    let going = TransferGroupControl([
      ("a", TransferControl(.paused, tap: .resume)),
      ("b", TransferControl(.downloading(0.5), tap: .pause)),
      ("c", TransferControl(.waiting, tap: .pause)),
      ("d", TransferControl(.done, tap: nil)),
    ])
    #expect(going.mark == .downloading(0.375))
    #expect(going.tap == .pause)
    #expect(going.ids == ["b", "c"])
    #expect(going.label("playlist") == "Pause playlist")
    let waiting = TransferGroupControl([
      ("a", TransferControl(.waiting, tap: .pause)), ("b", TransferControl(.done, tap: nil)),
    ])
    #expect(waiting.mark == .waiting)
    let paused = TransferGroupControl([
      ("a", TransferControl(.paused, tap: .resume)),
      ("b", TransferControl(.paused, tap: .resume)),
      ("c", TransferControl(.done, tap: nil)),
      // A step with nothing to pause: the album counts as paused.
      ("d", TransferControl(.waiting, tap: nil)),
    ])
    #expect(paused.mark == .paused)
    #expect(paused.tap == .resume)
    #expect(paused.ids == ["a", "b"])
    #expect(paused.label() == "Resume album")
  }

  @Test func aFinishedAlbumShowsTheCheckAndDoesNothing() {
    let done = TransferGroupControl([
      ("a", TransferControl(.done, tap: nil)), ("b", TransferControl(.done, tap: nil)),
    ])
    #expect(done.mark == .done)
    #expect(done.tap == nil)
    #expect(done.ids.isEmpty)
    #expect(done.label() == nil)
    #expect(done.summary(done: "on Apple Watch") == "2 of 2 songs on Apple Watch")
    let sending = TransferGroupControl([
      ("a", TransferControl(.waiting, tap: nil)), ("b", TransferControl(.done, tap: nil)),
    ])
    #expect(sending.mark == .waiting)
    #expect(sending.tap == nil)
  }

  @Test func transfersAreGroupedByAlbumAndPlaylistUnfinishedFirst() {
    let mix = DownloadOrigin(playlistId: "p1", playlistName: "Mix")
    let records = [
      record("b2", state: "ready", album: "Blue", track: 2),
      record("a1", state: "ready", album: "Amber", track: 1),
      record("b1", state: "downloading", album: "Blue", track: 1),
      record("m", state: "ready", album: "Other", origin: mix),
      record("lone", state: "queued", album: nil),
    ]
    let groups = DownloadGrouping.transfers(records) { $0.state == "ready" }
    #expect(groups.map(\.title) == ["Blue", "lone", "Mix", "Amber"])
    #expect(groups[0].records.map(\.id) == ["b1", "b2"])
    #expect(groups[0].lead == "Artist")
    #expect(groups[2].lead == "Playlist")
    #expect(groups[2].origin == mix)
    #expect(groups[3].origin == .direct)
    // The device sections keep their order: playlists, then albums by title.
    #expect(DownloadGrouping.build(records).map(\.title) == ["Mix", "Amber", "Blue", "lone"])
  }
}

/// Codex, batch 21 round 2: one song finishing before its pause must not
/// leave the album's later songs downloading.
struct TransferBatchTests {
  struct Finished: Error {}
  struct SignedOut: Error {}
  actor Calls {
    var ids: [String] = []
    func add(_ id: String) { ids.append(id) }
  }

  @Test func aSongThatAlreadyFinishedDoesNotStopTheRest() async {
    let calls = Calls()
    let failure = await TransferBatch.run(["a", "b", "c"], stops: { $0 is SignedOut }) { id in
      await calls.add(id)
      if id == "a" { throw Finished() }
    }
    #expect(await calls.ids == ["a", "b", "c"])
    #expect(failure is Finished)
  }

  @Test func signingOutStopsTheRun() async {
    let calls = Calls()
    let failure = await TransferBatch.run(["a", "b", "c"], stops: { $0 is SignedOut }) { id in
      await calls.add(id)
      if id == "a" { throw SignedOut() }
    }
    #expect(await calls.ids == ["a"])
    #expect(failure is SignedOut)
  }

  @Test func allDoneGivesNoFailure() async {
    let failure = await TransferBatch.run(["a"], stops: { _ in true }) { _ in }
    #expect(failure == nil)
  }
}
