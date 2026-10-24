import Foundation
import PigAPI

/// One server-sent event: its name and the data it carried.
public struct LiveEvent: Sendable, Equatable {
  public let name: String
  public let data: String?
  public init(_ name: String, data: String? = nil) {
    self.name = name
    self.data = data
  }
}
public struct EventParser: Sendable {
  private var line = Data()
  private var event: String?
  private var data: String?
  private var bytes = 0
  public init() {}
  public mutating func append(_ byte: UInt8) throws -> LiveEvent? {
    bytes += 1
    guard bytes <= 4 * 1024 * 1024, line.count <= 2 * 1024 * 1024 else {
      throw NativeFailure.unavailable("The live update was too large. Refresh your library.")
    }
    if byte != 10 {
      line.append(byte)
      return nil
    }
    let value = String(decoding: line, as: UTF8.self).trimmingCharacters(in: .newlines)
    line.removeAll(keepingCapacity: true)
    if value.isEmpty {
      defer {
        event = nil
        data = nil
        bytes = 0
      }
      return event.map { LiveEvent($0, data: data) }
    }
    if value.hasPrefix("event:") {
      event = String(value.dropFirst(6)).trimmingCharacters(in: .whitespaces)
    } else if value.hasPrefix("data:") {
      data = String(value.dropFirst(5)).trimmingCharacters(in: .whitespaces)
    }
    return nil
  }
}
private final class NoEventRedirect: NSObject, URLSessionTaskDelegate, Sendable {
  func urlSession(
    _ session: URLSession, task: URLSessionTask,
    willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest,
    completionHandler: @escaping @Sendable (URLRequest?) -> Void
  ) { completionHandler(nil) }
}
public struct LiveEvents: Sendable {
  let server: URL
  let vault: any DeviceTokenStore
  public init(server: URL, vault: any DeviceTokenStore) throws {
    self.server = try ServerAddress.validate(server.absoluteString)
    self.vault = vault
  }
  public func stream() -> AsyncThrowingStream<LiveEvent, any Error> {
    AsyncThrowingStream { continuation in
      let task = Task {
        let config = URLSessionConfiguration.ephemeral
        config.httpShouldSetCookies = false
        config.urlCache = nil
        config.timeoutIntervalForRequest = 45
        let session = URLSession(
          configuration: config, delegate: NoEventRedirect(), delegateQueue: nil)
        defer { session.invalidateAndCancel() }
        do {
          guard let token = try await vault.read() else { throw NativeFailure.authentication }
          var request = URLRequest(url: server.appendingPathComponent("api/v1/events"))
          request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
          request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
          let (stream, response) = try await session.bytes(for: request)
          guard let http = response as? HTTPURLResponse else {
            throw NativeFailure.unavailable("Live updates unavailable.")
          }
          if http.statusCode == 401 {
            try await vault.clear()
            throw NativeFailure.authentication
          }
          guard http.statusCode == 200, response.url == request.url,
            response.mimeType == "text/event-stream"
          else { throw NativeFailure.unavailable("Live updates unavailable.") }
          var parser = EventParser()
          for try await byte in stream {
            try Task.checkCancellation()
            if let event = try parser.append(byte) { continuation.yield(event) }
          }
          continuation.finish()
        } catch { continuation.finish(throwing: error) }
      }
      continuation.onTermination = { _ in task.cancel() }
    }
  }
}
extension NativeService {
  public func events() throws -> AsyncThrowingStream<LiveEvent, any Error> {
    try LiveEvents(server: server, vault: vault).stream()
  }
}
