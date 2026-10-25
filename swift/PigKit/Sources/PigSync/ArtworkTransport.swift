import Foundation
import PigAPI
import PigDomain
import PigStore

/// Binary transport until the generated artwork operation declares its image response.
/// Refuse redirects entirely so device credentials cannot follow a CDN/location response.
private final class NoArtworkRedirects: NSObject, URLSessionTaskDelegate, Sendable {
  func urlSession(
    _ session: URLSession, task: URLSessionTask,
    willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest,
    completionHandler: @escaping @Sendable (URLRequest?) -> Void
  ) { completionHandler(nil) }
}

final class ArtworkTransport: Sendable {
  let server: URL
  let vault: any DeviceTokenStore
  private let sessions: RenewableSession
  private var session: URLSession { sessions.session }
  init(
    server: URL, vault: any DeviceTokenStore, configuration: URLSessionConfiguration = .ephemeral
  ) {
    self.server = server
    self.vault = vault
    configuration.urlCache = nil
    configuration.httpCookieStorage = nil
    configuration.httpShouldSetCookies = false
    configuration.httpMaximumConnectionsPerHost = 4
    configuration.timeoutIntervalForRequest = 20
    configuration.timeoutIntervalForResource = 30
    let settings = configuration.copy() as! URLSessionConfiguration
    sessions = RenewableSession {
      URLSession(configuration: settings, delegate: NoArtworkRedirects(), delegateQueue: nil)
    }
  }
  func renew() { sessions.renew() }
  /// Server-side artwork aliases. They answer with a redirect to stored bytes.
  enum Alias: Sendable {
    case artist(UUID), background(UUID), release(UUID), releaseGroup(UUID)
    var path: String {
      switch self {
      case .artist(let id): "/api/v1/catalogue/artists/" + id.uuidString.lowercased() + "/artwork"
      case .background(let id):
        "/api/v1/catalogue/artists/" + id.uuidString.lowercased() + "/background"
      case .release(let id): "/api/v1/catalogue/artwork/release/" + id.uuidString.lowercased()
      case .releaseGroup(let id):
        "/api/v1/catalogue/artwork/release-group/" + id.uuidString.lowercased()
      }
    }
  }
  func fetch(_ artwork: ArtworkRequest, alias: Alias? = nil) async throws -> Data {
    let origin = try ServerAddress.validate(server.absoluteString)
    guard let alias else { return try await stored(artwork.sha, width: artwork.width, origin) }
    // Resolve the alias without following it, then load only a same-origin stored
    // image by hash through the native endpoint. Other redirects are refused.
    var parts = URLComponents(url: origin, resolvingAgainstBaseURL: false)!
    parts.path = alias.path
    let (body, response) = try await send(parts.url!)
    body.task.cancel()
    guard (301...308).contains(response.statusCode),
      let location = response.value(forHTTPHeaderField: "Location"),
      let target = URL(string: location, relativeTo: parts.url!)?.absoluteURL,
      target.scheme == origin.scheme, target.host == origin.host, target.port == origin.port,
      target.path.hasPrefix("/api/v1/covers/"), target.path.hasSuffix(".jpg")
    else { throw ArtworkFailure.unavailable }
    let sha = String(target.lastPathComponent.dropLast(4))
    guard (try? ArtworkRequest(sha: sha, width: artwork.width)) != nil else {
      throw ArtworkFailure.unavailable
    }
    return try await stored(sha, width: artwork.width, origin)
  }
  /// An artist logo, which the server returns as PNG bytes rather than a redirect.
  func logo(artist id: UUID) async throws -> Data {
    let origin = try ServerAddress.validate(server.absoluteString)
    var parts = URLComponents(url: origin, resolvingAgainstBaseURL: false)!
    parts.path = "/api/v1/catalogue/artists/" + id.uuidString.lowercased() + "/logo"
    let url = parts.url!
    let (bytes, response) = try await send(url)
    defer { bytes.task.cancel() }
    guard response.statusCode == 200, response.url == url,
      response.mimeType?.lowercased() == "image/png"
    else { throw ArtworkFailure.unavailable }
    return try await body(bytes, response)
  }
  private func body(_ bytes: URLSession.AsyncBytes, _ response: HTTPURLResponse) async throws
    -> Data
  {
    guard response.expectedContentLength <= ArtworkCache.maximumImageBytes else {
      throw ArtworkFailure.tooLarge
    }
    var data = Data()
    for try await byte in bytes {
      if data.count >= ArtworkCache.maximumImageBytes { throw ArtworkFailure.tooLarge }
      if data.count % 16384 == 0 { try Task.checkCancellation() }
      data.append(byte)
    }
    return data
  }
  private func send(_ url: URL) async throws -> (URLSession.AsyncBytes, HTTPURLResponse) {
    guard let token = try await vault.read() else { throw NativeFailure.authentication }
    var request = URLRequest(url: url)
    request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
    request.setValue("image/webp,image/jpeg,image/png", forHTTPHeaderField: "Accept")
    let (bytes, response) = try await session.bytes(for: request)
    guard let response = response as? HTTPURLResponse else {
      bytes.task.cancel()
      throw ArtworkFailure.unavailable
    }
    if response.statusCode == 401 {
      bytes.task.cancel()
      try await vault.clear()
      throw NativeFailure.authentication
    }
    return (bytes, response)
  }
  private func stored(_ sha: String, width: Int, _ origin: URL) async throws -> Data {
    var parts = URLComponents(url: origin, resolvingAgainstBaseURL: false)!
    parts.path = "/api/v1/artwork/" + sha
    parts.queryItems = [URLQueryItem(name: "w", value: String(width))]
    let url = parts.url!
    let (bytes, response) = try await send(url)
    defer { bytes.task.cancel() }
    guard response.statusCode == 200, response.url == url,
      ["image/webp", "image/jpeg", "image/png"].contains(response.mimeType?.lowercased() ?? "")
    else { throw ArtworkFailure.unavailable }
    return try await body(bytes, response)
  }
}
