import Foundation

/// Audio the Watch received before the manifest naming it waits in its inbox
/// (#219). The inbox is bounded by count and size where files arrive; this
/// bounds it in time: a file no manifest claimed within `pendingLifetime` goes.
public enum CompanionInbox {
  public static let pendingLifetime: TimeInterval = 2 * 86400

  /// The inbox files to delete: each transfer (".transfer") older than the
  /// lifetime, with its envelope (".json"), and any envelope left without its
  /// transfer.
  public static func expired(_ files: [URL], now: Date, created: (URL) -> Date?) -> [URL] {
    let transfers = Set(
      files.filter { $0.pathExtension == "transfer" }.map {
        $0.deletingPathExtension().lastPathComponent
      })
    var gone: [URL] = []
    for file in files {
      let name = file.deletingPathExtension().lastPathComponent
      switch file.pathExtension {
      case "transfer":
        if let date = created(file), now.timeIntervalSince(date) > pendingLifetime {
          gone.append(file)
          gone.append(file.deletingPathExtension().appendingPathExtension("json"))
        }
      case "json" where !transfers.contains(name):
        // Written just after its transfer moves in; one this old is orphaned.
        if let date = created(file), now.timeIntervalSince(date) > 60 { gone.append(file) }
      default: continue
      }
    }
    return gone
  }
}
