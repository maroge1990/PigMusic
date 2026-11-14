import Foundation
import Observation

/// Destructive actions that can be undone act at once with an Undo (D-79,
/// #183), as on the web: the thing leaves the screen now, and the change is
/// sent when its Undo has been offered for `wait`, or at once when the app
/// leaves the foreground or the account changes. Undo before then sends
/// nothing. Changes are sent one at a time, in the order they were made.
@available(macOS 14, *)
@MainActor @Observable public final class UndoQueue {
  public struct Item: Identifiable, Sendable, Equatable {
    public let id: String
    /// What happened, in a few words: "Removed from Road trip".
    public let label: String
  }
  /// Waiting for their Undo to run out, oldest first.
  public private(set) var items: [Item] = []
  /// Being sent, or sent a moment ago: still hidden while the screen reloads.
  public private(set) var sending: Set<String> = []
  @ObservationIgnored private var actions: [String: @MainActor () async -> Bool] = [:]
  @ObservationIgnored private var timers: [String: Task<Void, Never>] = [:]
  @ObservationIgnored private var chain: Task<Void, Never>?
  @ObservationIgnored private let wait: Duration
  @ObservationIgnored private let linger: Duration
  /// `linger`: how long a sent thing stays hidden, so it doesn't flash back
  /// before its screen shows the change.
  public init(wait: Duration = .seconds(10), linger: Duration = .seconds(3)) {
    self.wait = wait
    self.linger = linger
  }

  /// Whether a screen should leave this thing out: waiting or being sent.
  public func hides(_ id: String) -> Bool {
    sending.contains(id) || actions[id] != nil
  }

  /// Offers Undo for `label`, then runs `commit`, which says whether the
  /// change was made (a failure shows the thing again). A second action for
  /// an id already waiting is ignored.
  public func schedule(
    _ id: String, label: String, commit: @escaping @MainActor () async -> Bool
  ) {
    guard actions[id] == nil, !sending.contains(id) else { return }
    actions[id] = commit
    items.append(Item(id: id, label: label))
    let wait = wait
    timers[id] = Task { [weak self] in
      do { try await Task.sleep(for: wait) } catch { return }
      self?.send(id)
    }
  }

  /// Takes the action back: nothing is sent.
  public func undo(_ id: String) {
    guard actions.removeValue(forKey: id) != nil else { return }
    timers.removeValue(forKey: id)?.cancel()
    items.removeAll { $0.id == id }
  }

  /// Sends every waiting action now, in order.
  public func sendAll() {
    for item in items { send(item.id) }
  }

  /// Sends everything waiting and returns once all of it has been sent.
  public func sendAllAndWait() async {
    sendAll()
    await chain?.value
  }

  private func send(_ id: String) {
    guard let action = actions.removeValue(forKey: id) else { return }
    timers.removeValue(forKey: id)?.cancel()
    items.removeAll { $0.id == id }
    sending.insert(id)
    let previous = chain
    let linger = linger
    chain = Task { [weak self] in
      await previous?.value
      guard await action() else {
        self?.sending.remove(id)
        return
      }
      Task { [weak self] in
        try? await Task.sleep(for: linger)
        self?.sending.remove(id)
      }
    }
  }
}
