import Foundation
import Observation

/// What an action that failed has to say (#245), such as a song menu's
/// request the server refused, shown once in the place the Undo bar uses.
public struct ActionNotice: Identifiable, Equatable, Sendable {
  public let id: Int
  public let text: String
  /// What the server's log knows the failure by, when it said.
  public let reference: String?
  public let posted: ContinuousClock.Instant
}

/// The app's one notice for a failed action (#245): a song menu's failure
/// used to be written to a status line drawn on only four pages, so on
/// Home, Search and every album, artist, playlist and mix page a failure
/// gave nothing but the error haptic. It shows wherever the Undo bar can,
/// for `lifetime` or until tapped. The bar and the notice share one place:
/// the newer of the two shows (`shown(over:)`).
@available(macOS 14, *)
@MainActor @Observable public final class ActionNotices {
  public enum Shown: Equatable {
    case notice(ActionNotice)
    case undo(UndoQueue.Item)
  }
  public private(set) var current: ActionNotice?
  @ObservationIgnored private var serial = 0
  @ObservationIgnored private var timer: Task<Void, Never>?
  @ObservationIgnored private let lifetime: Duration
  public init(lifetime: Duration = .seconds(6)) { self.lifetime = lifetime }

  /// Shows `text` in place of any notice showing, for `lifetime`.
  @discardableResult public func post(_ text: String, reference: String? = nil) -> ActionNotice {
    serial += 1
    let notice = ActionNotice(id: serial, text: text, reference: reference, posted: .now)
    current = notice
    timer?.cancel()
    let lifetime = lifetime
    let id = notice.id
    timer = Task { [weak self] in
      do { try await Task.sleep(for: lifetime) } catch { return }
      self?.dismiss(id)
    }
    return notice
  }

  /// Hides notice `id` (tapped, or its time ran out); a newer one stays.
  public func dismiss(_ id: Int) {
    guard current?.id == id else { return }
    timer?.cancel()
    timer = nil
    current = nil
  }

  /// Hides any notice: the account that the failure belonged to has ended.
  public func clear() {
    timer?.cancel()
    timer = nil
    current = nil
  }

  /// What the shared place shows, given the newest action waiting for its
  /// Undo: the newer of it and the notice. A notice hides an older Undo
  /// only while it shows; the Undo's own time runs on throughout.
  public func shown(over undo: UndoQueue.Item?) -> Shown? {
    switch (current, undo) {
    case (nil, nil): nil
    case (let notice?, nil): .notice(notice)
    case (nil, let undo?): .undo(undo)
    case (let notice?, let undo?): undo.since > notice.posted ? .undo(undo) : .notice(notice)
    }
  }
}
