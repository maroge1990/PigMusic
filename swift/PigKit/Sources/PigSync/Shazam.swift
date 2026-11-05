import Foundation
import PigDomain

/// What "Download all missing" uses (D-70): the server's Apple resolve, to
/// find each song's exact recording, and the ordinary request.
public protocol ShazamRequestSource: Sendable {
  func resolveApple(kind: String, appleId: Int) async throws -> ResolvedApple?
  func requestMusic(_ tracks: [Track]) async throws -> AcquisitionResult
}
extension NativeService: ShazamRequestSource {}

/// What one "Download all missing" did.
public struct ShazamRequestOutcome: Sendable, Hashable {
  /// New requests the server made.
  public var created = 0
  /// Songs it already had requested.
  public var existing = 0
  public var skipped = 0
  /// The Shazam ids of songs the catalogue has no exact version of: not requested.
  public var unmatched: [String] = []
  public var downloadsEnabled = true
  public init() {}
}

public enum ShazamRequests {
  /// Requests the missing songs the person confirmed: each resolved to its
  /// exact recording first, a song with no match left out rather than
  /// guessed, two Shazams of one recording asked once. Every song is
  /// resolved before anything is requested, so a failed lookup requests
  /// nothing.
  /// `current` says whether the account that confirmed is still the one
  /// signed in: it is asked after every lookup and right before each
  /// request, so a sign-out (and another sign-in on the same server) while
  /// lookups run never requests songs for someone who didn't confirm them.
  public static func requestMissing(
    _ songs: [ShazamSong], source: any ShazamRequestSource,
    current: @Sendable () async -> Bool = { true }
  )
    async throws -> ShazamRequestOutcome
  {
    var outcome = ShazamRequestOutcome()
    var tracks: [Track] = []
    var seen = Set<String>()
    for song in songs {
      guard let appleId = song.appleId else {
        outcome.unmatched.append(song.shazamId)
        continue
      }
      let resolved = try await source.resolveApple(kind: "song", appleId: appleId)
      guard await current() else { throw CancellationError() }
      guard let resolved else {
        outcome.unmatched.append(song.shazamId)
        continue
      }
      guard seen.insert(resolved.id).inserted else { continue }
      tracks.append(song.requestTrack(resolved))
    }
    for start in stride(from: 0, to: tracks.count, by: 500) {
      try Task.checkCancellation()
      guard await current() else { throw CancellationError() }
      let result = try await source.requestMusic(
        Array(tracks[start..<min(start + 500, tracks.count)]))
      outcome.created += result.created
      outcome.existing += result.existing
      outcome.skipped += result.skipped
      outcome.downloadsEnabled = outcome.downloadsEnabled && result.downloadsEnabled
    }
    return outcome
  }
}
