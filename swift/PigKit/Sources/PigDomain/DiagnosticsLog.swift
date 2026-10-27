import Foundation

/// Private, on-device lifecycle breadcrumbs for diagnosing crashes and stuck
/// transfers: connection changes, sync revisions, transfers, playback state and
/// audio routes. Nothing leaves the device unless the person exports it.
///
/// Entries never hold credentials or signed links: addresses keep only their
/// host, and token-like values are masked before anything is written.
public final class DiagnosticsLog: @unchecked Sendable {
  /// Tools off the device (the playback harness) keep their log apart by
  /// setting `PIG_MUSIC_DIAGNOSTICS` to a folder before first use.
  public static let shared = DiagnosticsLog(
    directory: ProcessInfo.processInfo.environment["PIG_MUSIC_DIAGNOSTICS"].map {
      URL(fileURLWithPath: $0, isDirectory: true)
    })

  private let queue = DispatchQueue(label: "au.com.pig.music.diagnostics")
  private let file: URL?
  private let marker: URL?
  private let limit: Int
  private var lines: [String]
  /// True when the previous run ended without reaching the background or a
  /// clean exit: most often a crash or a system termination.
  public let previousRunEndedUnexpectedly: Bool

  public init(directory: URL? = nil, limit: Int = 1500) {
    let base =
      directory
      ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first?
      .appendingPathComponent("Diagnostics")
    self.limit = limit
    if let base {
      try? FileManager.default.createDirectory(at: base, withIntermediateDirectories: true)
      var excluded = base
      var values = URLResourceValues()
      values.isExcludedFromBackup = true
      try? excluded.setResourceValues(values)
    }
    file = base?.appendingPathComponent("lifecycle.log")
    marker = base?.appendingPathComponent("running")
    lines =
      file.flatMap { try? String(contentsOf: $0, encoding: .utf8) }?
      .split(separator: "\n").map(String.init) ?? []
    previousRunEndedUnexpectedly = marker.map { FileManager.default.fileExists(atPath: $0.path) }
      ?? false
  }

  /// Call once at launch. Records how the previous run ended.
  public func launched(_ app: String, version: String) {
    if previousRunEndedUnexpectedly {
      record("lifecycle", "previous run ended unexpectedly (crash or termination)")
    }
    record("lifecycle", "\(app) \(version) launched")
    setRunning(true)
  }
  /// The app is active again after the background.
  public func foreground() {
    record("lifecycle", "foreground")
    setRunning(true)
  }
  /// Reaching the background counts as a clean end: the system may end a
  /// suspended app at any time without that being a crash.
  public func background() {
    record("lifecycle", "background")
    setRunning(false)
  }

  public func record(_ category: String, _ message: String) {
    let stamp = ISO8601DateFormatter.string(
      from: Date(), timeZone: .current, formatOptions: [.withInternetDateTime, .withFractionalSeconds])
    let line = "\(stamp) [\(category)] \(Self.redact(message))"
    queue.async { [self] in
      lines.append(String(line.prefix(400)))
      if lines.count > limit + limit / 4 { lines.removeFirst(lines.count - limit) }
      persist()
    }
  }

  /// The newest entries, oldest first, for export or to send to the phone.
  public func text(maximumBytes: Int = 512 * 1024) -> String {
    queue.sync {
      var result: [String] = []
      var size = 0
      for line in lines.reversed() {
        size += line.utf8.count + 1
        if size > maximumBytes { break }
        result.append(line)
      }
      return result.reversed().joined(separator: "\n")
    }
  }

  public func clear() {
    queue.sync {
      lines = []
      persist()
    }
  }

  private func setRunning(_ running: Bool) {
    guard let marker else { return }
    queue.async {
      if running {
        FileManager.default.createFile(atPath: marker.path, contents: Data())
      } else {
        try? FileManager.default.removeItem(at: marker)
      }
    }
  }

  private func persist() {
    guard let file else { return }
    try? Data(lines.joined(separator: "\n").utf8).write(
      to: file, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
  }

  /// Keeps diagnostics free of secrets: links keep only their host, and
  /// bearer tokens, signatures and long opaque values are masked.
  public static func redact(_ message: String) -> String {
    var text = message
    let patterns: [(String, String)] = [
      (#"https?://([^/\s?#]+)[^\s]*"#, "https://$1/…"),
      (#"(?i)(bearer|token|password|signature|sig|key|code)([=:\s]+)[^\s&,;]+"#, "$1$2•••"),
      (#"[A-Za-z0-9_\-]{32,}"#, "•••"),
    ]
    for (pattern, template) in patterns {
      text = text.replacingOccurrences(of: pattern, with: template, options: .regularExpression)
    }
    return text
  }
}

/// The server build, from `/api/v1/server`. Servers older than this endpoint
/// have none, which apps report as "update the server".
public struct ServerInfo: Codable, Sendable, Equatable {
  public var version: String
  public var revision: String
  public var apiLevel: Int
  public var capabilities: [String]
  public init(version: String, revision: String, apiLevel: Int, capabilities: [String]) {
    self.version = version
    self.revision = revision
    self.apiLevel = apiLevel
    self.capabilities = capabilities
  }
  /// The server level this app was built against.
  public static let expectedLevel = 2
}
