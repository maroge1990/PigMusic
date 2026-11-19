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
    // #255: the row said "Now playing" while paused.
    expect(app.staticTexts["Paused"])
    XCTAssertFalse(app.staticTexts["Now playing"].exists, app.debugDescription)
  }

  /// #252: the home names its state; every demo song is on the Watch.
  func testTheHomeSaysTheWatchIsReady() {
    launchPlaying()
    let ready = app.descendants(matching: .any)
      .matching(NSPredicate(format: "label BEGINSWITH %@", "Ready for your run")).firstMatch
    for _ in 0..<5 where !ready.exists { app.swipeUp() }
    expect(ready)
  }

  /// #252: removing a song that isn't playing leaves playback and the queue
  /// alone; every removal used to stop playback and clear the queue.
  func testRemovingAnotherSongKeepsPlaying() {
    launchPlaying()
    let songs = app.descendants(matching: .any)
      .matching(NSPredicate(format: "label BEGINSWITH %@", "Songs")).firstMatch
    for _ in 0..<10 where !(songs.exists && songs.isHittable) { app.swipeUp() }
    expect(songs)
    songs.tap()
    let row = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "After the Rain"))
      .firstMatch
    for _ in 0..<5 where !(row.exists && row.isHittable) { app.swipeUp() }
    expect(row)
    row.swipeLeft()
    let remove = app.buttons["Remove"]
    expect(remove)
    remove.tap()
    // The removal is sent when its Undo runs out (about 10 s).
    let undo = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Undo")).firstMatch
    expect(undo)
    let sent = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == false"), object: undo)
    XCTAssertEqual(XCTWaiter().wait(for: [sent], timeout: 30), .completed, app.debugDescription)
    let back = app.buttons["BackButton"]
    if back.exists { back.tap() } else { app.swipeRight() }
    let pause = app.buttons["Pause"]
    for _ in 0..<10 where !pause.exists { app.swipeDown() }
    // Still playing (the demo songs last 30 s, so not necessarily the first).
    expect(pause)
    XCTAssertTrue(app.staticTexts["Now playing"].exists, app.debugDescription)
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
    // D-120: "… of 1 GB used" beside a gauge, with the song count.
    expect(
      app.descendants(matching: .any)
        .matching(NSPredicate(format: "label CONTAINS %@", "of 1 GB used")).firstMatch)
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
