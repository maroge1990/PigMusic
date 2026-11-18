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

  /// The button is its own tap target inside the row's link.
  func testTheRowsButtonPausesWithoutOpeningThePlayer() {
    launchPlaying()
    app.buttons["Pause"].tap()
    expect(app.buttons["Play"])
    XCTAssertTrue(app.staticTexts["PigMusic"].exists, "The player opened instead")
  }

  /// D-117 (#246): the storage page names Apple Watch and iPhone in full,
  /// has a storage limit rather than a budget, and refreshes rather than
  /// syncs.
  func testStorageUsesTheAgreedWords() {
    launchPlaying()
    let storage = app.buttons["Storage"]
    for _ in 0..<10 where !(storage.exists && storage.isHittable) { app.swipeUp() }
    expect(storage)
    storage.tap()
    expect(
      app.staticTexts.matching(NSPredicate(format: "label ENDSWITH %@", "songs on Apple Watch"))
        .firstMatch)
    let limit = app.descendants(matching: .any)
      .matching(NSPredicate(format: "label BEGINSWITH %@", "Storage limit")).firstMatch
    expect(limit)
    let refresh = app.buttons["Refresh from iPhone"]
    for _ in 0..<5 where !(refresh.exists && refresh.isHittable) { app.swipeUp() }
    expect(refresh)
    XCTAssertFalse(app.buttons["Sync with phone"].exists)
  }

  /// The system player draws the clock at the top; a title of our own
  /// overlapped it.
  func testThePlayerHasNoTitleOverTheClock() {
    launchPlaying()
    app.staticTexts["First Light"].tap()
    // watchOS 27 shows the artist; watchOS 26 (GitHub's runners) lists only
    // the player's Back button, so either shows the player is open.
    let artist = app.staticTexts["The Orchard"]
    let back = app.buttons["BackButton"]
    let opened = NSPredicate { _, _ in artist.exists || back.exists }
    let wait = XCTNSPredicateExpectation(predicate: opened, object: nil)
    XCTAssertEqual(XCTWaiter().wait(for: [wait], timeout: 10), .completed, app.debugDescription)
    sleep(1)
    XCTAssertFalse(app.staticTexts["Now playing"].exists, app.debugDescription)
  }
}
