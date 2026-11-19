import Foundation
import PigDomain
import Testing

/// Codex confirmation on batch E: song A plays from its download and the
/// server removes it. The first refresh can't read the fresh copies, so the
/// player isn't told: A's download is left alone. The next refresh finds A
/// again, tells the player, and only then lets the download go.
@Test func aFailedReadLeavesTheDownloadUntilThePlayerIsTold() {
  let downloads = [
    SourceRefresh.Download(id: "A", ready: true, revision: "1"),
    SourceRefresh.Download(id: "B", ready: true, revision: "1"),
  ]
  let known: [String: String?] = ["B": "1"]  // A was removed from the library.
  // Refresh 1: the read fails; A is skipped, not given a revision.
  let first = SourceRefresh.plan(cached: [:], downloads: downloads, known: known)
  #expect(first.changed == ["A"])
  #expect(SourceRefresh.skipped(first, playerTold: false) == ["A"])
  #expect(first.allowed["A"] == nil)
  // Refresh 2: A is found again; told, nothing is skipped and it goes.
  let second = SourceRefresh.plan(cached: [:], downloads: downloads, known: known)
  #expect(second.changed == ["A"])
  #expect(SourceRefresh.skipped(second, playerTold: true).isEmpty)
  #expect(second.allowed["B"] == .some("1"))
}

/// The plan as before: a cached song changed or removed, a stale download.
@Test func theRefreshPlanFindsStaleAudio() {
  let plan = SourceRefresh.plan(
    cached: ["x": "1", "y": "1", "gone": "1"],
    downloads: [
      .init(id: "d", ready: true, revision: "1"), .init(id: "p", ready: false, revision: "1"),
    ],
    known: ["x": "1", "y": "2", "d": "2", "p": "2"])
  #expect(plan.changed == ["y", "gone", "d"])
  #expect(plan.allowed == ["d": "2", "p": "2"])
}
