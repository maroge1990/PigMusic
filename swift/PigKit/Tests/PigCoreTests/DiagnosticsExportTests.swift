import Foundation
import PigDomain
import Testing

// #149: the 5 Nov device export carried a weeks-old crash from another build,
// 139 lines of the same offline failure, and refresh timings of 6 and 10 hours
// (the phone asleep in between).

/// A clock the test moves by hand.
private final class Hands: @unchecked Sendable {
  private let lock = NSLock()
  private var now: Duration = .zero
  func set(_ value: Duration) { lock.withLock { now = value } }
  var read: @Sendable () -> Duration { { [self] in lock.withLock { now } } }
}

struct DiagnosticsExportTests {
  @Test func timeInTheBackgroundIsNotCounted() {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let hands = Hands()
    let log = DiagnosticsLog(directory: root, clock: hands.read)
    hands.set(.milliseconds(100))
    let began = log.activeTime
    hands.set(.milliseconds(250))
    log.background()  // The phone sleeps mid-refresh.
    hands.set(.seconds(10 * 3600))
    log.foreground()
    hands.set(.seconds(10 * 3600) + .milliseconds(50))
    #expect(log.activeTime - began == .milliseconds(200))
    // A second background while still in the background changes nothing.
    log.background()
    log.background()
    hands.set(.seconds(11 * 3600))
    log.foreground()
    #expect(log.activeTime - began == .milliseconds(200))
  }

  @Test func aRepeatedFailureIsLoggedOnceWithACount() {
    var run = FailureRun()
    let offline = { run.failed(key: "offline -1009", line: "refresh failed: offline") }
    #expect(offline() == ["refresh failed: offline"])
    #expect(offline() == [])
    #expect(offline() == [])
    // A different failure ends the run with its count, then logs itself.
    #expect(
      run.failed(key: "server 500", line: "refresh failed: server")
        == ["the same failure repeated 2 more times", "refresh failed: server"])
    #expect(run.succeeded() == [])
    #expect(offline() == ["refresh failed: offline"])
    #expect(offline() == [])
    #expect(run.succeeded() == ["the same failure repeated 1 more time"])
    #expect(run.succeeded() == [])
  }

  @Test func onlyThisBuildsCrashReportsAreExported() throws {
    func payload(_ build: String) throws -> Data {
      try JSONSerialization.data(withJSONObject: [
        "timeStampBegin": "2026-10-15 01:00:00",
        "crashDiagnostics": [
          ["diagnosticMetaData": ["appBuildVersion": build, "appVersion": "0.2.0"]]
        ],
      ])
    }
    #expect(CrashReportBuild.matches(try payload("21"), build: "21"))
    #expect(!CrashReportBuild.matches(try payload("3"), build: "21"))
    #expect(!CrashReportBuild.matches(Data("not json".utf8), build: "21"))
    #expect(!CrashReportBuild.matches(Data("{}".utf8), build: "21"))
  }
}
