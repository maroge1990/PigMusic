import XCTest

/// Tap-through checks against the disposable HTTPS test server
/// (scripts/serve_ui_test_server.py), which scripts/simulator_ui_tests.py
/// starts and names through PIG_UI_SERVER, PIG_UI_USERNAME and
/// PIG_UI_PASSWORD. Skipped without it.
@MainActor
final class ServerTests: XCTestCase {
  private let app = XCUIApplication(bundleIdentifier: "au.com.pig.music")

  private func expect(
    _ element: XCUIElement, timeout: TimeInterval = 10,
    file: StaticString = #filePath, line: UInt = #line
  ) {
    if !element.waitForExistence(timeout: timeout) {
      print(app.debugDescription)
      XCTFail("Not found: \(element)", file: file, line: line)
    }
  }

  /// Signs in when the app shows the sign-in form; returns on Home.
  private func signIn() throws {
    let environment = ProcessInfo.processInfo.environment
    guard let server = environment["PIG_UI_SERVER"], let username = environment["PIG_UI_USERNAME"],
      let password = environment["PIG_UI_PASSWORD"]
    else { throw XCTSkip("No test server: run scripts/simulator_ui_tests.py") }
    continueAfterFailure = false
    app.launch()
    let address = app.textFields["https://music.example.com"]
    if address.waitForExistence(timeout: 15) {
      address.tap()
      // The app keeps the last server's address.
      let kept = (address.value as? String) ?? ""
      if kept != address.placeholderValue {
        address.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: kept.count))
      }
      address.typeText(server)
      app.textFields["Username"].tap()
      app.textFields["Username"].typeText(username)
      app.secureTextFields["Password"].tap()
      app.secureTextFields["Password"].typeText(password)
      app.buttons["Sign in"].tap()
    }
    expect(app.staticTexts["Find your next favourite."], timeout: 60)
  }

  /// iOS offers to save the password over whatever screen is showing, up to
  /// half a minute after signing in; every tap first sends it away.
  private func tap(_ element: XCUIElement, file: StaticString = #filePath, line: UInt = #line) {
    expect(element, file: file, line: line)
    let prompt = app.buttons["Not Now"]
    let deadline = Date.now.addingTimeInterval(15)
    while prompt.exists || !element.isHittable {
      if prompt.exists { prompt.tap() }
      if Date.now > deadline {
        XCTFail("Not tappable: \(element)\n\(app.debugDescription)", file: file, line: line)
      }
      sleep(1)
    }
    element.tap()
  }

  /// Sends an album to the paired Watch simulator. The iPhone then fetches the
  /// Watch's copies; the transfer itself never reaches a Watch simulator.
  func testSendAnAlbumToTheWatch() throws {
    try signIn()
    tap(app.tabBars.buttons["Library"])
    let artist = app.buttons["The Test Pilots, 1 album"]
    expect(artist, timeout: 30)
    tap(artist)
    tap(app.buttons.matching(NSPredicate(format: "label BEGINSWITH 'Night Drive'")).firstMatch)
    expect(app.buttons["Play Last Exit by The Test Pilots"])
    tap(app.buttons["Download"])
    tap(app.buttons["Send to Apple Watch"])
    let alert = app.alerts["Apple Watch"]
    expect(alert)
    XCTAssertTrue(
      alert.staticTexts.matching(
        NSPredicate(format: "label BEGINSWITH 'Sending 3 songs to Apple Watch.'")
      ).firstMatch.exists, alert.debugDescription)
    tap(alert.buttons["OK"])
  }
}
