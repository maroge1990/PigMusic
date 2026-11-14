import Foundation

/// Collapses a run of the same failure into its first line and a count (#149):
/// offline, every library refresh failed the same way and logged a line each
/// time (139 of 2,402 lines in one export).
public struct FailureRun: Sendable, Equatable {
  private var key: String?
  private var repeats = 0
  public init() {}
  /// The lines to log for a failure: its own line when it differs from the
  /// last one (after a count of that one's repeats), and while it repeats only
  /// a running count at 10, 100, 1,000… repeats, so a long outage stays in
  /// the log even if the app ends before it does (Codex on 43e2844).
  public mutating func failed(key: String, line: String) -> [String] {
    if key == self.key {
      repeats += 1
      return Self.milestone(repeats) ? ["the same failure has repeated \(repeats) times so far"] : []
    }
    let ended = summary()
    self.key = key
    repeats = 0
    return ended + [line]
  }
  /// The count of the run that just ended, if it repeated.
  public mutating func succeeded() -> [String] {
    let ended = summary()
    key = nil
    repeats = 0
    return ended
  }
  /// The run still going, for an export taken before it ends.
  public var pending: String? {
    repeats > 0 ? "the same failure has repeated \(repeats) more \(repeats == 1 ? "time" : "times") so far" : nil
  }
  private static func milestone(_ count: Int) -> Bool {
    var step = 10
    while step < count { step *= 10 }
    return step == count
  }
  private func summary() -> [String] {
    repeats > 0 ? ["the same failure repeated \(repeats) more \(repeats == 1 ? "time" : "times")"] : []
  }
}
