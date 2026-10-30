import XCTest
import HTTPTypes
import OpenAPIRuntime
@testable import PigAPI

private actor MemoryStore: DeviceTokenStore {
    var token: String?
    func read() -> String? { token }
    func save(_ value: String) { token = value }
    func clear() { token = nil }
}

private struct Recorded: Sendable {
    let method: HTTPRequest.Method
    let path: String
    let pigHeader: String?
    let authorization: String?
    let body: String
}

private actor FakeTransport: ClientTransport {
    typealias Reply = (status: Int, json: String)
    private var replies: [Reply]
    private(set) var requests: [Recorded] = []
    var fail = false
    init(_ replies: [Reply]) { self.replies = replies }

    func send(_ request: HTTPRequest, body: HTTPBody?, baseURL: URL, operationID: String) async throws -> (HTTPResponse, HTTPBody?) {
        var text = ""
        if let body { text = String(decoding: try await Data(collecting: body, upTo: 1_000_000), as: UTF8.self) }
        requests.append(Recorded(method: request.method, path: request.path ?? "",
                                 pigHeader: request.headerFields[HTTPField.Name("X-Pig-Request")!],
                                 authorization: request.headerFields[.authorization], body: text))
        if fail { throw URLError(.notConnectedToInternet) }
        let reply = replies.removeFirst()
        var response = HTTPResponse(status: .init(code: reply.status))
        response.headerFields[.contentType] = "application/json"
        return (response, HTTPBody(reply.json))
    }
}

