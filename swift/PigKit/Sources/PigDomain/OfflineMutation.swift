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
  /// No edits for a name the server would refuse (PlaylistName).
  public static func make(name: String, songs: [Track]) -> [OfflineMutation] {
    guard let name = PlaylistName.accepted(name) else { return [] }
    let playlist = UUID().uuidString.lowercased()
    return [OfflineMutation(action: "create", playlistId: playlist, name: name)]
      + songs.map { OfflineMutation(action: "append", playlistId: playlist, track: $0) }
  }
}

/// The server's rule for a playlist's name (`NativeMutations.name`, the
/// request's `max_length`), checked on the iPhone before a new playlist
/// enters the outbox (Codex round 2 on batch G): Python's `strip()`, then
/// not empty and at most 100 characters (code points, as Python counts).
/// tests/fixtures/playlist_names.json keeps the two alike.
public enum PlaylistName {
  /// The server's own words for a name it refuses.
  public static let refusal = "Choose a playlist name up to 100 characters."
  public static let limit = 100
  /// The name as it is sent, or nil when the server would refuse it.
  public static func accepted(_ raw: String) -> String? {
    let scalars = Array(raw.unicodeScalars)
    guard let first = scalars.firstIndex(where: { !isPythonSpace($0) }),
      let last = scalars.lastIndex(where: { !isPythonSpace($0) })
    else { return nil }
    let kept = scalars[first...last]
    guard kept.count <= limit else { return nil }
    return String(String.UnicodeScalarView(kept))
  }
  /// Python's `str.isspace()`: what `strip()` takes off.
  public static let pythonSpaces: Set<UInt32> = [
    0x9, 0xA, 0xB, 0xC, 0xD, 0x1C, 0x1D, 0x1E, 0x1F, 0x20, 0x85, 0xA0, 0x1680, 0x2000,
    0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x2007, 0x2008, 0x2009, 0x200A, 0x2028,
    0x2029, 0x202F, 0x205F, 0x3000,
  ]
  static func isPythonSpace(_ scalar: Unicode.Scalar) -> Bool {
    pythonSpaces.contains(scalar.value)
  }
}

/// A new playlist and the edits that depend on it (#285; Codex round 2 on
/// batch G): every edit naming the playlist id its create chose.
public enum NewPlaylistGroup {
  /// The edits that depend on `create`: every other edit to the playlist it
  /// makes. None for a create without its own id (the Playlists tab's).
  public static func dependents(of create: PendingMutation, in pending: [PendingMutation])
    -> [PendingMutation]
  {
    guard create.mutation.action == "create", let id = create.mutation.playlistId else {
      return []
    }
    return pending.filter { $0.id != create.id && $0.mutation.playlistId == id }
  }
  /// Creates the server refused for good (rejected, or in conflict; a failed
  /// connection leaves an edit queued), each with its dependents first and
  /// the create last: the order they leave the outbox in, so an interrupted
  /// clean-up still finds the create and finishes next time.
  public static func refused(_ pending: [PendingMutation]) -> [[PendingMutation]] {
    pending.filter {
      $0.mutation.action == "create" && $0.mutation.playlistId != nil
        && ($0.state == "rejected" || $0.state == "conflict")
    }
    .map { dependents(of: $0, in: pending) + [$0] }
  }
}

/// The edit outbox's room (Codex round 1 on batch G): edits that belong
/// together (a new playlist and its songs, #285) enter it together or not at
/// all, so a full outbox never keeps the playlist and drops its songs.
public enum MutationOutboxRoom {
  public static let limit = 1000
  /// Whether `adding` edits fit beside the `pending` ones already waiting.
  public static func fits(pending: Int, adding: Int) -> Bool {
    adding >= 0 && pending + adding <= limit
  }
}
