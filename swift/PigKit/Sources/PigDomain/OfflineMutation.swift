import Foundation

public struct OfflineMutation: Codable, Sendable, Identifiable {
  public var operationId: String
  public var action: String
  public var playlistId: String?
  public var name: String?
  public var revision: Int?
  public var order: [Int]?
  public var position: Int?
  public var track: Track?
  public var favourite: Bool?
  public var preferences: AccountPreferences?
  public var id: String { operationId }
  public init(
    action: String, playlistId: String? = nil, name: String? = nil, revision: Int? = nil,
    order: [Int]? = nil, position: Int? = nil, track: Track? = nil, favourite: Bool? = nil,
    preferences: AccountPreferences? = nil
  ) {
    operationId = UUID().uuidString.lowercased()
    self.action = action
    self.playlistId = playlistId
    self.name = name
    self.revision = revision
    self.order = order
    self.position = position
    self.track = track
    self.favourite = favourite
    self.preferences = preferences
  }
}
public struct PendingMutation: Codable, Sendable, Identifiable {
  public var mutation: OfflineMutation
  public var state: String
  public var message: String?
  /// Whether a send of it may have reached the server: false only for an
  /// edit never sent, or one the server answered without applying. An edit
  /// saved by an older build has no marker and may have been sent (Codex
  /// confirmation on batch E).
  public var attempted: Bool?
  /// A send may have reached the server.
  public var maybeSent: Bool { attempted != false }
  /// Discarded after a send that may have reached the server: never sent
  /// again, kept to say so until the person removes it from the list.
  public var withdrawn: Bool { state == "withdrawn" }
  public var id: String { mutation.id }
  public init(mutation: OfflineMutation) {
    self.mutation = mutation
    state = "queued"
    attempted = false
  }
}
public struct OfflineMutationReceipt: Codable, Sendable {
  public var operationId: String
  public var applied: Bool
  public var playlistId: String?
  public var revision: Int?
  public var favourited: Bool?
  public init(
    operationId: String, applied: Bool, playlistId: String? = nil, revision: Int? = nil,
    favourited: Bool? = nil
  ) {
    self.operationId = operationId
    self.applied = applied
    self.playlistId = playlistId
    self.revision = revision
    self.favourited = favourited
  }
}
public enum MutationRejection: Error, LocalizedError, Sendable {
  case rejected
  public var errorDescription: String? {
    "The server refused this edit. Review or discard it to continue."
  }
}

/// A menu's New playlist (#285): the playlist and its songs as edits for
/// the outbox, in order. The new playlist's id is chosen here (the server
/// takes it, a UUID), so the songs can be added to it before it has reached
/// the server, and every edit is for that one playlist: a refused create
/// holds its songs back with it. No edits for a name that is only spaces.
public enum NewPlaylistEdits {
  public static func make(name: String, songs: [Track]) -> [OfflineMutation] {
    let name = name.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !name.isEmpty else { return [] }
    let playlist = UUID().uuidString.lowercased()
    return [OfflineMutation(action: "create", playlistId: playlist, name: name)]
      + songs.map { OfflineMutation(action: "append", playlistId: playlist, track: $0) }
  }
}
