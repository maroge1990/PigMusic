import Foundation
import PigDomain

/// One Watch publish at a time, without dropping requests (#97). The phone
/// publishes after every download, receipt and playlist change; a request
/// that arrived while another publish was running was dropped, so a receipt
/// or a finished song could wait for some later, unrelated change. Now the
/// running publish goes round once more, forced if any request asked for it.
@MainActor public final class WatchPublishLoop {
  private let loop = RefreshLoop()
  private var force = false
  public init() {}
  public var isRunning: Bool { loop.isRunning }
  /// Runs `work`, or, while a publish runs, asks it to run again.
  public func run(force: Bool, _ work: (_ force: Bool) async -> Void) async {
    self.force = self.force || force
    await loop.run {
      let forced = self.force
      self.force = false
      await work(forced)
    }
  }
}
