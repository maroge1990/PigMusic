import Foundation

/// The server's own share of a request, from its `Server-Timing` header, so
/// a slow wait in the diagnostics log says whether the server or the
/// connection held it up. Request IDs match the server's log lines.
public enum ServerTiming {
  /// "server 17100 ms: mix-lease 16900, auth-ready 3": the server's total and
  /// its slowest phases of at least `minimum` ms. Nil without a total.
  public static func summary(_ header: String?, minimum: Double = 20) -> String? {
    guard let header else { return nil }
    var total: Double?
    var phases: [(name: String, ms: Double)] = []
    for entry in header.split(separator: ",") {
      let parts = entry.split(separator: ";").map { $0.trimmingCharacters(in: .whitespaces) }
      guard let name = parts.first, !name.isEmpty,
        let ms = parts.dropFirst().first(where: { $0.hasPrefix("dur=") })
          .flatMap({ Double($0.dropFirst(4)) }), ms.isFinite
      else { continue }
      if name == "app" { total = ms } else { phases.append((name, ms)) }
    }
    guard let total else { return nil }
    let slow = phases.filter { $0.ms >= minimum }.sorted { $0.ms > $1.ms }.prefix(4)
    let detail = slow.map { "\($0.name) \(Int($0.ms.rounded()))" }.joined(separator: ", ")
    return "server \(Int(total.rounded())) ms" + (detail.isEmpty ? "" : ": " + detail)
  }

  /// A fresh request ID: 16 hex digits, short enough to survive log redaction.
  public static func newRequestID() -> String {
    let value = String(UInt64.random(in: .min ... .max), radix: 16)
    return String(repeating: "0", count: 16 - value.count) + value
  }
}
