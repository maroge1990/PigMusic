import XCTest

/// The Watch's Debug demo library (`--demo`): three generated songs in its
/// own archive, playing from launch with `--demo-play`. WatchConnectivity
/// file transfers don't reach a Watch simulator, so this stands in for music
/// sent from the phone.
@MainActor
final class WatchDemoTests: XCTestCase {
  private let app = XCUIApplication(bundleIdentifier: "au.com.pig.music.watchkitapp")

  private func expect(
    _ element: XCUIElement, timeout: TimeInterval = 10,
    file: StaticString = #filePath, line: UInt = #line
  ) {
    if !element.waitForExistence(timeout: timeout) {
      print(app.debugDescription)
      XCTFail("Not found: \(element)", file: file, line: line)
    }
  }

  private func launchPlaying() {
    continueAfterFailure = false
    app.launchArguments = ["--demo", "--demo-play"]
    app.launch()
    expect(app.staticTexts["First Light"], timeout: 30)
    expect(app.buttons["Pause"])
  }

  func testTheDemoLibraryPlays() {
    launchPlaying()
  }
}
