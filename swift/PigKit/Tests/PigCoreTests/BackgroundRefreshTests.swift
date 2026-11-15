import Foundation
import PigDomain
import PigDownloads
import Testing

/// Answers each song by a fixed state, as the server's device copies do.
private actor PreparedCopies: DeviceCopySource {
  let states: [String: String]
  init(_ states: [String: String]) { self.states = states }
  func deviceCopy(trackId: String, quality: String, create: Bool) async throws -> DeviceCopy {
    let json: [String: Any] = [
      "state": states[trackId] ?? "preparing", "quality": quality,
      "sha256": String(repeating: "a", count: 64), "source_sha256": String(repeating: "b", count: 64),
      "bytes": 10, "extension": "m4a", "key": trackId,
    ]
    return try LibraryCodec.decoder().decode(
      DeviceCopy.self, from: JSONSerialization.data(withJSONObject: json))
  }
  func copyRequest(trackId: String, quality: String) async throws -> URLRequest {
    URLRequest(url: URL(string: "https://server.invalid/" + trackId)!)
  }
}

/// What one background refresh did, as the app wires it.
@MainActor private final class Launch {
  let fence = AccountFence()
  /// The scene's state: a launch for the refresh alone never became active.
  var inForeground = false
  var restorations = 0
  var handOffs = 0
  var rescheduled = 0
}

/// #223: iOS launched the app for the Watch refresh with no scene, so the
/// account was never restored and the refresh returned at once: the queued
/// Watch copies stayed queued and no further refresh was asked for.
@MainActor struct BackgroundRefreshTests {
  private func track(_ id: String) throws -> Track {
    try LibraryCodec.decoder().decode(
      Track.self, from: Data(#"{"id":"\#(id)","title":"\#(id)","artist":"Artist"}"#.utf8))
  }

  /// From fresh app state: the copies an earlier run queued are on disk, no
  /// account is attached, and the scene never became active.
  @Test func aLaunchForTheRefreshAloneRestoresTheAccountAndHandsTheQueuedCopiesOn() async throws
  {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    // An earlier run queued three Watch copies, then iOS ended it.
    try await DownloadArchive(directory: root).enqueue(
      [try track("a"), try track("b"), try track("c")], quality: "aac")

    let launch = Launch()
    let source = PreparedCopies(["a": "ready", "c": "ready"])
    var controller: DownloadController?
    var outcome: BackgroundHandOff.Outcome?
    let result = await BackgroundRefresh.run(
      attached: nil, fence: launch.fence, inForeground: { launch.inForeground },
      restore: {
        // The saved account: its Watch controller reads the saved copies.
        launch.restorations += 1
        let generation = launch.fence.advance()
        let restored = DownloadController(
          archive: try! DownloadArchive(directory: root), source: source)
        await restored.reload()
        controller = restored
        return .attached(generation: generation)
      },
      handOff: {
        launch.handOffs += 1
        guard let controller else { return }
        outcome = await BackgroundHandOff.run(
          controller.records, source: source, limit: 64, isCancelled: { launch.inForeground }
        ) { _, _, _ in }
        // The hand-off asks for another refresh while copies still wait.
        if outcome?.waiting.isEmpty == false { launch.rescheduled += 1 }
      },
      reschedule: { launch.rescheduled += 1 })
    #expect(result == .handedOff)
    #expect(launch.restorations == 1)
    #expect(controller?.records.map(\.state) == ["queued", "queued", "queued"])
    #expect(outcome?.handed == ["a", "c"])
    #expect(outcome?.waiting == ["b"])
    #expect(launch.rescheduled == 1)
  }

  @Test func anAccountAlreadyAttachedHandsOnWithoutRestoring() async {
    let launch = Launch()
    let result = await BackgroundRefresh.run(
      attached: launch.fence.generation, fence: launch.fence, inForeground: { false },
      restore: {
        launch.restorations += 1
        return .signedOut
      }, handOff: { launch.handOffs += 1 }, reschedule: { launch.rescheduled += 1 })
    #expect(result == .handedOff)
    #expect(launch.restorations == 0 && launch.handOffs == 1 && launch.rescheduled == 0)
  }

  /// A keychain still locked before the first unlock: the account is kept and
  /// another refresh is asked for. No saved account: nothing is asked for.
  @Test func aRestorationThatCannotFinishNowAsksForAnotherRefresh() async {
    let locked = Launch()
    let result = await BackgroundRefresh.run(
      attached: nil, fence: locked.fence, inForeground: { false },
      restore: { .unavailable(generation: locked.fence.generation) },
      handOff: { locked.handOffs += 1 }, reschedule: { locked.rescheduled += 1 })
    #expect(result == .rescheduled)
    #expect(locked.handOffs == 0 && locked.rescheduled == 1)

    let signedOut = Launch()
    #expect(
      await BackgroundRefresh.run(
        attached: nil, fence: signedOut.fence, inForeground: { false },
        restore: { .signedOut }, handOff: { signedOut.handOffs += 1 },
        reschedule: { signedOut.rescheduled += 1 }) == .stopped)
    #expect(signedOut.handOffs == 0 && signedOut.rescheduled == 0)
  }

  /// A sign-out while the account was being restored: nothing is handed on
  /// and no refresh is asked for the old account, whether it attached or not.
  @Test func aSignOutDuringTheRestorationStopsIt() async {
    for attaches in [true, false] {
      let launch = Launch()
      let result = await BackgroundRefresh.run(
        attached: nil, fence: launch.fence, inForeground: { false },
        restore: {
          let generation = launch.fence.advance()
          await Task.yield()
          launch.fence.end()  // The person signs out meanwhile.
          return attaches
            ? .attached(generation: generation) : .unavailable(generation: generation)
        }, handOff: { launch.handOffs += 1 }, reschedule: { launch.rescheduled += 1 })
      #expect(result == .stopped)
      #expect(launch.handOffs == 0 && launch.rescheduled == 0)
    }
  }

  /// The person opens the app while the refresh restores the account: the
  /// foreground's own work takes over.
  @Test func theForegroundArrivingDuringTheRestorationWins() async {
    let launch = Launch()
    let result = await BackgroundRefresh.run(
      attached: nil, fence: launch.fence, inForeground: { launch.inForeground },
      restore: {
        launch.inForeground = true
        return .attached(generation: launch.fence.advance())
      }, handOff: { launch.handOffs += 1 }, reschedule: { launch.rescheduled += 1 })
    #expect(result == .stopped)
    #expect(launch.handOffs == 0)
  }

  /// The scene and the background refresh restore the account once between
  /// them, whichever asks first; a later call starts a new run.
  @Test func oneRestorationIsSharedByEveryCallerWhileItRuns() async {
    let flight = SingleFlight<Int>()
    let launch = Launch()
    var open: CheckedContinuation<Void, Never>?
    let restore: @MainActor () async -> Int = {
      launch.restorations += 1
      await withCheckedContinuation { open = $0 }
      return launch.restorations
    }
    let background = Task { @MainActor in await flight.run(restore) }
    while open == nil { await Task.yield() }
    let scene = Task { @MainActor in await flight.run(restore) }
    for _ in 0..<10 { await Task.yield() }
    #expect(flight.isRunning)
    open?.resume()
    #expect(await background.value == 1)
    #expect(await scene.value == 1)
    #expect(launch.restorations == 1)
    #expect(!flight.isRunning)
    open = nil
    let later = Task { @MainActor in await flight.run(restore) }
    while open == nil { await Task.yield() }
    open?.resume()
    #expect(await later.value == 2)
  }
}
