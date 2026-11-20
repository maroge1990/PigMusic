import Foundation
import PigDomain
import Testing

struct BatteryMetricsTests {
  /// D-113: MetricKit's day figures go in the diagnostics export, labelled
  /// plainly, so a locked-phone test shows what the background work cost.
  @Test func batteryFiguresAreLabelledPlainly() {
    let start = Date(timeIntervalSince1970: 1_800_000_000)
    var metrics = BatteryMetrics(
      from: start, to: start.addingTimeInterval(86400), appVersion: "0.2.0 (1040)")
    metrics.foregroundTime = 1800
    metrics.backgroundTime = 5400
    metrics.backgroundAudioTime = 3600
    metrics.cpuTime = 42
    metrics.wifiDown = 120_000_000
    metrics.backgroundExits = ["watchdog": 1]
    let text = metrics.text()
    #expect(text.contains("Time running in the background: 1 h 30 min"))
    #expect(text.contains("of which playing audio: 1 h 0 min"))
    #expect(text.contains("Processor time (foreground and background): 42 s"))
    #expect(text.contains("Wi-Fi: 120.0 MB downloaded, not reported uploaded"))
    #expect(text.contains("Mobile data: not reported downloaded"))
    #expect(text.contains("Ended by iOS in the background: watchdog 1"))
    #expect(text.contains("Memory: not reported at its peak"))
  }

  /// #253: iOS ended the app nine times in a day for memory pressure while
  /// suspended; the export says how much memory the app held, at its peak
  /// and while suspended, so the next report shows whether it is the cause.
  @Test func memoryFiguresAreInTheExport() throws {
    let start = Date(timeIntervalSince1970: 1_800_000_000)
    var metrics = BatteryMetrics(
      from: start, to: start.addingTimeInterval(86400), appVersion: "0.2.0 (1040)")
    metrics.peakMemory = 412_000_000
    metrics.suspendedMemory = 96_500_000
    metrics.backgroundExits = ["memory pressure": 9]
    #expect(
      metrics.text().contains("Memory: 412.0 MB at its peak, 96.5 MB on average while suspended"))
    // Kept reports from before the figures still read.
    let older = try JSONDecoder().decode(
      BatteryMetrics.self,
      from: Data(#"{"from":0,"to":86400,"appVersion":"1","backgroundExits":{}}"#.utf8))
    #expect(older.peakMemory == nil && older.suspendedMemory == nil)
  }
}
