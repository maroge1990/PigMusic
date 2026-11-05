import Foundation

/// Which signed-in account the app's work belongs to, and the long-running
/// tasks that end with it (#46).
///
/// Every change of account (sign-in, sign-out, a revoked session) advances
/// the generation. Work that awaits captures the generation first and checks
/// it after each await, so an answer that arrives for the old account is
/// dropped instead of landing in the new one. Tasks the account owns (live
/// updates, a poll) are cancelled together when it ends.
@MainActor public final class AccountFence {
  public private(set) var generation = 0
  private var owned: [String: Task<Void, Never>] = [:]
  public init() {}

  /// A new account begins: everything started under the old one is stale.
  /// Owned tasks keep running; they see the new generation and stop.
  @discardableResult public func advance() -> Int {
    generation += 1
    return generation
  }
  /// The account ends: advances and cancels every task it owns.
  public func end() {
    advance()
    let tasks = owned.values
    owned = [:]
    for task in tasks { task.cancel() }
  }
  /// Whether the account a stamp was taken under is still the current one.
  public func isCurrent(_ stamp: Int) -> Bool { generation == stamp }
  /// Throws `CancellationError` when the account changed since `stamp`.
  public func check(_ stamp: Int) throws {
    guard generation == stamp else { throw CancellationError() }
  }
  /// Runs `operation` for the current account; its answer is dropped with
  /// `CancellationError` when the account changed while it ran.
  public func fenced<T>(_ operation: () async throws -> T) async throws -> T {
    let stamp = generation
    let value = try await operation()
    try check(stamp)
    return value
  }

  /// Keeps `task` under `key` until the account ends or it is cancelled by
  /// key. A task already kept under that key is cancelled.
  public func own(_ key: String, _ task: Task<Void, Never>) {
    owned.updateValue(task, forKey: key)?.cancel()
  }
  /// The task kept under `key`, if any.
  public func task(_ key: String) -> Task<Void, Never>? { owned[key] }
  /// Cancels and forgets the task kept under `key`.
  public func cancel(_ key: String) {
    owned.removeValue(forKey: key)?.cancel()
  }
}