final class OnboardingTests: XCTestCase {
    private let origin = URL(string: "https://music.example.com")!
    private let user = #"{"id":"u1","username":"alex","display_name":"Alex","role":"member"}"#
    private func invite() throws -> Invite { try Invite.parse("https://music.example.com/#/join?code=K7M2ABCDEFGHJKLM") }
    private func session() -> String { #"{"user":\#(user),"token":"device-token-1","expires_at":1893456000}"# }
    private func body(_ json: String) throws -> [String: Any] {
        try XCTUnwrap(JSONSerialization.jsonObject(with: Data(json.utf8)) as? [String: Any])
    }

    func testJoinActivatesThenSignsInAndStoresOnlyToken() async throws {
        let transport = FakeTransport([(200, #"{"user":\#(user)}"#), (200, session())])
        let store = MemoryStore()
        let onboarding = Onboarding(transport: transport, tokens: { _ in store })
        let saved = try await onboarding.join(try invite(), password: "correct horse battery", deviceName: "Alex's iPhone")
        XCTAssertEqual(saved, SavedServer(origin: origin, username: "alex", displayName: "Alex"))
        let requests = await transport.requests
        XCTAssertEqual(requests.map(\.path), ["/api/v1/setup", "/api/v1/auth/device"])
        XCTAssertTrue(requests.allSatisfy { $0.method == .post && $0.pigHeader == "1" })
        let setup = try body(requests[0].body)
        XCTAssertEqual(setup["code"] as? String, "K7M2ABCDEFGHJKLM")
        XCTAssertEqual(setup["password"] as? String, "correct horse battery")
        let device = try body(requests[1].body)
        XCTAssertEqual(device["username"] as? String, "alex")
        XCTAssertEqual(device["password"] as? String, "correct horse battery")
        XCTAssertEqual(device["platform"] as? String, "ios")
        XCTAssertEqual(device["device_name"] as? String, "Alex's iPhone")
        let token = await store.read()
        XCTAssertEqual(token, "device-token-1")
        XCTAssertNotEqual(token, "correct horse battery")
    }

    func testRejectedInviteNeitherSendsNorClearsAnExistingSession() async throws {
        let transport = FakeTransport([(401, #"{"detail":"invalid_setup_code"}"#)])
        let store = MemoryStore()
        await store.save("existing-session")
        do {
            _ = try await Onboarding(transport: transport, tokens: { _ in store })
                .join(try invite(), password: "correct horse battery", deviceName: "Phone")
            XCTFail("Expected an expired invite")
        } catch OnboardingError.inviteExpiredOrUsed {}
        let requests = await transport.requests
        XCTAssertNil(requests.first?.authorization)
        let token = await store.read()
        XCTAssertEqual(token, "existing-session")
    }

    func testPasswordIsNeverPersisted() async throws {
        let suite = "onboarding-\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let transport = FakeTransport([(200, #"{"user":\#(user)}"#), (200, session())])
        let store = MemoryStore()
        let saved = try await Onboarding(transport: transport, tokens: { _ in store })
            .join(try invite(), password: "correct horse battery", deviceName: "Phone")
        try saved.save(to: defaults)
        let stored = try XCTUnwrap(defaults.data(forKey: SavedServer.defaultsKey))
        XCTAssertFalse(String(decoding: stored, as: UTF8.self).contains("correct horse"))
    }

    func testSetupErrorMapping() async throws {
        let cases: [(Int, OnboardingError)] = [
            (401, .inviteExpiredOrUsed), (422, .passwordNotAccepted), (429, .rateLimited), (500, .unexpected(status: 500)), (409, .unexpected(status: 409))
        ]
        for (status, expected) in cases {
            let transport = FakeTransport([(status, #"{"detail":"x","code":"x"}"#)])
            let store = MemoryStore()
            do {
                _ = try await Onboarding(transport: transport, tokens: { _ in store }).join(try invite(), password: "correct horse battery", deviceName: "P")
                XCTFail("expected failure for \(status)")
            } catch {
                XCTAssertEqual(error as? OnboardingError, expected, "status \(status)")
            }
            let count = await transport.requests.count
            XCTAssertEqual(count, 1)
            let token = await store.read()
            XCTAssertNil(token)
        }
    }

    func testAccountCreatedButSignInFails() async throws {
        let transport = FakeTransport([(200, #"{"user":\#(user)}"#), (429, #"{"detail":"rate_limited","code":"rate_limited"}"#)])
        let store = MemoryStore()
        do {
            _ = try await Onboarding(transport: transport, tokens: { _ in store }).join(try invite(), password: "correct horse battery", deviceName: "P")
            XCTFail("expected failure")
        } catch {
            XCTAssertEqual(error as? OnboardingError, .accountCreatedSignInFailed(username: "alex"))
        }
        let token = await store.read()
        XCTAssertNil(token)
    }

    func testSignInSuccessAndErrors() async throws {
        let transport = FakeTransport([(200, session())])
        let store = MemoryStore()
        let saved = try await Onboarding(transport: transport, tokens: { _ in store })
            .signIn(server: URL(string: "https://music.example.com/")!, username: "alex", password: "pw", deviceName: "Phone")
        XCTAssertEqual(saved.origin, origin)
        let signInToken = await store.read()
        XCTAssertEqual(signInToken, "device-token-1")
        for (status, expected) in [(401, OnboardingError.invalidCredentials), (429, .rateLimited), (503, .unexpected(status: 503))] {
            let failing = FakeTransport([(status, #"{"detail":"x","code":"x"}"#)])
            do {
                _ = try await Onboarding(transport: failing, tokens: { _ in MemoryStore() }).signIn(server: origin, username: "a", password: "b", deviceName: "P")
                XCTFail("expected failure")
            } catch { XCTAssertEqual(error as? OnboardingError, expected) }
        }
    }

    func testErrorBodyWithoutCodeStillMapsByStatus() async throws {
        let transport = FakeTransport([(401, #"{"detail":"invalid_setup_code"}"#)])
        do {
            _ = try await Onboarding(transport: transport, tokens: { _ in MemoryStore() }).join(try invite(), password: "correct horse battery", deviceName: "P")
            XCTFail("expected failure")
        } catch { XCTAssertEqual(error as? OnboardingError, .inviteExpiredOrUsed) }
    }

    func testSignOutRevokesOnServerThenForgetsEvenOffline() async throws {
        for offline in [false, true] {
            let suite = "signout-\(UUID().uuidString)"
            let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
            defer { defaults.removePersistentDomain(forName: suite) }
            let saved = SavedServer(origin: origin, username: "alex", displayName: "Alex")
            try saved.save(to: defaults)
            let store = MemoryStore()
            await store.save("device-token-1")
            let transport = FakeTransport([(200, #"{"logged_out":true}"#)])
            if offline { await transport.setFail() }
            try await Onboarding(transport: transport, tokens: { _ in store }).signOut(saved, defaults: defaults)
            let requests = await transport.requests
            XCTAssertEqual(requests.map(\.path), ["/api/v1/auth/logout"])
            XCTAssertEqual(requests.first?.authorization, "Bearer device-token-1")
            let token = await store.read()
            XCTAssertNil(token)
            XCTAssertNil(SavedServer.load(from: defaults))
        }
    }

    func testInsecureServerAndNetworkFailure() async throws {
        do {
            _ = try await Onboarding(transport: FakeTransport([]), tokens: { _ in MemoryStore() })
                .signIn(server: URL(string: "http://music.local")!, username: "a", password: "b", deviceName: "P")
            XCTFail("expected failure")
        } catch { XCTAssertEqual(error as? OnboardingError, .insecureServer) }
        let transport = FakeTransport([])
        await transport.setFail()
        do {
            _ = try await Onboarding(transport: transport, tokens: { _ in MemoryStore() })
                .signIn(server: origin, username: "a", password: "b", deviceName: "P")
            XCTFail("expected failure")
        } catch { XCTAssertEqual(error as? OnboardingError, .unreachable) }
    }
}

private extension FakeTransport {
    func setFail() { fail = true }
}

final class SavedServerTests: XCTestCase {
    func testRoundTripAndForget() async throws {
        let suite = "saved-\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        XCTAssertNil(SavedServer.load(from: defaults))
        let saved = SavedServer(origin: URL(string: "https://pigmusic.example-tailnet.ts.net:10000")!, username: "alex", displayName: "Alex")
        XCTAssertEqual(saved.tokenAccount, "https://pigmusic.example-tailnet.ts.net:10000")
        XCTAssertEqual(saved.host, "pigmusic.example-tailnet.ts.net")
        try saved.save(to: defaults)
        XCTAssertEqual(SavedServer.load(from: defaults), saved)
        let store = MemoryStore()
        await store.save("tok")
        try await saved.forget(tokens: store, defaults: defaults)
        XCTAssertNil(SavedServer.load(from: defaults))
        let token = await store.read()
        XCTAssertNil(token)
    }
}
