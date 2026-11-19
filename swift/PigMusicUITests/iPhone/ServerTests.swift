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
    // Signed in: the tab bar's Home tab shows (the Home subtitle is gone, #215).
    expect(app.tabBars.buttons["Home"], timeout: 60)
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
    // D-103: sending shows no pop-up and no text, only a brief checkmark.
    XCTAssertFalse(app.alerts["Apple Watch"].waitForExistence(timeout: 5), app.debugDescription)
    XCTAssertFalse(
      app.staticTexts.matching(NSPredicate(format: "label CONTAINS 'Apple Watch'")).firstMatch
        .exists, app.debugDescription)
    XCTAssertFalse(app.staticTexts["Connect and refresh your library on iPhone first."].exists)
    expect(app.buttons["Download"])
  }

  /// D-120 (#251): Your music quality saves on a tap, with no Save button,
  /// and the household default says its quality in words.
  func testYourMusicQualitySavesOnATap() throws {
    try signIn()
    let quality = app.buttons.matching(NSPredicate(format: "label BEGINSWITH 'Your music quality'"))
      .firstMatch
    let aac = app.buttons["AAC 256 kbps"].firstMatch
    // Just after sign-in the Account sheet can be sent away (iOS's password
    // prompt): open it again until the page shows.
    for _ in 0..<4 where !aac.exists {
      if !quality.exists { tap(app.buttons["Account"].firstMatch) }
      tap(quality)
      _ = aac.waitForExistence(timeout: 15)
    }
    expect(aac)
    XCTAssertFalse(app.buttons["Save preference"].exists)
    let household = app.buttons.matching(
      NSPredicate(format: "label BEGINSWITH 'Household default, '")
    ).firstMatch
    expect(household)
    XCTAssertFalse(household.label.contains("_"), household.label)
    // The library may still be loading after sign-in, when a choice can't be
    // saved yet: tap until it is ticked.
    let deadline = Date.now.addingTimeInterval(120)
    while !aac.isSelected, Date.now < deadline {
      if app.buttons["Not Now"].firstMatch.exists { app.buttons["Not Now"].firstMatch.tap() }
      aac.tap()
      _ = aac.wait(for: \.isSelected, toEqual: true, timeout: 5)
    }
    XCTAssertTrue(aac.isSelected, app.debugDescription)
    // Saved on the server: it is still ticked when the page opens again.
    tap(app.navigationBars["Your music quality"].buttons.firstMatch)
    expect(app.buttons.matching(NSPredicate(format: "label CONTAINS 'AAC 256 kbps'")).firstMatch)
    tap(quality)
    let reopened = app.buttons["AAC 256 kbps"].firstMatch
    expect(reopened, timeout: 30)
    XCTAssertTrue(reopened.isSelected, app.debugDescription)
    // Back to the default, for the other tests.
    tap(household)
    let restored = expectation(
      for: NSPredicate(format: "isSelected == true"), evaluatedWith: household)
    wait(for: [restored], timeout: 10)
  }

  /// D-109: the iPhone page lists its transfers by album, as the Server page
  /// does; the album row opens to its songs (#228).
  func testThePhonesTransfersAreGroupedByAlbum() throws {
    try signIn()
    tap(app.tabBars.buttons["Library"])
    let artist = app.buttons["The Test Pilots, 1 album"]
    expect(artist, timeout: 30)
    tap(artist)
    tap(app.buttons.matching(NSPredicate(format: "label BEGINSWITH 'Night Drive'")).firstMatch)
    tap(app.buttons["Download"])
    tap(app.buttons["Smaller AAC copy"])
    tap(app.tabBars.buttons["Downloads"])
    tap(app.buttons.matching(NSPredicate(format: "label BEGINSWITH 'iPhone'")).firstMatch)
    let song = app.descendants(matching: .any)
      .matching(NSPredicate(format: "label BEGINSWITH 'Headlights'")).firstMatch
    // The album, not its songs: they show once it opens.
    let album = app.buttons.matching(NSPredicate(format: "label CONTAINS 'Night Drive'")).firstMatch
    expect(album, timeout: 30)
    XCTAssertFalse(song.exists, app.debugDescription)
    tap(album)
    expect(song)
  }
}
