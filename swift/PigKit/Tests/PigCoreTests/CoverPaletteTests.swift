import PigDomain
import Testing

/// Brand roles from Pig family 1.9.0 (`PigBrand` tokens), in both appearances.
private let lightRoles = CoverTintRoles(
  canvas: CoverRGB(0.98431373, 0.97254902, 0.98039216),
  text: CoverRGB(0.10980392, 0.08627451, 0.12549020),
  secondary: CoverRGB(0.36078431, 0.32549020, 0.39215686),
  textAccent: CoverRGB(0.69019608, 0.13725490, 0.33725490),
  accent: CoverRGB(0.83921569, 0.20000000, 0.42352941))
private let darkRoles = CoverTintRoles(
  canvas: CoverRGB(0.08235294, 0.06666667, 0.10196078),
  text: CoverRGB(0.96078431, 0.94509804, 0.95686275),
  secondary: CoverRGB(0.65490196, 0.62352941, 0.68235294),
  textAccent: CoverRGB(0.93725490, 0.47843137, 0.68235294),
  accent: CoverRGB(0.93725490, 0.47843137, 0.68235294))

/// A cover of solid colour blocks: `(r, g, b, a, pixels)`.
private func cover(_ blocks: [(UInt8, UInt8, UInt8, UInt8, Int)]) -> [UInt8] {
  blocks.flatMap { block in
    Array(repeating: [block.0, block.1, block.2, block.3], count: block.4).flatMap { $0 }
  }
}

private func close(_ colour: CoverRGB?, _ red: Double, _ green: Double, _ blue: Double) -> Bool {
  guard let colour else { return false }
  return abs(colour.red - red) < 0.02 && abs(colour.green - green) < 0.02
    && abs(colour.blue - blue) < 0.02
}

@Test func paletteChoosesTheColourfulBoxOverALargerNeutralOne() {
  // A white sleeve with a red band: the page tints red, not white.
  let pixels = cover([(250, 250, 250, 255, 700), (200, 30, 40, 255, 300)])
  #expect(close(CoverPalette.swatch(rgba: pixels), 200 / 255, 30 / 255, 40 / 255))
}

@Test func paletteTakesTheLargestColourWhenTwoAreAlikeInChroma() {
  let pixels = cover([(30, 60, 200, 255, 600), (200, 40, 40, 255, 400)])
  #expect(close(CoverPalette.swatch(rgba: pixels), 30 / 255, 60 / 255, 200 / 255))
}

@Test func paletteIgnoresSpecksAndTransparentPixels() {
  // 1% green specks are noise; transparent pink never counts.
  let pixels = cover([
    (40, 40, 40, 255, 990), (0, 255, 0, 255, 10), (255, 0, 200, 0, 5000),
  ])
  #expect(close(CoverPalette.swatch(rgba: pixels), 40 / 255, 40 / 255, 40 / 255))
}

@Test func paletteNeedsOpaquePixels() {
  #expect(CoverPalette.swatch(rgba: []) == nil)
  #expect(CoverPalette.swatch(rgba: cover([(255, 0, 0, 0, 400)])) == nil)
}

@Test func washKeepsEveryTextRoleAboveTheAAFloorInBothAppearances() {
  // A sweep of hues, saturations and lightnesses, including black and white.
  var tinted = 0
  for red in stride(from: 0.0, through: 1, by: 0.125) {
    for green in stride(from: 0.0, through: 1, by: 0.125) {
      for blue in stride(from: 0.0, through: 1, by: 0.125) {
        let swatch = CoverRGB(red, green, blue)
        for (dark, roles) in [(false, lightRoles), (true, darkRoles)] {
          guard let wash = CoverTint.wash(swatch, dark: dark, roles: roles) else { continue }
          tinted += 1
          for role in [roles.text, roles.secondary, roles.textAccent] {
            #expect(role.contrast(with: wash) >= 4.5)
          }
          #expect(roles.accent.contrast(with: wash) >= 3)
          // Dark stays dark and light stays light.
          #expect(dark ? wash.luminance < 0.1 : wash.luminance > 0.4)
        }
      }
    }
  }
  // The floor is a guard, not a filter that removes the tint from most covers.
  #expect(tinted > 9 * 9 * 9 * 2 * 9 / 10)
}

@Test func washIsAHintOfTheCoversHue() throws {
  let red = try #require(CoverTint.wash(CoverRGB(0.8, 0.1, 0.1), dark: false, roles: lightRoles))
  #expect(red.red > red.green + 0.04 && red.red > red.blue + 0.04)
  let blue = try #require(CoverTint.wash(CoverRGB(0.1, 0.2, 0.8), dark: true, roles: darkRoles))
  #expect(blue.blue > blue.red + 0.04 && blue.blue > blue.green + 0.04)
}

@Test func washIsTheCanvasWithAQuarterOfTheToneBlendedIn() {
  // D-96: brand first, a hint of cover. The wash is the canvas moved a
  // quarter of the way to the toned cover colour, never the raw cover.
  let swatch = CoverRGB(0.18, 0.38, 0.55)
  for (dark, roles) in [(false, lightRoles), (true, darkRoles)] {
    let tone = CoverTint.tone(swatch, dark: dark)
    let expected = roles.canvas.mixed(with: tone, 0.25)
    #expect(CoverTint.wash(swatch, dark: dark, roles: roles) == expected)
  }
}

@Test func washStaysCloseToTheCanvasForEveryCover() {
  // Even a pure, saturated cover moves no channel of the canvas by more
  // than 0.08 and its luminance by no more than a quarter: the canvas stays
  // the page's colour and the rose accent its only strong one.
  for red in stride(from: 0.0, through: 1, by: 0.25) {
    for green in stride(from: 0.0, through: 1, by: 0.25) {
      for blue in stride(from: 0.0, through: 1, by: 0.25) {
        for (dark, roles) in [(false, lightRoles), (true, darkRoles)] {
          guard let wash = CoverTint.wash(CoverRGB(red, green, blue), dark: dark, roles: roles)
          else { continue }
          let canvas = roles.canvas
          let moved = max(
            abs(wash.red - canvas.red), abs(wash.green - canvas.green),
            abs(wash.blue - canvas.blue))
          #expect(moved <= 0.08)
          #expect(wash.contrast(with: canvas) < 1.25)
        }
      }
    }
  }
}

@Test func washFallsBackToTheCanvasWhenTheBlendFailsTheFloor() {
  // Roles that fail the floor on the blend: no tint, the plain canvas.
  var weak = lightRoles
  weak.secondary = CoverRGB(0.7, 0.7, 0.7)
  #expect(CoverTint.wash(CoverRGB(0.2, 0.5, 0.2), dark: false, roles: weak) == nil)
}

@Test func contrastMatchesWCAG() {
  #expect(abs(CoverRGB(0, 0, 0).contrast(with: CoverRGB(1, 1, 1)) - 21) < 0.001)
  #expect(abs(CoverRGB(0.5, 0.5, 0.5).contrast(with: CoverRGB(0.5, 0.5, 0.5)) - 1) < 0.001)
}
