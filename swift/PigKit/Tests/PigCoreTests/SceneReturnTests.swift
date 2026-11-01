import PigDomain
import Testing

@Test func aBriefInterruptionDoesNotRestartTheApp() {
  // #97: Control Centre or a notification for a moment changes nothing.
  #expect(!SceneReturn.restarts(away: .seconds(3), suspended: false))
  // Thirty seconds away, or a real background, starts again and refreshes.
  #expect(SceneReturn.restarts(away: .seconds(30), suspended: false))
  #expect(SceneReturn.restarts(away: .seconds(1), suspended: true))
}
