import XCTest

/// Photographs every iPhone screen for design review, signed in to the
/// showcase server (scripts/serve_showcase_server.py). Run by
/// scripts/simulator_showcase.py, which names the folder through
/// PIG_SCREENS_DIR and the appearance through PIG_SCREENS_APPEARANCE; skipped
/// without them. A screen it cannot reach is skipped, with the accessibility
/// tree written beside the pictures, and the tour carries on.
@MainActor
final class ScreensTour: XCTestCase {
  private let app = XCUIApplication(bundleIdentifier: "au.com.pig.music")
  private var folder = URL(fileURLWithPath: NSTemporaryDirectory())
  private var appearance = "light"
  private var count = 0
  private let environment = ProcessInfo.processInfo.environment

  private func name(_ key: String, _ fallback: String) -> String {
    environment["PIG_TOUR_" + key] ?? fallback
  }

  private func dismissPrompts() {
    let prompt = app.buttons["Not Now"]
    if prompt.exists { prompt.tap() }
    // The Apple Music question arrives with the first search.
    let system = XCUIApplication(bundleIdentifier: "com.apple.springboard")
    let refuse = system.buttons.matching(NSPredicate(format: "label BEGINSWITH 'Don'")).firstMatch
    if refuse.exists { refuse.tap() }
  }

  private func shoot(_ name: String, settle: UInt32 = 2) {
    dismissPrompts()
    sleep(settle)
    count += 1
    let file = String(format: "%02d-%@-%@", count, name, appearance)
    let shot = XCUIScreen.main.screenshot()
    try? shot.pngRepresentation.write(to: folder.appendingPathComponent(file + ".png"))
    let attachment = XCTAttachment(screenshot: shot)
    attachment.name = file
    attachment.lifetime = .keepAlways
    add(attachment)
  }

  private func missing(_ name: String) {
    try? app.debugDescription.write(
      to: folder.appendingPathComponent("missing-\(name)-\(appearance).txt"), atomically: true,
      encoding: .utf8)
  }

  private func element(_ format: String, _ arguments: String...) -> XCUIElement {
    let predicate = NSPredicate(format: format, argumentArray: arguments)
    let button = app.buttons.matching(predicate).firstMatch
    return button.exists ? button : app.descendants(matching: .any).matching(predicate).firstMatch
  }

  private func begins(_ label: String) -> XCUIElement { element("label BEGINSWITH %@", label) }

  /// Scrolls to `target` and taps it; false (and a tree on disk) if it never shows.
  @discardableResult private func tap(
    _ target: XCUIElement, _ name: String, timeout: TimeInterval = 8
  ) -> Bool {
    dismissPrompts()
    // Grids and lists are lazy: a row below the fold exists only once
    // scrolled to. A chip beyond the right edge is dragged into view.
    var found = target.waitForExistence(timeout: timeout) && target.isHittable
    for _ in 0..<24 where !found {
      if target.exists, target.frame.minX >= app.frame.maxX - 20 || target.frame.maxX <= 20 {
        let y = target.frame.midY / app.frame.height
        let leftwards = target.frame.minX > 0
        app.coordinate(withNormalizedOffset: CGVector(dx: leftwards ? 0.8 : 0.2, dy: y))
          .press(
            forDuration: 0.1,
            thenDragTo: app.coordinate(
              withNormalizedOffset: CGVector(dx: leftwards ? 0.2 : 0.8, dy: y)))
      } else {
        nudge()
      }
      dismissPrompts()
      found = target.exists && target.isHittable
    }
    guard found else {
      missing(name)
      return false
    }
    target.tap()
    return true
  }

