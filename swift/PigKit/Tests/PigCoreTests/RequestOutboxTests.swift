import Foundation
import PigDomain
import PigStore
import PigSync
import Testing

private struct Offline: Error {}

/// #206: a song request acts at once and waits on the iPhone until the
/// server confirms it, so being offline neither loses it nor locks the screen.
@MainActor struct RequestOutboxTests {
  private let first = Track(id: "first", title: "First", artist: "Artist")
  private let second = Track(id: "second", title: "Second", artist: "Artist")
  private let done = AcquisitionResult(created: 1, existing: 0, skipped: 0, downloadsEnabled: true)

  private func makeStore() throws -> (LibraryStore, URL) {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    return (try LibraryStore(path: root.appendingPathComponent("requests.sqlite").path), root)
  }

  @Test func aRequestWaitsOnceAndIsSentWithTheOthers() async throws {
    let (store, root) = try makeStore()
    defer { try? FileManager.default.removeItem(at: root) }
    try await RequestOutbox.add(first, to: store)
    try await RequestOutbox.add(second, to: store)
    try await RequestOutbox.add(first, to: store)
    #expect(await RequestOutbox.pending(store).map(\.id) == ["first", "second"])
    var sent: [[String]] = []
    let outcome = try await RequestOutbox.send(store: store, isCurrent: { true }) {
      sent.append($0.map(\.id))
      return done
    }
    #expect(outcome == .sent(downloadsEnabled: true))
    #expect(sent == [["first", "second"]])
    #expect(await RequestOutbox.pending(store).isEmpty)
  }

  @Test func offlineKeepsEveryRequestForTheNextTry() async throws {
    let (store, root) = try makeStore()
    defer { try? FileManager.default.removeItem(at: root) }
    try await RequestOutbox.add(first, to: store)
    let outcome = try await RequestOutbox.send(store: store, isCurrent: { true }) { _ in
      throw Offline()
    }
    #expect(outcome == .offline)
    #expect(await RequestOutbox.pending(store).map(\.id) == ["first"])
    // The app closing in between changes nothing: the request is stored.
    let reopened = try LibraryStore(path: root.appendingPathComponent("requests.sqlite").path)
    #expect(await RequestOutbox.pending(reopened).map(\.id) == ["first"])
  }

  @Test func aRequestMadeWhileSendingWaitsForTheNextSend() async throws {
    let (store, root) = try makeStore()
    defer { try? FileManager.default.removeItem(at: root) }
    try await RequestOutbox.add(first, to: store)
    let outcome = try await RequestOutbox.send(store: store, isCurrent: { true }) { _ in
      try await RequestOutbox.add(second, to: store)
      return done
    }
    #expect(outcome == .sent(downloadsEnabled: true))
    #expect(await RequestOutbox.pending(store).map(\.id) == ["second"])
  }

  @Test func aRefusedRequestIsDroppedAndSaysWhy() async throws {
    let (store, root) = try makeStore()
    defer { try? FileManager.default.removeItem(at: root) }
    try await RequestOutbox.add(first, to: store)
    let outcome = try await RequestOutbox.send(store: store, isCurrent: { true }) { _ in
      throw NativeFailure.unavailable("No.")
    }
    #expect(outcome == .refused(["first"], "No."))
    #expect(await RequestOutbox.pending(store).isEmpty)
  }

  @Test func anEndedAccountSendsNothingAndKeepsNothingFromTheAnswer() async throws {
    let (store, root) = try makeStore()
    defer { try? FileManager.default.removeItem(at: root) }
    try await RequestOutbox.add(first, to: store)
    var asked = false
    let before = try await RequestOutbox.send(store: store, isCurrent: { false }) { _ in
      asked = true
      return done
    }
    #expect(before == .stale && !asked)
    var current = true
    let after = try await RequestOutbox.send(store: store, isCurrent: { current }) { _ in
      current = false
      return done
    }
    #expect(after == .stale)
    #expect(await RequestOutbox.pending(store).map(\.id) == ["first"])
    let signedOut = try await RequestOutbox.send(store: store, isCurrent: { true }) { _ in
      throw NativeFailure.authentication
    }
    #expect(signedOut == .signedOut)
    #expect(await RequestOutbox.pending(store).map(\.id) == ["first"])
  }

  @Test func atMost500SongsGoInOneRequest() async throws {
    let (store, root) = try makeStore()
    defer { try? FileManager.default.removeItem(at: root) }
    for index in 0..<501 {
      try await RequestOutbox.add(Track(id: "\(index)", title: "Song", artist: "A"), to: store)
    }
    var sizes: [Int] = []
    _ = try await RequestOutbox.send(store: store, isCurrent: { true }) {
      sizes.append($0.count)
      return done
    }
    #expect(sizes == [500])
    #expect(await RequestOutbox.pending(store).map(\.id) == ["500"])
  }
}
