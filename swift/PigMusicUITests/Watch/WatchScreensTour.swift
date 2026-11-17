import XCTest

/// Photographs the Watch's screens for design review, on the Debug build's
/// large demo library (`--demo --demo-big`). scripts/simulator_showcase.py
/// runs it and names the folder through PIG_SCREENS_DIR; skipped without it.
@MainActor
final class WatchScreensTour: XCTestCase {
  private let app = XCUIApplication(bundleIdentifier: "au.com.pig.music.watchkitapp")
  private var folder: URL?
  private var count = 0

  private func shoot(_ name: String) {
    sleep(1)
    count += 1
    let file = String(format: "watch-%02d-%@", count, name)
    let shot = XCUIScreen.main.screenshot()
    if let folder { try? shot.pngRepresentation.write(to: folder.appendingPathComponent(file + ".png")) }
    let attachment = XCTAttachment(screenshot: shot)
    attachment.name = file
    attachment.lifetime = .keepAlways
    add(attachment)
  }

  private func element(_ label: String) -> XCUIElement {
    app.descendants(matching: .any).matching(NSPredicate(format: "label BEGINSWITH %@", label))
      .firstMatch
  }

  /// Taps the element if it is there; a miss is written beside the pictures.
  @discardableResult private func open(_ label: String, as name: String) -> Bool {
    let target = element(label)
    // Lists are lazy: a row below the fold exists only once scrolled to.
    var found = target.waitForExistence(timeout: 5) && target.isHittable
    for _ in 0..<30 where !found {
      nudge()
      found = target.exists && target.isHittable
    }
    guard found else {
      if let folder {
        try? app.debugDescription.write(
          to: folder.appendingPathComponent("watch-missing-\(name).txt"), atomically: true,
          encoding: .utf8)
      }
      return false
    }
    target.tap()
    shoot(name)
    return true
  }

  /// Scrolls about a third of a screen, so no row is passed over unseen.
  private func nudge() {
    app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.7))
      .press(
        forDuration: 0.05,
        thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.4)))
  }

  private func back() {
    let button = app.navigationBars.buttons.firstMatch
    if button.exists { button.tap() } else { app.swipeRight() }
    sleep(1)
  }

  private func home() {
    app.terminate()
    app.launchArguments = ["--demo", "--demo-big"]
    app.launch()
    _ = element("Playlists").waitForExistence(timeout: 60)
  }

  func testTour() throws {
    guard let path = ProcessInfo.processInfo.environment["PIG_SCREENS_DIR"] else {
      throw XCTSkip("No screens folder: run scripts/simulator_showcase.py")
    }
    folder = URL(fileURLWithPath: path)
    continueAfterFailure = true
    home()
    shoot("home")
    app.swipeUp()
    shoot("home-lower")
    home()
    if open("Playlists", as: "playlists") {
      if open("Morning run", as: "playlist") {
        app.swipeUp()
        shoot("playlist-lower")
        // A song: starts playback and shows what a playing list looks like.
        open("Back Song 3", as: "after-tapping-a-song")
      }
    }
    home()
    shoot("home-playing")
    if open("Now playing", as: "player") {
      app.swipeUp()
      shoot("controls")
      app.swipeUp()
      shoot("controls-lower")
    }
    home()
    if open("Artists", as: "artists") {
      app.swipeUp()
      shoot("artists-lower")
      if open("Fleetwood Mac", as: "artist") { open("Rumours", as: "album-from-artist") }
    }
    home()
    if open("Albums", as: "albums") {
      app.swipeUp()
      shoot("albums-lower")
      open("Hamilton", as: "album-long-title")
    }
    home()
    if open("Songs", as: "songs") {
      app.swipeUp()
      shoot("songs-lower")
      // Swipe a row for Remove, then its Undo.
      let row = app.buttons.matching(NSPredicate(format: "label CONTAINS 'Song'")).firstMatch
      if row.exists {
        row.swipeLeft()
        shoot("song-swiped")
        let remove = app.buttons["Remove"]
        if remove.exists {
          remove.tap()
          app.swipeDown()
          app.swipeDown()
          shoot("undo")
        }
      }
    }
    home()
    app.swipeUp()
    app.swipeUp()
    if open("Storage", as: "storage") {
      app.swipeUp()
      shoot("storage-lower")
      if open("Storage limit", as: "storage-limit-picker") { back() }
    }
  }
}
