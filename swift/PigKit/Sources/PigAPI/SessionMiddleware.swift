import Foundation
import HTTPTypes
import OpenAPIRuntime
import OpenAPIURLSession

public struct SessionMiddleware: ClientMiddleware {
    private let vault: any DeviceTokenStore
    private let origin: URL
    public init(vault: any DeviceTokenStore, origin: URL) throws {
        self.origin = try ServerAddress.validate(origin.absoluteString)
        self.vault = vault
    }
    public func intercept(_ request: HTTPRequest, body: HTTPBody?, baseURL: URL, operationID: String,
                          next: (HTTPRequest, HTTPBody?, URL) async throws -> (HTTPResponse, HTTPBody?)) async throws -> (HTTPResponse, HTTPBody?) {
        guard baseURL.scheme == origin.scheme, baseURL.host == origin.host, baseURL.port == origin.port else {
            throw ServerAddress.Invalid.secureURLRequired
        }
        var outgoing = request
        if request.method != .get && request.method != .head { outgoing.headerFields[HTTPField.Name("X-Pig-Request")!] = "1" }
        if let token = try await vault.read() { outgoing.headerFields[.authorization] = "Bearer " + token }
        let result = try await next(outgoing,body,baseURL)
        if result.0.status.code == 401 { try await vault.clear() }
        return result
    }
}

/// Sends each request on the session `session` gives now, so its owner can
/// swap in fresh connections (`RenewableSession`).
public struct RenewableTransport: ClientTransport {
    let session: @Sendable () -> URLSession
    public init(session: @escaping @Sendable () -> URLSession) { self.session = session }
    public func send(_ request: HTTPRequest, body: HTTPBody?, baseURL: URL, operationID: String) async throws -> (HTTPResponse, HTTPBody?) {
        try await URLSessionTransport(configuration: .init(session: session()))
            .send(request, body: body, baseURL: baseURL, operationID: operationID)
    }
}

public enum PigClient {
    public static func make(server: String, vault: any DeviceTokenStore,
                            transport: any ClientTransport = URLSessionTransport()) throws -> Client {
        let url = try ServerAddress.validate(server)
        return Client(serverURL:url, transport:transport, middlewares:[try SessionMiddleware(vault:vault,origin:url), TransientRetryMiddleware()])
    }
}
