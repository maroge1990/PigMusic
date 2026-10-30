import Foundation
import PigDomain
import PigPlayback
import Testing

struct PlaybackRepositoryTests {
  @Test func leasesReuseFilesProtectReadersAndEvictOnlyInactiveSources() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: root) }
    let input = root.appendingPathComponent("input.wav")
    try Data(repeating: 7, count: 12).write(to: input)
    let repository = PlaybackSourceRepository(
      preparer: try AudioSourcePreparer(root: root, maximumFileBytes: 20, maximumTotalBytes: 40))
    let a = Track(id: "a", title: "A", artist: "Fixture")
    let b = Track(id: "b", title: "B", artist: "Fixture")
    let c = Track(id: "c", title: "C", artist: "Fixture")
    let first = try await repository.acquireLocal(a, file: input)
    let duplicate = try await repository.acquireLocal(a, file: input)
    #expect(first.id != duplicate.id)
    #expect(first.file == duplicate.file)
    let second = try await repository.acquireLocal(b, file: input)
    await repository.release(first)
    await #expect(throws: AudioSourceFailure.storageLimit) {
      try await repository.acquireLocal(c, file: input)
    }
    #expect(FileManager.default.fileExists(atPath: duplicate.file.path))
    await repository.release(duplicate)
    let third = try await repository.acquireLocal(c, file: input)
    #expect(!FileManager.default.fileExists(atPath: first.file.path))
    #expect(FileManager.default.fileExists(atPath: second.file.path))
    await repository.release(second)
    await repository.release(third)
    let reused = try await repository.acquireLocal(c, file: input)
    #expect(reused.file == third.file)
    let revisions = await repository.savedRevisions()
    #expect(Set(revisions.keys) == ["b", "c"])
    await repository.release(reused)
    await repository.reset()
    #expect(!FileManager.default.fileExists(atPath: reused.file.path))
    #expect(try Data(contentsOf: input) == Data(repeating: 7, count: 12))
  }

  @Test func forgettingChangedSongsKeepsEveryOtherCachedSource() async throws {
    // #76: one changed song used to reset every cached source and stop playback.
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: root) }
    let input = root.appendingPathComponent("input.wav")
    try Data(repeating: 7, count: 12).write(to: input)
    let repository = PlaybackSourceRepository(
      preparer: try AudioSourcePreparer(root: root, maximumFileBytes: 20, maximumTotalBytes: 1000))
    let changed = try await repository.acquireLocal(
      Track(id: "changed", title: "Changed", artist: "Fixture"), file: input)
    await repository.release(changed)
    let playing = try await repository.acquireLocal(
      Track(id: "playing", title: "Playing", artist: "Fixture"), file: input)
    let other = try await repository.acquireLocal(
      Track(id: "other", title: "Other", artist: "Fixture"), file: input)
    await repository.release(other)
    await repository.forget(ids: ["changed", "playing"])
    #expect(Set(await repository.savedRevisions().keys) == ["playing", "other"])
    #expect(!FileManager.default.fileExists(atPath: changed.file.path))
    #expect(FileManager.default.fileExists(atPath: playing.file.path))
    await repository.release(playing)
    await repository.reset()
  }

  @Test func householdAndRemoteSourcesAreNotCheckedAgainstPersonalLibrary() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: root) }
    let input = root.appendingPathComponent("input.wav")
    try Data(repeating: 7, count: 12).write(to: input)
    let repository = PlaybackSourceRepository(
      preparer: try AudioSourcePreparer(root: root, maximumFileBytes: 20, maximumTotalBytes: 1000))
    let states: [String: String?] = [
      "mine": "in_library", "legacy": nil, "household": "household_available",
      "requested": "requested", "failed": "failed", "remote": "not_in_library",
      // Mix and radio songs say whether you own the song, but play from the
      // mix cache under an identity the library never lists.
      "mix:edition:0": "in_library", "mix:edition:1": nil,
    ]
    for (id, state) in states {
      var track = Track(id: id, title: id, artist: "Fixture")
      track.serverState = state
      // Released sources stay cached, and cached sources are what a refresh checks.
      await repository.release(try await repository.acquireLocal(track, file: input))
      let personal = !id.hasPrefix("mix:") && (state == nil || state == "in_library")
      #expect(await repository.savedRevisions().keys.contains(id) == personal)
    }
    await repository.reset()
  }

  @Test func downloadedFilesPlayInPlaceWithoutTheStagingLimit() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: root) }
    let input = root.appendingPathComponent("long.flac")
    try Data(repeating: 7, count: 500).write(to: input)
    let repository = PlaybackSourceRepository(
      preparer: try AudioSourcePreparer(root: root, maximumFileBytes: 20, maximumTotalBytes: 40))
    let lease = try await repository.leaseLocal(input)
    #expect(lease.file == input)
    #expect(lease.byteCount == 500)
    await repository.release(lease)
    await repository.reset()
    #expect(FileManager.default.fileExists(atPath: input.path))
  }

  @Test func changedRevisionCreatesDistinctSourceAndLocalStagingIsBounded() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: root) }
    let input = root.appendingPathComponent("input.wav")
    try Data(repeating: 7, count: 12).write(to: input)
    let preparer = try AudioSourcePreparer(root: root, maximumFileBytes: 20, maximumTotalBytes: 40)
    let repository = PlaybackSourceRepository(preparer: preparer)
    var track = Track(id: "a", title: "A", artist: "Fixture")
    track.sourceRevision = "first"
    let first = try await repository.acquireLocal(track, file: input)
    await repository.release(first)
    track.sourceRevision = "second"
    let second = try await repository.acquireLocal(track, file: input)
    #expect(first.file != second.file)
    await repository.release(second)
    await repository.reset()
    try Data(repeating: 7, count: 21).write(to: input)
    await #expect(throws: AudioSourceFailure.tooLarge) { try await preparer.stageLocalFile(input) }
    await #expect(throws: AudioSourceFailure.invalidURL) {
      try await preparer.stageLocalFile(URL(string: "https://media.invalid/song.wav")!)
    }
    let files =
      (FileManager.default.enumerator(at: root, includingPropertiesForKeys: nil)?.allObjects
      as? [URL] ?? []).filter {
        (try? $0.resourceValues(forKeys: [.isRegularFileKey]).isRegularFile) == true
      }
    #expect(files.map { $0.resolvingSymlinksInPath() } == [input.resolvingSymlinksInPath()])
  }

  @Test func interruptionPreservesIntentButManualPauseAndDeniedResumeWin() {
    var intent = PlaybackIntent()
    intent.play()
    intent.beginInterruption()
    #expect(intent.wantsPlayback && !intent.canPlay)
    intent.endInterruption(shouldResume: true)
    #expect(intent.canPlay)
    intent.beginInterruption()
    intent.pause()
    intent.endInterruption(shouldResume: true)
    #expect(!intent.canPlay)
    intent.play()
    intent.beginInterruption()
    intent.endInterruption(shouldResume: false)
    #expect(!intent.wantsPlayback && !intent.interrupted)
  }

  /// 25 Oct diagnostics: headphones came out, iOS began an interruption and
  /// never ended it, and every Play afterwards prepared the song paused until
  /// the app was relaunched. The person's own Play ends it.
  @Test func playingAgainEndsAnInterruptionIOSNeverEnded() {
    var intent = PlaybackIntent()
    intent.play()
    intent.beginInterruption()
    intent.pause()
    // Automatic resumption still waits for the interruption to end.
    intent.play()
    #expect(!intent.canPlay)
    intent.choosePlay()
    #expect(intent.canPlay && !intent.interrupted)
  }
}
