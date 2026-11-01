import Foundation
import OpenAPIRuntime
import PigAPI

/// Sessions signed out while their server was out of reach (D-55, #97),
/// every server's in one Keychain item, ended by their own tokens once the
/// server answers. One actor owns the item: parking and ending never
/// overwrite each other, and ending removes only the sessions the server
/// confirmed, so a session parked meanwhile is kept (Codex round 1).
public actor ParkedSessions {
  private let store: any DeviceTokenStore
  private let transport: (any ClientTransport)?
  private var ending = false
  public init(store: any DeviceTokenStore, transport: (any ClientTransport)? = nil) {
    self.store = store
    self.transport = transport
  }
  /// Server address → tokens still to end there, as last changed here.
  /// Every change applies to this copy at once (no suspension between
  /// reading and changing it) and the copies are saved in order.
  private var memory: [String: [String]]?
  private var saving: Task<Result<Void, any Error>, Never>?
  /// The parked sessions, read from the Keychain the first time. A failed
  /// read is not "none": it throws and nothing is cached, so a later save
  /// can't overwrite sessions it never saw (Codex round 2).
  private func current() async throws -> [String: [String]] {
    if let memory { return memory }
    var loaded: [String: [String]] = [:]
    if let text = try await store.read() {
      loaded = try JSONDecoder().decode([String: [String]].self, from: Data(text.utf8))
    }
    // Another change may have loaded and changed it while the Keychain read.
    if let memory { return memory }
    memory = loaded
    return loaded
  }
  /// Applies `value` now and saves it after the saves before it.
  private func change(_ value: [String: [String]]) -> Task<Result<Void, any Error>, Never> {
    let kept = value.filter { !$0.value.isEmpty }
    memory = kept
    let previous = saving
    let store = store
    let task = Task {
      _ = await previous?.value
      return await Result {
        if kept.isEmpty {
          try await store.clear()
        } else {
          try await store.save(String(decoding: try JSONEncoder().encode(kept), as: UTF8.self))
        }
      }
    }
    saving = task
    return task
  }
  /// Parks a session; returns only once it is saved, so the caller may
  /// forget the token (Codex round 2: a failed save lost it). On a failed
  /// save the session is taken out again and the error thrown.
  public func park(server: String, token: String) async throws {
    var value = try await current()
    if !value[server, default: []].contains(token) {
      value[server, default: []].append(token)
    }
    if case .failure(let error) = await change(value).value {
      if var now = memory {
        now[server] = now[server]?.filter { $0 != token }
        memory = now.filter { !$0.value.isEmpty }
      }
      throw error
    }
  }
  public var count: Int {
    get async { ((try? await current()) ?? [:]).values.reduce(0) { $0 + $1.count } }
  }
  /// Asks each server to end its parked sessions; those it can't be asked
  /// about yet stay parked. One run at a time.
  public func end() async {
    guard !ending else { return }
    ending = true
    defer { ending = false }
    var ended: [String: Set<String>] = [:]
    guard let parked = try? await current() else { return }
    for (server, tokens) in parked {
      for token in tokens where await logout(server: server, token: token) {
        ended[server, default: []].insert(token)
      }
    }
    guard !ended.isEmpty else { return }
    // From the copy as it is now: a session parked while the requests ran
    // stays. A failed save leaves ended sessions listed; ending one again is
    // harmless.
    guard var value = try? await current() else { return }
    for (server, gone) in ended {
      value[server] = value[server]?.filter { !gone.contains($0) }
    }
    _ = await change(value).value
  }
  private func logout(server: String, token: String) async -> Bool {
    let vault = ParkedToken(token: token)
    let made: Client?
    if let transport {
      made = try? PigClient.make(server: server, vault: vault, transport: transport)
    } else {
      made = try? PigClient.make(server: server, vault: vault)
    }
    guard let client = made else { return false }
    switch try? await client.logout_api_v1_auth_logout_post(.init()) {
    case .ok?, .unauthorized?: return true
    default: return false
    }
  }
}

/// One parked session's token, for the request that ends it.
private struct ParkedToken: DeviceTokenStore {
  let token: String
  func read() async throws -> String? { token }
  func save(_ token: String) async throws {}
  func clear() async throws {}
}
