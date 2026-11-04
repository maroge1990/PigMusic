import Foundation

/// Free space for a song the person asked to keep. The plain available
/// capacity leaves out purgeable space (caches, offloadable data) that the
/// system frees on demand for important files, so a nearly full device
/// refused downloads it had room for (#97). Where the system gives no
/// important-usage figure (watchOS has none), the plain one is used.
public enum VolumeSpace {
  public static func available(at directory: URL) throws -> Int64? {
    #if os(watchOS)
      let values = try directory.resourceValues(forKeys: [.volumeAvailableCapacityKey])
      return values.volumeAvailableCapacity.map(Int64.init)
    #else
      let values = try directory.resourceValues(forKeys: [
        .volumeAvailableCapacityForImportantUsageKey, .volumeAvailableCapacityKey,
      ])
      return choose(
        important: values.volumeAvailableCapacityForImportantUsage,
        plain: values.volumeAvailableCapacity.map(Int64.init))
    #endif
  }
  /// The important-usage figure when the system gives one, else the plain one.
  public static func choose(important: Int64?, plain: Int64?) -> Int64? {
    if let important, important > 0 { return important }
    return plain
  }
}
