import Foundation

/// Runs saves one after another, in the order they were asked for. Playback
/// checkpoints were each saved in a task of their own, so an older position
/// could finish last and win, and the app resumed from an earlier spot (#97).
@MainActor public final class OrderedWrites {
  private var last: Task<Void, Never>?
  public init() {}
  public func add(_ write: @escaping @Sendable () async -> Void) {
    let previous = last
    last = Task {
      await previous?.value
      await write()
    }
  }
  /// Waits for every save asked for so far.
  public func drain() async { await last?.value }
}
