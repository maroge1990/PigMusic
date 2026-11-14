import PigCompanion
import Testing

/// A publish that can be held open, as one waiting on the archive is.
@MainActor private final class Publisher {
  var runs: [Bool] = []
  private var gate: CheckedContinuation<Void, Never>?
  private var reached: CheckedContinuation<Void, Never>?
  var holdNext = false
  func publish(force: Bool) async {
    runs.append(force)
    if holdNext {
      holdNext = false
      reached?.resume()
      reached = nil
      await withCheckedContinuation { gate = $0 }
    }
  }
  func waitUntilHeld() async { await withCheckedContinuation { reached = $0 } }
  func release() {
    gate?.resume()
    gate = nil
  }
}

/// `WatchPhoneController.publish` before the fix.
@MainActor private final class OldGuard {
  var publishing = false
  func publish(_ publisher: Publisher, force: Bool) async {
    guard !publishing else { return }
    publishing = true
    defer { publishing = false }
    await publisher.publish(force: force)
  }
}

@MainActor struct WatchPublishLoopTests {
  @Test func theOldGuardDropsAPublishDuringAnother() async {
    let publisher = Publisher()
    let old = OldGuard()
    publisher.holdNext = true
    let first = Task { @MainActor in await old.publish(publisher, force: false) }
    await publisher.waitUntilHeld()
    await old.publish(publisher, force: true)  // A receipt arrives.
    publisher.release()
    await first.value
    #expect(publisher.runs == [false])
  }

  @Test func aPublishDuringAnotherRunsAfterItForced() async {
    let publisher = Publisher()
    let loop = WatchPublishLoop()
    publisher.holdNext = true
    let first = Task { @MainActor in
      await loop.run(force: false) { await publisher.publish(force: $0) }
    }
    await publisher.waitUntilHeld()
    // Several requests while it runs: one more publish, forced if any was.
    await loop.run(force: false) { await publisher.publish(force: $0) }
    await loop.run(force: true) { await publisher.publish(force: $0) }
    #expect(loop.isRunning)
    publisher.release()
    await first.value
    #expect(publisher.runs == [false, true])
    #expect(!loop.isRunning)
    // The force was used up.
    await loop.run(force: false) { await publisher.publish(force: $0) }
    #expect(publisher.runs == [false, true, false])
  }
}

@Test func theBridgeHeartbeatIsASafetyNetNotAPoll() {
  // #178: events publish; the foreground check ran every 2 s while paired.
  #expect(WatchPublishLoop.heartbeat >= .seconds(30))
}