  @discardableResult private func open(_ target: XCUIElement, _ name: String) -> Bool {
    guard tap(target, name) else { return false }
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

  private func tab(_ label: String) {
    // Twice: the second tap returns a tab to its first page.
    let button = app.tabBars.buttons[label]
    // Scrolling down shrinks the tab bar to one button; the top brings it back.
    for _ in 0..<6 where !(button.exists && button.isHittable) { app.swipeDown() }
    if button.waitForExistence(timeout: 10) {
      button.tap()
      button.tap()
    }
    sleep(1)
  }

  private func back() {
    let bar = app.navigationBars.buttons.firstMatch
    let candidates = [app.buttons["BackButton"], app.buttons["Back"]]
    if let button = candidates.first(where: { $0.exists && $0.isHittable }) {
      button.tap()
    } else if bar.exists && bar.isHittable && !["Done", "Account", "Cancel"].contains(bar.label) {
      bar.tap()
    } else {
      app.coordinate(withNormalizedOffset: CGVector(dx: 0.01, dy: 0.5))
        .press(
          forDuration: 0.1,
          thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.8, dy: 0.5)))
    }
    sleep(1)
  }

  /// Closes a menu by tapping the top of the screen.
  private func closeMenu() {
    app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.07)).tap()
    sleep(1)
  }

  private func closeSheet() {
    let done = app.buttons["Done"]
    if done.exists && done.isHittable { done.tap() } else { app.swipeDown(velocity: .fast) }
    sleep(1)
  }

  private func scrolled(_ name: String, pages: Int) {
    for page in 2...max(2, pages) {
      app.swipeUp()
      shoot("\(name)-\(page)")
    }
  }

  private func signIn() throws {
    guard let server = environment["PIG_UI_SERVER"], let username = environment["PIG_UI_USERNAME"],
      let password = environment["PIG_UI_PASSWORD"], let path = environment["PIG_SCREENS_DIR"]
    else { throw XCTSkip("No showcase server: run scripts/simulator_showcase.py") }
    folder = URL(fileURLWithPath: path)
    appearance = environment["PIG_SCREENS_APPEARANCE"] ?? "light"
    continueAfterFailure = true
    app.launch()
    let address = app.textFields["https://music.example.com"]
    if address.waitForExistence(timeout: 15) {
      shoot("sign-in")
      address.tap()
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
    XCTAssertTrue(app.tabBars.buttons["Home"].waitForExistence(timeout: 90), app.debugDescription)
    // Covers and feeds arrive after the first page.
    sleep(20)
  }

  func testTour() throws {
    try signIn()

    // Home, its mixes, Rewind and the chart.
    tab("Home")
    shoot("home")
    scrolled("home", pages: 5)
    tab("Home")
    let mix = element("label BEGINSWITH 'Play ' AND NOT (label CONTAINS ' by ')")
    if mix.waitForExistence(timeout: 5) {
      mix.press(forDuration: 1.2)
      shoot("mix-card-menu")
      if open(app.buttons["Show songs"], "mix") {
        scrolled("mix", pages: 2)
        if open(app.buttons["Mix actions"], "mix-options") { closeMenu() }
        let more = begins("More actions for ")
        if open(more, "mix-song-menu") { closeMenu() }
        back()
      } else {
        closeMenu()
      }
    } else {
      missing("mix-card")
    }
    tab("Home")
    if open(begins("This week: "), "rewind") {
      scrolled("rewind", pages: 3)
      back()
    }
    tab("Home")
    // Only when the chart loaded: it comes from an outside provider.
    for _ in 0..<10 where !app.buttons["See all popular songs"].exists { nudge() }
    if app.buttons["See all popular songs"].exists,
      open(app.buttons["See all popular songs"], "chart")
    {
      back()
    }

    // Library (D-120): the tiles, each collection, the sort menu, an artist,
    // an album, a playlist.
    tab("Library")
    shoot("library")
    for tile in ["Artists", "Albums", "Songs", "Playlists"] {
      tab("Library")
      guard open(app.buttons[tile], "library-" + tile.lowercased()) else { continue }
      if tile == "Albums" {
        scrolled("library-albums", pages: 2)
        for chip in ["Musicals", "Soundtracks", "Compilations"] {
          app.swipeDown()
          open(app.buttons[chip], "library-" + chip.lowercased())
        }
        if open(begins("Sort, "), "sort-menu") { closeMenu() }
      }
    }
    if open(begins(name("PLAYLIST", "Road trip")), "playlist") {
      scrolled("playlist", pages: 2)
      let row = begins("More actions for ")
      if row.exists {
        row.swipeLeft()
        shoot("playlist-row-swiped")
        row.swipeRight()
      }
      back()
    }
    if open(app.buttons["New playlist"], "playlist-tools") { back() }
    tab("Library")
    tap(app.buttons["Artists"], "artists-tile")
    if open(begins(name("ARTIST", "Fleetwood Mac")), "library-artist") {
      scrolled("library-artist", pages: 3)
      back()
    }
    tab("Library")
    tap(app.buttons["Albums"], "albums-tile")
    if open(begins(name("ALBUM", "Rumours")), "library-album") {
      // The album's one menu (D-118), then a song's.
      let albumMenu = "More actions for " + name("ALBUM", "Rumours")
      if open(app.buttons[albumMenu], "album-menu") { closeMenu() }
      let songMenu = element("label BEGINSWITH 'More actions for ' AND label != %@", albumMenu)
      if open(songMenu, "song-menu") {
        if open(begins("Add to playlist"), "song-menu-playlists") { closeMenu() }
        closeMenu()
      }
      if open(app.buttons["Download"], "download-menu") { closeMenu() }
      scrolled("library-album", pages: 2)
      app.swipeDown()
      app.swipeDown()
      // Play, then the player, its menu and the queue.
      if tap(app.buttons["Play"], "play") {
        sleep(3)
        shoot("album-playing")
        if open(begins("Now playing: "), "now-playing") {
          if open(app.buttons["Song actions"], "now-playing-menu") { closeMenu() }
          scrolled("now-playing", pages: 2)
          if open(begins("More actions for "), "queue-row-menu") { closeMenu() }
          if open(app.buttons["Edit"], "queue") { back() }
          closeSheet()
        }
      }
      back()
    }

    // Downloads: the tab and its three pages.
    tab("Downloads")
    shoot("downloads")
    for (page, label) in [("server", "Server"), ("iphone", "iPhone"), ("watch", "Apple Watch")] {
      tab("Downloads")
      if open(begins(label), "downloads-" + page) {
        // Open the first album group to show its songs.
        let group = element("label CONTAINS ' songs' OR label CONTAINS ' song ·'")
        if group.exists && group.isHittable {
          group.tap()
          shoot("downloads-\(page)-open")
        }
        scrolled("downloads-" + page, pages: 2)
        back()
      }
    }

    // Search: before typing, Shazamed, results, then catalogue pages.
    tab("Search")
    shoot("search")
    if open(app.buttons["Shazamed"], "shazamed") { back() }
    let field = app.searchFields.firstMatch.exists ? app.searchFields.firstMatch : app.textFields.firstMatch
    if tap(field, "search-field") {
      field.typeText(name("SEARCH", "radiohead"))
      sleep(8)
      shoot("search-typing")
      field.typeText("\n")
      sleep(2)
      shoot("search-results")
      scrolled("search-results", pages: 3)
      app.swipeDown()
      let artist = element("label BEGINSWITH[c] %@", name("SEARCH", "radiohead"))
      if open(artist, "catalogue-artist") {
        sleep(6)
        shoot("catalogue-artist-loaded")
        scrolled("catalogue-artist", pages: 4)
        let album = element("label CONTAINS[c] %@", name("CATALOGUE_ALBUM", "OK Computer"))
        if open(album, "catalogue-album") {
          sleep(5)
          shoot("catalogue-album-loaded")
          scrolled("catalogue-album", pages: 2)
          if open(begins("Other versions"), "editions") { back() }
          back()
        }
        back()
      }
    }

    // Account and each of its pages.
    tab("Home")
    if open(app.buttons["Account"], "account") {
      scrolled("account", pages: 2)
      for page in [
        "Preferences", "Password", "Devices and sessions", "Your music quality",
        "Changes not sent yet", "Diagnostics",
      ] {
        if open(begins(page), "account-" + page.lowercased().replacingOccurrences(of: " ", with: "-")) {
          sleep(2)
          back()
        }
      }
      closeSheet()
    }
  }
}
