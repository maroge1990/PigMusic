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

extension WatchPublishLoop {
  /// How often the phone's bridge checks the Watch link and archive on its
  /// own while in the foreground (#178). Downloads, receipts, the link's state
  /// changes, the 45 s batch deferral and every library sync already publish,
  /// so this is only a safety net, paired or not.
  public nonisolated static let heartbeat: Duration = .seconds(30)
}
