import Foundation

/// One day's battery-related figures for the app, as iOS's MetricKit reports
/// them (about once a day, for the day before), kept for the diagnostics
/// export (#219, D-113): after a test with the phone locked, they show what
/// the app's background work cost. MetricKit gives no battery percentage for
/// an app; it measures what uses the energy: time running, processor,
/// graphics, network and location.
public struct BatteryMetrics: Codable, Sendable, Equatable {
  public var from: Date
  public var to: Date
  public var appVersion: String
  /// Seconds.
  public var foregroundTime: Double?
  public var backgroundTime: Double?
  public var backgroundAudioTime: Double?
  public var backgroundLocationTime: Double?
  /// Seconds of processor time, foreground and background together.
  public var cpuTime: Double?
  public var gpuTime: Double?
  /// Bytes.
  public var wifiDown: Double?
  public var wifiUp: Double?
  public var cellularDown: Double?
  public var cellularUp: Double?
  public var diskWrites: Double?
  /// Bytes of memory at the day's peak, and on average while suspended
  /// (#253): iOS ends a suspended app with a larger footprint first when it
  /// needs memory, so these say whether the app's footprint is the cause.
  public var peakMemory: Double?
  public var suspendedMemory: Double?
  /// Times iOS ended the app in the background, by reason; reasons with none
  /// are left out.
  public var backgroundExits: [String: Int] = [:]
  public init(from: Date, to: Date, appVersion: String) {
    self.from = from
    self.to = to
    self.appVersion = appVersion
  }

  /// The export's lines for this report.
  public func text() -> String {
    func time(_ seconds: Double?) -> String {
      guard let seconds else { return "not reported" }
      let whole = Int(seconds.rounded())
      if whole < 60 { return "\(whole) s" }
      if whole < 3600 { return "\(whole / 60) min \(whole % 60) s" }
      return "\(whole / 3600) h \(whole % 3600 / 60) min"
    }
    func size(_ bytes: Double?) -> String {
      guard let bytes else { return "not reported" }
      return String(format: "%.1f MB", bytes / 1_000_000)
    }
    var lines = [
      "Period: \(from.formatted(.iso8601)) to \(to.formatted(.iso8601)) (app \(appVersion))",
      "Time in the foreground: \(time(foregroundTime))",
      "Time running in the background: \(time(backgroundTime))",
      "  of which playing audio: \(time(backgroundAudioTime))",
      "  of which using location: \(time(backgroundLocationTime))",
      "Processor time (foreground and background): \(time(cpuTime))",
      "Graphics processor time: \(time(gpuTime))",
      "Wi-Fi: \(size(wifiDown)) downloaded, \(size(wifiUp)) uploaded",
      "Mobile data: \(size(cellularDown)) downloaded, \(size(cellularUp)) uploaded",
      "Written to storage: \(size(diskWrites))",
      "Memory: \(size(peakMemory)) at its peak, \(size(suspendedMemory)) on average while suspended",
    ]
    if backgroundExits.isEmpty {
      lines.append("Ended by iOS in the background: never")
    } else {
      lines.append(
        "Ended by iOS in the background: "
          + backgroundExits.sorted { $0.key < $1.key }.map { "\($0.key) \($0.value)" }
          .joined(separator: ", "))
    }
    return lines.joined(separator: "\n")
  }
}
