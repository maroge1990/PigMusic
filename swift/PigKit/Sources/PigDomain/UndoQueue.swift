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
  @ObservationIgnored private var undoes: [String: @MainActor () -> Void] = [:]
  @ObservationIgnored private var timers: [String: Task<Void, Never>] = [:]
  @ObservationIgnored private var chain: Task<Void, Never>?
  /// Rises when the account ends: actions queued before it are dropped.
  @ObservationIgnored private var era = 0
  @ObservationIgnored private let wait: Duration
  @ObservationIgnored private let linger: Duration
  /// `linger`: how long a sent thing stays hidden, so it doesn't flash back
  /// before its screen shows the change.
  public init(wait: Duration = .seconds(10), linger: Duration = .seconds(3)) {
    self.wait = wait
    self.linger = linger
  }

  /// Whether a screen should leave this thing out: waiting or being sent.
  /// Read from observed state, so the screen that asked redraws when it is
  /// scheduled or undone (#206); `items` and `actions` change together.
  public func hides(_ id: String) -> Bool {
    sending.contains(id) || items.contains { $0.id == id }
  }

  /// The Undo id of removing a song from the person's library (D-108).
  public static func removalKey(_ songId: String) -> String { "library-remove:" + songId }

  /// Whether this song's removal from the library waits for its Undo or is
  /// being sent, under its own id or its library id (D-108, #214).
  public func removing(_ song: Track) -> Bool {
    hides(Self.removalKey(song.id))
      || song.libraryTrackId.map { hides(Self.removalKey($0)) } == true
  }

  /// `songs` less those whose removal from the library waits for its Undo
  /// or is being sent (D-108, #214): every screen that lists the person's
  /// songs shows the same, and Undo brings them back untouched.
  public func withoutRemoved(_ songs: [Track]) -> [Track] {
    guard pending(prefix: Self.removalKey("")) else { return songs }
    return songs.filter { !removing($0) }
  }

  /// Whether anything whose id starts with `prefix` is waiting or being sent.
  public func pending(prefix: String) -> Bool {
    items.contains { $0.id.hasPrefix(prefix) } || sending.contains { $0.hasPrefix(prefix) }
  }

  /// Offers Undo for `label`, then runs `commit`, which says whether the
  /// change was made (a failure shows the thing again). A second action for
  /// an id already waiting is ignored. `undone` runs if Undo is pressed: for
  /// a change made at once that Undo puts back (Up next, D-118).
  public func schedule(
    _ id: String, label: String, undone: (@MainActor () -> Void)? = nil,
    commit: @escaping @MainActor () async -> Bool
  ) {
    guard actions[id] == nil, !sending.contains(id) else { return }
    actions[id] = commit
    if let undone { undoes[id] = undone }
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
    undoes.removeValue(forKey: id)?()
  }

  /// Sends one waiting action now: its Undo leaves (an Up next removal whose
  /// queue has ended can no longer be undone).
  public func sendNow(_ id: String) { send(id) }

  /// Sends every waiting action now, in order.
  public func sendAll() {
    for item in items { send(item.id) }
  }

  /// Drops every action not yet started, waiting or queued behind a send:
  /// the account that made them has ended (Codex, batch 9).
  public func discardAll() {
    era += 1
    for timer in timers.values { timer.cancel() }
    timers = [:]
    actions = [:]
    undoes = [:]
    items = []
  }

  /// Sends everything waiting and returns once all of it has been sent.
  public func sendAllAndWait() async {
    sendAll()
    await chain?.value
  }

  private func send(_ id: String) {
    guard let action = actions.removeValue(forKey: id) else { return }
    undoes.removeValue(forKey: id)
    timers.removeValue(forKey: id)?.cancel()
    items.removeAll { $0.id == id }
    sending.insert(id)
    let previous = chain
    let linger = linger
    let era = era
    chain = Task { [weak self] in
      await previous?.value
      guard self?.era == era else {
        self?.sending.remove(id)
        return
      }
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
