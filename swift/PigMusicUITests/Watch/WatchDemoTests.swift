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

  /// The library's Now playing row keeps its play/pause button at the row's
  /// end, after the song, not straight after the title mid-row.
  func testTheRowsPauseButtonSitsAtItsEnd() {
    launchPlaying()
    let pause = app.buttons["Pause"]
    let title = app.staticTexts["First Light"]
    XCTAssertGreaterThan(pause.frame.maxX, app.frame.maxX * 0.85, app.debugDescription)
    XCTAssertGreaterThan(pause.frame.minX, title.frame.maxX)
    // A round button, leaving the song and artist their room.
    XCTAssertLessThan(pause.frame.width, app.frame.width * 0.3)
  }

  /// The system player draws the clock at the top; a title of our own
  /// overlapped it.
  func testThePlayerHasNoTitleOverTheClock() {
    launchPlaying()
    app.staticTexts["First Light"].tap()
    expect(app.staticTexts["The Orchard"])
    sleep(1)
    XCTAssertFalse(app.staticTexts["Now playing"].exists, app.debugDescription)
  }
}
