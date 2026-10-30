import XCTest

@testable import PigAPI

final class ServerAddressTests: XCTestCase {
  func testEquivalentOriginsShareTheSameCacheAndTokenAddress() throws {
    XCTAssertEqual(
      try ServerAddress.validate(" https://Music.Example:443/ "),
      try ServerAddress.validate("https://music.example"))
    XCTAssertNotEqual(
      try ServerAddress.validate("https://music.example:10000"),
      try ServerAddress.validate("https://music.example"))
  }
  func testPrivateHTTPSAndRejectCredentialURLs() throws {
    XCTAssertEqual(
      try ServerAddress.validate("https://pigmusic.example-tailnet.ts.net:10000").port, 10000)
    for value in [
      "http://host", "https://user:secret@host", "https://host?token=secret", "https://host/api",
      "https://host#token",
    ] {
      XCTAssertThrowsError(try ServerAddress.validate(value))
    }
  }
}
