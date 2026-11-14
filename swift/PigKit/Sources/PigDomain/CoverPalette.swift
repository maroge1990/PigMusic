import Foundation

/// An sRGB colour with components from 0 to 1.
public struct CoverRGB: Hashable, Sendable {
  public var red: Double
  public var green: Double
  public var blue: Double
  public init(red: Double, green: Double, blue: Double) {
    self.red = red
    self.green = green
    self.blue = blue
  }
  public init(_ red: Double, _ green: Double, _ blue: Double) {
    self.init(red: red, green: green, blue: blue)
  }

  /// WCAG 2 relative luminance.
  public var luminance: Double {
    func linear(_ value: Double) -> Double {
      value <= 0.04045 ? value / 12.92 : pow((value + 0.055) / 1.055, 2.4)
    }
    return 0.2126 * linear(red) + 0.7152 * linear(green) + 0.0722 * linear(blue)
  }
  /// WCAG 2 contrast ratio, 1 to 21.
  public func contrast(with other: CoverRGB) -> Double {
    let (a, b) = (luminance, other.luminance)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)
  }
  /// `self` moved towards `other` by `amount` (0 keeps self, 1 gives other).
  public func mixed(with other: CoverRGB, _ amount: Double) -> CoverRGB {
    CoverRGB(
      red + (other.red - red) * amount, green + (other.green - green) * amount,
      blue + (other.blue - blue) * amount)
  }

  var hsl: (hue: Double, saturation: Double, lightness: Double) {
    let high = max(red, green, blue)
    let low = min(red, green, blue)
    let lightness = (high + low) / 2
    let delta = high - low
    guard delta > 0.000_001 else { return (0, 0, lightness) }
    let saturation = delta / (1 - abs(2 * lightness - 1))
    var hue: Double
    if high == red {
      hue = (green - blue) / delta
    } else if high == green {
      hue = (blue - red) / delta + 2
    } else {
      hue = (red - green) / delta + 4
    }
    hue = (hue * 60).truncatingRemainder(dividingBy: 360)
    if hue < 0 { hue += 360 }
    return (hue, min(1, saturation), lightness)
  }
  init(hue: Double, saturation: Double, lightness: Double) {
    let chroma = (1 - abs(2 * lightness - 1)) * saturation
    let segment = hue / 60
    let x = chroma * (1 - abs(segment.truncatingRemainder(dividingBy: 2) - 1))
    let (r, g, b): (Double, Double, Double) =
      switch segment {
      case ..<1: (chroma, x, 0)
      case ..<2: (x, chroma, 0)
      case ..<3: (0, chroma, x)
      case ..<4: (0, x, chroma)
      case ..<5: (x, 0, chroma)
      default: (chroma, 0, x)
      }
    let m = lightness - chroma / 2
    self.init(r + m, g + m, b + m)
  }
}

/// The colour a cover lends its page (#199, as Mornye's `cover_palette.dart`):
/// a small quantizer over a downsampled cover. Pixels fall into coarse colour
/// boxes; the chosen colour is the box with the most pixels, weighted towards
/// colourful boxes, so a red band on a white sleeve tints red rather than white.
public enum CoverPalette {
  /// Boxes smaller than this share of the opaque pixels are noise.
  static let minimumShare = 0.03

  /// The cover's colour from RGBA8 pixels (any premultiplication), or nil
  /// when fewer than a handful of pixels are opaque.
  public static func swatch(rgba pixels: [UInt8]) -> CoverRGB? {
    // 3 bits a channel: 512 boxes, coarse enough that one printed colour
    // does not split across many boxes.
    var count = [Int](repeating: 0, count: 512)
    var sums = [Double](repeating: 0, count: 512 * 3)
    var opaque = 0
    var index = 0
    while index + 3 < pixels.count {
      let alpha = pixels[index + 3]
      if alpha >= 128 {
        // Undo premultiplication so a half-transparent edge keeps its hue.
        let scale = 255 / Double(alpha)
        let r = min(255, Double(pixels[index]) * scale)
        let g = min(255, Double(pixels[index + 1]) * scale)
        let b = min(255, Double(pixels[index + 2]) * scale)
        let box = (Int(r) >> 5) << 6 | (Int(g) >> 5) << 3 | (Int(b) >> 5)
        count[box] += 1
        sums[box * 3] += r
        sums[box * 3 + 1] += g
        sums[box * 3 + 2] += b
        opaque += 1
      }
      index += 4
    }
    guard opaque >= 16 else { return nil }
    var best: (score: Double, colour: CoverRGB)?
    for box in 0..<512 where count[box] > 0 {
      let share = Double(count[box]) / Double(opaque)
      guard share >= minimumShare else { continue }
      let n = Double(count[box]) * 255
      let colour = CoverRGB(sums[box * 3] / n, sums[box * 3 + 1] / n, sums[box * 3 + 2] / n)
      // Chroma (0 for greys, 1 for a pure hue) lifts a colourful box over a
      // larger neutral one, up to five times its weight.
      let chroma = max(colour.red, colour.green, colour.blue)
        - min(colour.red, colour.green, colour.blue)
      let score = share * (0.2 + chroma)
      if best == nil || score > best!.score { best = (score, colour) }
    }
    return best?.colour
  }
}

/// The text roles a tinted background has to keep readable, in one appearance.
public struct CoverTintRoles: Sendable {
  public var canvas: CoverRGB
  public var text: CoverRGB
  public var secondary: CoverRGB
  public var textAccent: CoverRGB
  /// Non-text marks (the rose play button, slider, rings): 3:1 is enough.
  public var accent: CoverRGB
  public init(
    canvas: CoverRGB, text: CoverRGB, secondary: CoverRGB, textAccent: CoverRGB,
    accent: CoverRGB
  ) {
    self.canvas = canvas
    self.text = text
    self.secondary = secondary
    self.textAccent = textAccent
    self.accent = accent
  }
}

/// How a cover's colour becomes a page background that keeps the brand's
/// text roles legible (brand 1.8 section 16: 4.5:1 for text, 3:1 for marks).
public enum CoverTint {
  /// Text contrast floor (WCAG AA for body-size text).
  public static let textFloor = 4.5
  /// Floor for non-text marks.
  public static let markFloor = 3.0

  /// The background colour at the tint's strongest point, or nil when no
  /// tint of this cover keeps every role above the floor (the page keeps the
  /// brand canvas). The cover's hue is kept; its lightness is moved to suit
  /// the appearance (a pale wash in light, a deep one in dark) and its
  /// saturation is capped, so the cover colours the page without shouting.
  public static func wash(_ swatch: CoverRGB, dark: Bool, roles: CoverTintRoles) -> CoverRGB? {
    let (hue, saturation, _) = swatch.hsl
    let toned = CoverRGB(
      hue: hue, saturation: min(saturation, dark ? 0.5 : 0.6), lightness: dark ? 0.24 : 0.82)
    // The strongest tint that passes; weaker ones sit closer to the canvas.
    for strength in stride(from: 0.7, through: 0.3, by: -0.1) {
      let candidate = roles.canvas.mixed(with: toned, strength)
      if passes(candidate, roles: roles) { return candidate }
    }
    return nil
  }

  /// Every role readable on `background`.
  public static func passes(_ background: CoverRGB, roles: CoverTintRoles) -> Bool {
    [roles.text, roles.secondary, roles.textAccent].allSatisfy {
      $0.contrast(with: background) >= textFloor
    } && roles.accent.contrast(with: background) >= markFloor
  }
}
