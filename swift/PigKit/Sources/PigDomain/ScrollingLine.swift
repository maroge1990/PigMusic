import Foundation

/// How a one-line name too long for its width scrolls on the full player
/// (D-65): after a pause it slides at a steady speed to show its end, pauses,
/// and slides back. A name that fits stays still.
public struct ScrollPlan: Hashable, Sendable {
  /// Seconds the start (and the end) of the name stays in view.
  public static let pause = 2.0
  /// Points a second, slow enough to read.
  public static let speed = 30.0
  /// How far the name moves: what doesn't fit, in whole points.
  public let distance: Double
  public init(textWidth: Double, boxWidth: Double) {
    distance = boxWidth > 0 ? max(0, (textWidth - boxWidth).rounded(.up)) : 0
  }
  /// Less than a point over is drawn as it is.
  public var scrolls: Bool { distance > 1 }
  /// Seconds the slide to the end takes; never a jump.
  public var duration: Double { max(1, distance / Self.speed) }
}
