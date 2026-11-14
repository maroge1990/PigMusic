import Foundation

/// Crash reports MetricKit delivers can be from any earlier build: an export
/// for build 21 carried a debug build 3 crash from weeks before (#149).
public enum CrashReportBuild {
  /// Whether a payload's JSON (MXDiagnosticPayload.jsonRepresentation) holds a
  /// diagnostic from this build. Unreadable payloads are left out.
  public static func matches(_ json: Data, build: String) -> Bool {
    guard
      let payload = try? JSONSerialization.jsonObject(with: json) as? [String: Any]
    else { return false }
    let kinds = ["crashDiagnostics", "hangDiagnostics", "cpuExceptionDiagnostics",
                 "diskWriteExceptionDiagnostics", "appLaunchDiagnostics"]
    let builds = kinds.flatMap { (payload[$0] as? [[String: Any]]) ?? [] }
      .compactMap { ($0["diagnosticMetaData"] as? [String: Any])?["appBuildVersion"] as? String }
    return builds.contains(build)
  }
}
