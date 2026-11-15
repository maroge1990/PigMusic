import XCTest

/// Tap-through checks on the Debug build's offline demo library (`--demo`):
/// three 3-second songs on two albums, no server. They drive the PigMusic
/// app already installed in the simulator (see docs/TESTING.md).
@MainActor
final class DemoLibraryTests: XCTestCase {
  private let app = XCUIApplication(bundleIdentifier: "au.com.pig.music")

  private func launch() {
    continueAfterFailure = false
    app.launchArguments = ["--demo"]
    app.launch()
    expect(app.staticTexts["Recently added"], timeout: 30)
  }

  /// Waits for `element`; on failure prints the screen's accessibility tree.
  private func expect(
    _ element: XCUIElement, timeout: TimeInterval = 10,
    file: StaticString = #filePath, line: UInt = #line
  ) {
    if !element.waitForExistence(timeout: timeout) {
      print(app.debugDescription)
      XCTFail("Not found: \(element)", file: file, line: line)
    }
  }

  private var miniPlayer: XCUIElement {
    app.buttons.matching(NSPredicate(format: "label BEGINSWITH 'Now playing: '")).firstMatch
  }

  private func playSunrise() {
    app.buttons["Sunrise, The Orchard"].tap()
    expect(app.buttons["Play"])
    app.buttons["Play"].tap()
    expect(miniPlayer)
  }

  /// #207: the splash pig sits where the launch screen drew it, the centre of
  /// the whole screen, so it doesn't drop when the splash takes over.
  func testTheSplashPigSitsInTheMiddleOfTheScreen() {
    continueAfterFailure = false
    app.launchArguments = ["--demo", "--hold-splash"]
    app.launch()
    let pig = app.otherElements["PigMusic is loading"].firstMatch
    expect(pig, timeout: 30)
    let screen = app.windows.firstMatch.frame
    XCTAssertEqual(pig.frame.midX, screen.midX, accuracy: 1)
    XCTAssertEqual(pig.frame.midY, screen.midY, accuracy: 1)
  }

  /// #62: an album opened from Home looks like the same album in Library.
  func testAHomeAlbumOpensAsALibraryAlbum() {
    launch()
    app.buttons["Quiet Hours, Riverlight"].tap()
    expect(app.staticTexts["IN YOUR LIBRARY"])
    expect(app.staticTexts["1 song"])
    expect(app.buttons["Play"])
    expect(app.buttons["Play After the Rain by Riverlight"])
    expect(app.buttons["More actions for After the Rain"])
  }

  /// #108: the full player names the song and the artist, each on its line.
  func testThePlayerNamesTheSongAndArtist() {
    launch()
    playSunrise()
    app.buttons["Pause"].tap()
    let playing = miniPlayer.label.replacingOccurrences(of: "Now playing: ", with: "")
    miniPlayer.tap()
    expect(app.staticTexts["Now playing"])
    XCTAssertTrue(app.staticTexts.matching(identifier: playing).count >= 1)
    XCTAssertTrue(app.staticTexts["The Orchard"].exists)
    expect(app.staticTexts["Up next"])
    app.buttons["Done"].tap()
    XCTAssertTrue(app.staticTexts["Now playing"].waitForNonExistence(timeout: 5))
  }

  func testNextSongMovesThroughTheAlbum() {
    launch()
    playSunrise()
    app.buttons["Pause"].tap()
    if miniPlayer.label == "Now playing: First Light" { app.buttons["Next song"].tap() }
    expect(app.buttons["Now playing: Open Road"])
  }

  func testASongsMenuOffersItsActions() {
    launch()
    playSunrise()
    app.buttons["Pause"].tap()
    app.buttons["More actions for First Light"].tap()
    expect(app.staticTexts["Song actions"])
    // Play next and Add to queue are offered while something is playing.
    for action in ["Play next", "Add to queue", "Add to playlist", "Download original"] {
      XCTAssertTrue(app.buttons[action].exists, action)
    }
    app.buttons["Done"].tap()
    XCTAssertTrue(app.staticTexts["Song actions"].waitForNonExistence(timeout: 5))
  }

  func testLibraryLeadsFromAnArtistToTheirAlbum() {
    launch()
    app.tabBars.buttons["Library"].tap()
    expect(app.staticTexts["Demo listener · 3 songs"])
    app.buttons["The Orchard"].tap()
    expect(app.staticTexts["IN YOUR LIBRARY"])
    let album = app.buttons.matching(NSPredicate(format: "label BEGINSWITH 'Sunrise'")).firstMatch
    expect(album)
    XCTAssertTrue(app.buttons["Play"].isEnabled)
    album.tap()
    expect(app.staticTexts["2 songs"])
    expect(app.buttons["Play Open Road by The Orchard"])
  }

  /// The album page's Download menu is named "Download" and opens its choices.
  func testTheDownloadMenuIsOneNamedButton() {
    launch()
    app.buttons["Sunrise, The Orchard"].tap()
    expect(app.staticTexts["IN YOUR LIBRARY"])
    checkDownloadMenu()
  }

  /// D-103: an artist page has the same Download menu, Send to Apple Watch
  /// included, instead of a separate Watch button.
  func testTheArtistPageHasTheDownloadMenu() {
    launch()
    app.tabBars.buttons["Library"].tap()
    app.buttons["The Orchard"].tap()
    expect(app.staticTexts["IN YOUR LIBRARY"])
    expect(app.buttons["Play"])
    XCTAssertFalse(
      app.buttons.matching(NSPredicate(format: "label ENDSWITH 'to Apple Watch'")).firstMatch
        .exists)
    checkDownloadMenu()
  }

  /// One button named "Download" that opens the menu (D-103, D-106).
  private func checkDownloadMenu(file: StaticString = #filePath, line: UInt = #line) {
    let download = app.buttons["Download"]
    expect(download, file: file, line: line)
    XCTAssertEqual(
      app.buttons.matching(identifier: "Download").count, 1, file: file, line: line)
    // iOS gives a Menu an unnamed inner button; Mark keeps the pop-up menu anyway (D-106).
    // Enabled once the page's songs have loaded.
    let enabled = expectation(for: NSPredicate(format: "isEnabled == true"), evaluatedWith: download)
    wait(for: [enabled], timeout: 10)
    // It still opens the menu, with the device choices first.
    download.tap()
    expect(app.buttons["Download smaller copies"], file: file, line: line)
  }

  func testSearchFindsALibrarySong() {
    launch()
    app.tabBars.buttons["Search"].tap()
    let field = app.searchFields["Songs, albums and artists"]
    expect(field)
    field.tap()
    // A fresh simulator shows a one-time keyboard tip over the keys.
    if app.buttons["Continue"].waitForExistence(timeout: 2) { app.buttons["Continue"].tap() }
    field.typeText("Rain")
    expect(app.staticTexts["After the Rain"])
  }
}
