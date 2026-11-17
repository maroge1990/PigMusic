import Foundation
import Observation

/// What an action that failed has to say (#245), such as a song menu's
/// request the server refused, shown once beside the Undo bar.
public struct ActionNotice: Identifiable, Equatable, Sendable {
  public let id: Int
  public let text: String
  /// What the server's log knows the failure by, when it said.
  public let reference: String?
}

/// The app's one notice for a failed action (#245): a song menu's failure
/// used to be written to a status line drawn on only four pages, so on
/// Home, Search and every album, artist, playlist and mix page a failure
/// gave nothing but the error haptic. It shows wherever the Undo bar can,
/// for `lifetime` or until tapped, stacked above a waiting Undo, which it
/// never hides: an Undo's time runs on, and a hidden one could be sent
/// before the person could take it back (Codex on #245).
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
    let notice = ActionNotice(id: serial, text: text, reference: reference)
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

  /// What the Undo bar's place shows, top to bottom: the notice, then the
  /// newest action waiting for its Undo, each when there is one.
  public func stack(over undo: UndoQueue.Item?) -> [Shown] {
    (current.map { [.notice($0)] } ?? []) + (undo.map { [.undo($0)] } ?? [])
  }
}
