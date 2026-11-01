import XCTest

/// The Watch app on its own, before the phone has sent anything. It drives
/// the PigMusicWatch app already installed in the simulator.
@MainActor
final class WatchLaunchTests: XCTestCase {
  func testAnEmptyWatchSaysHowToSendMusic() {
    let app = XCUIApplication(bundleIdentifier: "au.com.pig.music.watchkitapp")
    app.launch()
    XCTAssertTrue(app.staticTexts["No music on your Watch yet"].waitForExistence(timeout: 30))
    XCTAssertTrue(
      app.staticTexts["On iPhone, use Send to Apple Watch on a playlist, artist or album."].exists)
  }
}
