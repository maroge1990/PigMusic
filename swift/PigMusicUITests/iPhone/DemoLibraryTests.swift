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

  /// Pauses from the mini player when its Pause is on screen (iOS 27, the
  /// development Mac); iOS 26 on GitHub's runners lists it off screen.
  private func pause() {
    let pauses = app.buttons.matching(NSPredicate(format: "label == 'Pause'"))
    expect(pauses.firstMatch)
    // Asking a hidden one whether it is hittable fails the test on iOS 26,
    // so pick by frame and tap its middle.
    let drawn = pauses.allElementsBoundByIndex.first {
      $0.frame.minX.isFinite && $0.frame.minY.isFinite && !$0.frame.isEmpty
    }
    // iOS 26 lists the mini player's Pause off screen: the song then keeps
    // playing, which these tests don't depend on (the demo songs are long).
    drawn?.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
  }

  /// The mini player's controls are reported erratically on iOS 26 (GitHub's
  /// runners: on screen, off screen or missing from run to run); the tests
  /// that drive them run on iOS 27, the development Mac's and Mark's.
  private func skipBelowIOS27() throws {
    try XCTSkipIf(
      ProcessInfo.processInfo.operatingSystemVersion.majorVersion < 27,
      "The mini player's controls need iOS 27 (#265)")
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

  /// #210, #229: Add to library in a song's ⋯ menu offers its Undo whole,
  /// just above the mini player, and the Undo goes when its 10 s are up.
  /// The menu leaves no sheet behind to float above. D-115 (#246): asking
  /// the server for a song is "Add to library", never "Request song".
  func testAddToLibraryFromTheMenuShowsTheUndoAboveTheMiniPlayer() {
    continueAfterFailure = false
    app.launchArguments = ["--demo", "--demo-play"]
    app.launch()
    expect(miniPlayer, timeout: 30)
    // A Popular song on Home: the demo's library songs can't be requested.
    let more = app.buttons["More actions for Chart Song 1"]
    expect(more)
    for _ in 0..<5 where !more.isHittable { app.swipeUp() }
    more.tap()
    let request = app.buttons["Add to library"]
    expect(request)
    XCTAssertFalse(app.buttons["Request song"].exists)
    XCTAssertFalse(app.buttons["Add to my library"].exists)
    // Not in the library: none of the library's own actions.
    XCTAssertFalse(app.buttons["Remove from library"].exists)
    request.tap()
    let undo = app.buttons["Undo"]
    expect(undo)
    let label = app.staticTexts["Adding Chart Song 1"]
    expect(label)
    XCTAssertFalse(request.exists)
    let screen = app.windows.firstMatch.frame
    // Inset like the bar on the tabs: 16 pt margins plus the bar's own padding.
    XCTAssertGreaterThanOrEqual(label.frame.minX, screen.minX + 32 - 1)
    XCTAssertLessThanOrEqual(undo.frame.maxX, screen.maxX - 32 + 1)
    let settled = expectation(
      for: NSPredicate { _, _ in
        undo.exists && undo.frame.maxY <= self.miniPlayer.frame.minY
          && undo.frame.maxY > self.miniPlayer.frame.minY - 80
      }, evaluatedWith: nil)
    wait(for: [settled], timeout: 3)
    XCTAssertTrue(undo.isHittable)
    XCTAssertEqual(app.buttons.matching(identifier: "Undo").count, 1)
    XCTAssertTrue(undo.waitForNonExistence(timeout: 12))
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
  func testThePlayerNamesTheSongAndArtist() throws {
    try skipBelowIOS27()
    launch()
    playSunrise()
    pause()
    let playing = miniPlayer.label.replacingOccurrences(of: "Now playing: ", with: "")
    miniPlayer.tap()
    expect(app.staticTexts["Now playing"])
    XCTAssertTrue(app.staticTexts.matching(identifier: playing).count >= 1)
    XCTAssertTrue(app.staticTexts["The Orchard"].exists)
    expect(app.staticTexts["Up next"])
    app.buttons["Done"].tap()
    XCTAssertTrue(app.staticTexts["Now playing"].waitForNonExistence(timeout: 5))
  }

  func testNextSongMovesThroughTheAlbum() throws {
    try skipBelowIOS27()
    launch()
    playSunrise()
    pause()
    if miniPlayer.label == "Now playing: First Light" { app.buttons["Next song"].tap() }
    expect(app.buttons["Now playing: Open Road"])
  }

  /// D-112 (#229): a song's ⋯ opens a pop-up menu, not a sheet, and a long
  /// press on the row opens the same menu.
  func testASongsMenuOffersItsActions() throws {
    try skipBelowIOS27()
    launch()
    playSunrise()
    pause()
    // Starting playback briefly covers the row (the mini player settling).
    let more = app.buttons["More actions for First Light"]
    let ready = XCTNSPredicateExpectation(
      predicate: NSPredicate(format: "hittable == true"), object: more)
    XCTAssertEqual(XCTWaiter().wait(for: [ready], timeout: 10), .completed, app.debugDescription)
    more.tap()
    checkSongMenu()
    // The quality is chosen in the Download submenu (D-106).
    // The album page behind has its own Download button: take the one in
    // the menu, lined up with the menu's other items.
    let playNext = app.buttons["Play next"]
    let downloads = app.buttons.matching(identifier: "Download")
    let inMenu = downloads.allElementsBoundByIndex.first {
      abs($0.frame.minX - playNext.frame.minX) < 1 && abs($0.frame.width - playNext.frame.width) < 1
    }
    guard let inMenu else {
      print(app.debugDescription)
      return XCTFail("No Download item in the song menu")
    }
    inMenu.tap()
    expect(app.buttons["Original"])
    expect(app.buttons["Smaller AAC copy"])
    dismissMenu()
    app.buttons["Play First Light by The Orchard"].press(forDuration: 1.2)
    checkSongMenu()
    dismissMenu()
  }

  private func checkSongMenu(file: StaticString = #filePath, line: UInt = #line) {
    // Play next and Add to queue are offered while something is playing.
    // Send to Apple Watch only while one is paired (D-118).
    expect(app.buttons["Play next"], file: file, line: line)
    for action in [
      "Add to queue", "Add to playlist", "Download", "Go to album", "Go to artist",
      "Remove from library",
    ] {
      XCTAssertTrue(app.buttons[action].exists, action, file: file, line: line)
    }
    // A library song is never offered to the library again (D-115), and
    // nothing is a sheet.
    XCTAssertFalse(app.buttons["Add to library"].exists, file: file, line: line)
    XCTAssertFalse(app.buttons["Request song"].exists, file: file, line: line)
    XCTAssertFalse(app.navigationBars["Song actions"].exists, file: file, line: line)
  }

  /// A tap above the menu closes it without choosing anything.
  /// Taps outside the menu. A long press lifts the row as a preview, which
  /// may sit where the first tap lands, so it tries the other end too.
  private func dismissMenu(file: StaticString = #filePath, line: UInt = #line) {
    for spot in [0.08, 0.96] where app.buttons["Play next"].exists {
      app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: spot)).tap()
      _ = app.buttons["Play next"].waitForNonExistence(timeout: 3)
    }
    XCTAssertFalse(app.buttons["Play next"].exists, file: file, line: line)
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
    // It still opens the menu, with the device choices first, worded as a
    // song's Download (D-118).
    download.tap()
    expect(app.buttons["Smaller AAC copy"], file: file, line: line)
    XCTAssertTrue(app.buttons["Original"].exists, file: file, line: line)
  }

  /// The menu item named `label` lined up with `reference`, another item of
  /// the open menu: the page behind can have a button of the same name.
  private func inMenu(_ label: String, beside reference: XCUIElement) -> XCUIElement? {
    app.buttons.matching(identifier: label).allElementsBoundByIndex.first {
      abs($0.frame.minX - reference.frame.minX) < 1 && abs($0.frame.width - reference.frame.width) < 1
    }
  }

  /// D-118 (#249): a song's menu keeps one order on every page: Favourite,
  /// Add to playlist; Download (and Send to Apple Watch while one is paired);
  /// Go to album, Go to artist; Remove from library. Nothing plays, so the
  /// queue's items are left out.
  func testTheSongMenuKeepsTheAgreedOrder() {
    launch()
    app.buttons["Sunrise, The Orchard"].tap()
    expect(app.buttons["Play First Light by The Orchard"])
    let more = app.buttons["More actions for First Light"]
    // The page is still sliding in for a moment.
    let ready = XCTNSPredicateExpectation(
      predicate: NSPredicate(format: "hittable == true"), object: more)
    XCTAssertEqual(XCTWaiter().wait(for: [ready], timeout: 10), .completed, app.debugDescription)
    more.tap()
    let playlist = app.buttons["Add to playlist"]
    expect(playlist)
    let favourite = app.buttons.matching(
      NSPredicate(
        format: "label IN %@",
        ["Add to favourites", "Remove from favourites", "Load favourite status"])
    ).firstMatch
    XCTAssertTrue(favourite.exists, app.debugDescription)
    XCTAssertFalse(app.buttons["Play next"].exists)
    guard let download = inMenu("Download", beside: playlist) else {
      print(app.debugDescription)
      return XCTFail("No Download item in the song menu")
    }
    var order = [favourite, playlist, download]
    let watch = app.buttons["Send to Apple Watch"]
    if watch.exists { order.append(watch) }
    order += ["Go to album", "Go to artist", "Remove from library"].map { app.buttons[$0] }
    for (above, below) in zip(order, order.dropFirst()) {
      XCTAssertTrue(below.exists, "\(below)\n\(app.debugDescription)")
      XCTAssertLessThan(above.frame.midY, below.frame.midY, "\(above) above \(below)")
    }
    // Go to artist opens the song's artist.
    app.buttons["Go to artist"].tap()
    expect(app.navigationBars["The Orchard"])
  }

  /// D-118 (#249): a long press on an album card opens the album's one menu.
  func testALongPressOnAnAlbumCardOpensItsMenu() {
    launch()
    let card = app.buttons["Sunrise, The Orchard"]
    expect(card)
    card.press(forDuration: 1.2)
    expect(app.buttons["Shuffle"])
    for action in ["Play", "Play next", "Add to playlist", "Download"] {
      XCTAssertTrue(app.buttons[action].exists, "\(action)\n\(app.debugDescription)")
    }
    // Save and Hide this edition belong to mixes alone.
    XCTAssertFalse(app.buttons["Hide this edition"].exists)
    XCTAssertFalse(app.buttons["Save this edition"].exists)
    dismissMenu()
  }

  /// D-79 (#183): hiding a mix edition acts at once with Undo, from the
  /// mix's one menu (D-118), and Undo brings it back.
  func testHidingAMixEditionOffersUndo() {
    launch()
    let mix = app.buttons["Play Daily Mix"]
    expect(mix)
    mix.press(forDuration: 1.2)
    let hide = app.buttons["Hide this edition"]
    expect(hide)
    for action in ["Show songs", "Play next", "Save this edition"] {
      XCTAssertTrue(app.buttons[action].exists, "\(action)\n\(app.debugDescription)")
    }
    hide.tap()
    expect(app.staticTexts["Hid Daily Mix"])
    XCTAssertTrue(mix.waitForNonExistence(timeout: 5), app.debugDescription)
    // No question was asked.
    XCTAssertFalse(app.sheets.firstMatch.exists)
    XCTAssertFalse(app.alerts.firstMatch.exists)
    app.buttons["Undo"].tap()
    expect(mix)
  }

  /// D-79 (#183): signing out of this iPhone can't be undone, so it asks
  /// first, as signing out another device does.
  func testSigningOutOfThisIPhoneAsksFirst() {
    launch()
    // A tap while Home is still settling can be lost: try again until the
    // Account sheet shows.
    let account = app.navigationBars["Account"]
    for _ in 0..<3 where !account.exists {
      app.buttons["Account"].firstMatch.tap()
      _ = account.waitForExistence(timeout: 5)
    }
    expect(account)
    // At the foot of the form, drawn only once scrolled to.
    let signOut = app.buttons["Sign out"]
    for _ in 0..<6 where !(signOut.exists && signOut.isHittable) { app.swipeUp() }
    expect(signOut)
    signOut.tap()
    let question = app.staticTexts["Sign out of this iPhone? It will need to sign in again."]
    expect(question)
    // Cancelled: still signed in.
    let cancel = app.buttons["Cancel"]
    if cancel.exists {
      cancel.tap()
    } else {
      app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.1)).tap()
    }
    XCTAssertTrue(question.waitForNonExistence(timeout: 5))
    expect(app.staticTexts["Demo listener"])
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
