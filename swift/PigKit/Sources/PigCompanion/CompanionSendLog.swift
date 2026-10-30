import Foundation

/// When the phone last handed each manifest ("manifest:<revision>") or song
/// ("audio:<id>:<checksum>") to WatchConnectivity, so nothing is queued twice.
/// An entry is due again after `retryAfter` without a confirmation.
public struct CompanionSendLog: Sendable, Equatable {
  public static let retryAfter: TimeInterval = 600
  private var sent: [String: Date] = [:]
  public init() {}
  public func due(_ key: String, now: Date = Date()) -> Bool {
    sent[key].map { now.timeIntervalSince($0) > Self.retryAfter } ?? true
  }
  public mutating func mark(_ key: String, at date: Date = Date()) { sent[key] = date }
  /// A new library version or a reconnect: manifests go again, but songs already
  /// handed over keep their marks, or every unconfirmed song crosses again each
  /// time the Watch app comes to the foreground (#70).
  public mutating func forgetManifests() {
    sent = sent.filter { key, _ in !key.hasPrefix("manifest:") }
  }
  /// Another account, a newer Watch revision or a reset: everything goes again.
  public mutating func reset() { sent = [:] }
}
