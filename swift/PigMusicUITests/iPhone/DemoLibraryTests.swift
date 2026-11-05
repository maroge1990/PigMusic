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

  /// #97: the album page's Download menu is one VoiceOver stop named
  /// "Download", not that plus an unnamed button in the same place.
  func testTheDownloadMenuIsOneNamedButton() {
    launch()
    app.buttons["Sunrise, The Orchard"].tap()
    expect(app.staticTexts["IN YOUR LIBRARY"])
    let download = app.buttons["Download"]
    expect(download)
    XCTAssertEqual(app.buttons.matching(identifier: "Download").count, 1)
    let place = download.frame
    let unnamed = app.buttons.allElementsBoundByIndex.filter {
      $0.label.isEmpty && $0.frame.intersects(place)
    }
    if !unnamed.isEmpty { print(app.debugDescription) }
    XCTAssertEqual(unnamed.count, 0, "an unnamed button sits on the Download menu")
    // It still opens the menu.
    download.tap()
    expect(app.buttons["Download originals"])
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
