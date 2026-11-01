import Foundation

/// Whether coming back to the app starts its foreground work again (#97).
/// A glance at Control Centre or a notification makes the app inactive for
/// a moment; stopping and restarting live updates, downloads and Watch
/// copying, then refreshing the library, each time cost battery for nothing.
/// Only a real trip to the background, or 30 s away, does.
public enum SceneReturn {
  public static let longAway: Duration = .seconds(30)
  public static func restarts(away: Duration, suspended: Bool) -> Bool {
    suspended || away >= longAway
  }
}
