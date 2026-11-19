import Foundation
import PigDomain
import Testing

/// Codex confirmation on batch E: song A plays from its download and the
/// server removes it. The first refresh can't read the fresh copies, so the
/// player isn't told: A's download stays. The next refresh finds A again,
/// tells the player, and only then lets the download go.
@Test func aFailedReadKeepsTheDownloadUntilThePlayerIsTold() {
  var downloads = [
    SourceRefresh.Download(id: "A", ready: true, revision: "1"),
    SourceRefresh.Download(id: "B", ready: true, revision: "1"),
  ]
  let known: [String: String?] = ["B": "1"]  // A was removed from the library.
  // Refresh 1: the read fails.
  let first = SourceRefresh.plan(cached: [:], downloads: downloads, known: known)
  #expect(first.changed == ["A"])
  let kept = SourceRefresh.deferring(first, downloads: downloads)
  #expect(kept["A"] == .some("1"))
  #expect(kept["B"] == .some("1"))
  // What the archive keeps: a download whose revision is allowed.
  downloads = downloads.filter { download in
    kept[download.id].map { $0 == download.revision } ?? false
  }
  #expect(downloads.map(\.id) == ["A", "B"])
  // Refresh 2: A is found again for the player; told, its download goes.
  let second = SourceRefresh.plan(cached: [:], downloads: downloads, known: known)
  #expect(second.changed == ["A"])
  #expect(second.allowed["A"] == nil)
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
