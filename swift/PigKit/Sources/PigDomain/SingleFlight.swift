import Foundation

/// One run of an operation at a time, shared by every caller that asks while
/// it runs (#223). The launch restores the signed-in account once whether the
/// scene or a background refresh asks first; a call after it finished starts
/// a new run.
@MainActor public final class SingleFlight<Value: Sendable> {
  private var running: Task<Value, Never>?
  public init() {}

  /// Whether a run is under way.
  public var isRunning: Bool { running != nil }

  /// Joins the run under way, or starts `operation` when there is none.
  public func run(_ operation: @escaping @MainActor () async -> Value) async -> Value {
    if let running { return await running.value }
    let task = Task { @MainActor in await operation() }
    running = task
    let value = await task.value
    if running == task { running = nil }
    return value
  }
}
