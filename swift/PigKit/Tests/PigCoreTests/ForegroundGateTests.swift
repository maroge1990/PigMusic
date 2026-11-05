import PigDomain
import Testing

struct ForegroundGateTests {
  private let start = ContinuousClock.now

  @Test func theFirstActivationStartsTheForegroundWork() {
    var gate = ForegroundGate()
    #expect(gate.suspended)
    #expect(
      gate.change(active: true, now: start)
        == .back(away: .zero, renewConnections: false, restart: true))
    #expect(!gate.suspended && gate.active)
  }

  @Test func aMomentOfControlCentreChangesNothing() {
    var gate = ForegroundGate()
    _ = gate.change(active: true, now: start)
    #expect(gate.change(active: false, now: start) == .stay)
    #expect(!gate.active && !gate.suspended)
    #expect(
      gate.change(active: true, now: start + .seconds(3))
        == .back(away: .seconds(3), renewConnections: false, restart: false))
  }

  @Test func theBackgroundSuspendsOnceAndTheReturnRestarts() {
    var gate = ForegroundGate()
    _ = gate.change(active: true, now: start)
    #expect(gate.change(active: false, now: start) == .stay)
    // Inactive first, then the background: timed from the first.
    #expect(gate.change(active: false, background: true, now: start + .seconds(1)) == .suspend)
    #expect(gate.change(active: false, background: true, now: start + .seconds(2)) == .stay)
    #expect(gate.suspended)
    #expect(
      gate.change(active: true, now: start + .seconds(5))
        == .back(away: .seconds(5), renewConnections: false, restart: true))
    #expect(!gate.suspended)
  }

  @Test func longAwayRenewsConnectionsAndRestartsWithoutTheBackground() {
    var gate = ForegroundGate()
    _ = gate.change(active: true, now: start)
    _ = gate.change(active: false, now: start)
    #expect(
      gate.change(active: true, now: start + .seconds(31))
        == .back(away: .seconds(31), renewConnections: true, restart: true))
  }
}
