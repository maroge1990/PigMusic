import Foundation

/// One delayed run at a time (#123). The pending run always clears itself
/// when its wait ends, whether it then runs or finds its account gone, so a
/// later run can be scheduled; it never clears a newer one scheduled after
/// it was cancelled.
@MainActor public final class DeferredRun {
  private var pending: (id: Int, task: Task<Void, Never>)?
  private var next = 0
  public init() {}
  /// Whether a run is waiting.
  public var isPending: Bool { pending != nil }
  /// Schedules `run` after `delay` unless one is already waiting. `isCurrent`
  /// is asked when the wait ends; the run is skipped (and cleared) if not.
  public func schedule(
    after delay: Duration, isCurrent: @escaping @MainActor () -> Bool,
    run: @escaping @MainActor () async -> Void
  ) {
    guard pending == nil else { return }
    next += 1
    let id = next
    let task = Task { [weak self] in
      try? await Task.sleep(for: delay)
      guard let self, self.pending?.id == id else { return }
      self.pending = nil
      guard !Task.isCancelled, isCurrent() else { return }
      await run()
    }
    pending = (id, task)
  }
  /// Drops a waiting run (the account ended).
  public func cancel() {
    pending?.task.cancel()
    pending = nil
  }
}
