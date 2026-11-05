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
  public var id: String { mutation.id }
  public init(mutation: OfflineMutation) {
    self.mutation = mutation
    state = "queued"
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
    "The server rejected this edit. Review or discard it before continuing sync."
  }
}
