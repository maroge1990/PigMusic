import XCTest
@testable import PigAPI

final class InviteTests: XCTestCase {
    private let code = "K7M2ABCDEFGHJKLM"

    func testValidLinks() throws {
        let cases: [(String, String, String)] = [
            ("https://music.example.com/#/join?code=K7M2ABCDEFGHJKLM", "https://music.example.com", code),
            ("https://pigmusic.example-tailnet.ts.net:10000/#/join?code=K7M2ABCDEFGHJKLM", "https://pigmusic.example-tailnet.ts.net:10000", code),
            ("https://music.example.com/#/join?code=K7M2ABCDEFGHJKLM", "https://music.example.com", code),
            ("https://music.example.com#/join?code=K7M2ABCDEFGHJKLM", "https://music.example.com", code),
            ("  https://music.example.com/#/join?code=k7m2abcdefghjklm\n", "https://music.example.com", code),
            ("https://Music.Example.com/#/join?utm=x&code=K7M2ABCDEFGHJKLM&other=1", "https://music.example.com", code),
            ("HTTPS://music.example.com/#/join?code=23456789", "https://music.example.com", "23456789")
        ]
        for (text, server, expected) in cases {
            let invite = try Invite.parse(text)
            XCTAssertEqual(invite.server.absoluteString, server, text)
            XCTAssertEqual(invite.code, expected, text)
        }
    }

    func testRejections() {
        let cases: [(String, Invite.Failure)] = [
            ("http://music.example.com/#/join?code=K7M2ABCDEFGHJKLM", .insecureServer),
            ("https://user:pw@music.example.com/#/join?code=K7M2ABCDEFGHJKLM", .notAnInvite),
            ("https://user@music.example.com/#/join?code=K7M2ABCDEFGHJKLM", .notAnInvite),
            ("https://music.example.com/app/#/join?code=K7M2ABCDEFGHJKLM", .notAnInvite),
            ("https://music.example.com/?x=1#/join?code=K7M2ABCDEFGHJKLM", .notAnInvite),
            ("https://music.example.com/#/join", .notAnInvite),
            ("https://music.example.com/#/join?other=1", .notAnInvite),
            ("https://music.example.com/#/join?code=", .invalidCode),
            ("https://music.example.com/#/join?code=K7M2ABCD0OIL1234", .invalidCode),
            ("https://music.example.com/#/join?code=K7M2ABC", .invalidCode),
            ("https://music.example.com/#/join?code=" + String(repeating: "A", count: 65), .invalidCode),
            ("https://music.example.com/#/login?code=K7M2ABCDEFGHJKLM", .notAnInvite),
            ("https://music.example.com/", .notAnInvite),
            ("hello world", .notAnInvite),
            ("", .notAnInvite),
            ("ftp://music.example.com/#/join?code=K7M2ABCDEFGHJKLM", .notAnInvite),
            ("see https://music.example.com/#/join?code=K7M2ABCDEFGHJKLM please", .notAnInvite)
        ]
        for (text, failure) in cases {
            XCTAssertThrowsError(try Invite.parse(text), text) { XCTAssertEqual($0 as? Invite.Failure, failure, text) }
        }
    }
}
