import Foundation
import Observation
import PigCompanion
import PigDomain
import WatchConnectivity

/// WatchConnectivity owns delivery scheduling. File ownership is taken synchronously in its delegate.
@MainActor @Observable final class CompanionLink: NSObject, WCSessionDelegate {
  private nonisolated let deliveries = DeliveryCounter()
  var reachable = false
  /// Paired and installed (iPhone), or activated (Watch): music can be delivered.
  var available = false
  /// The parts of `available`, so the phone never guesses "not installed"
  /// before the session has finished activating.
  var activated = false
  var paired = false
  var installed = false
  var error: String?
  @ObservationIgnored var onContext: (@MainActor (CompanionContext) async -> Void)?
  @ObservationIgnored var onFile: (@MainActor (URL, Data) async -> Void)?
  @ObservationIgnored var onMessage: (@MainActor (Data) async -> Void)?
  @ObservationIgnored var onRequest: (@MainActor (Data) async -> Data)?
  @ObservationIgnored var onActivated: (@MainActor () async -> Void)?
  @ObservationIgnored nonisolated let inbox: URL
  @ObservationIgnored private let session: WCSession?
  init(inbox: URL) throws {
    self.inbox = inbox
    try FileManager.default.createDirectory(at: inbox, withIntermediateDirectories: true)
    session = WCSession.isSupported() ? .default : nil
    super.init()
    session?.delegate = self
    session?.activate()
  }
  var supported: Bool { session != nil }
  /// Install and reachability can change while the app is suspended without a
  /// delegate callback reaching it, so re-read them when the app returns.
  func refreshState() {
    guard let session, session.activationState == .activated else { return }
    activated = true
    reachable = session.isReachable
    #if os(iOS)
      paired = session.isPaired
      installed = session.isWatchAppInstalled
      available = paired && installed
    #endif
  }
  /// Retry: activate again when activation failed or never completed, then re-read state.
  func reconnect() {
    guard let session else { return }
    if session.activationState != .activated {
      error = nil
      session.activate()
    }
    refreshState()
  }
  func publish(_ context: CompanionContext) throws {
    guard let session, session.activationState == .activated else { return }
    try session.updateApplicationContext(["context": try JSONEncoder().encode(context)])
  }
  func publishReceipt(_ receipt: CompanionAcknowledgement) throws {
    guard let session, session.activationState == .activated else { return }
    try session.updateApplicationContext(["receipt": try JSONEncoder().encode(receipt)])
  }
  func transfer(_ file: URL, metadata: Data) {
    guard let session, session.activationState == .activated else { return }
    session.transferFile(file, metadata: ["envelope": metadata])
  }
  /// Queued transfers survive relaunches with their original file URLs. After
  /// an app update moves the data container (or a file is replaced) they can
  /// never succeed, and would block the song from being sent again, so they
  /// are cancelled and left for the next publish to resend.
  var outstanding: [Data] {
    guard let session else { return [] }
    var live: [Data] = []
    for transfer in session.outstandingFileTransfers {
      if FileManager.default.isReadableFile(atPath: transfer.file.fileURL.path) {
        if let data = transfer.file.metadata?["envelope"] as? Data { live.append(data) }
      } else {
        transfer.cancel()
      }
    }
    return live
  }
  func cancelTransfers() {
    for transfer in session?.outstandingFileTransfers ?? [] { transfer.cancel() }
  }
  /// Cancels only the queued transfers whose envelope matches.
  func cancelTransfers(where matches: (Data) -> Bool) {
    for transfer in session?.outstandingFileTransfers ?? [] {
      if let data = transfer.file.metadata?["envelope"] as? Data, matches(data) {
        transfer.cancel()
      }
    }
  }
  func message(_ data: Data) {
    guard let session, session.activationState == .activated else { return }
    guard
      !session.outstandingUserInfoTransfers.contains(where: {
        ($0.userInfo["payload"] as? Data) == data
      })
    else { return }
    session.transferUserInfo(["payload": data])
  }
  func request(_ data: Data) async throws -> Data {
    guard let session, session.isReachable else { throw CompanionFailure.stale }
    return try await withCheckedThrowingContinuation { continuation in
      Self.send(data, over: session, to: continuation)
    }
  }
  /// WatchConnectivity replies on its own queue, so the handlers must not
  /// inherit main-actor isolation (Swift traps when they run off main).
  private nonisolated static func send(
    _ data: Data, over session: WCSession, to continuation: CheckedContinuation<Data, any Error>
  ) {
    session.sendMessageData(
      data, replyHandler: { continuation.resume(returning: $0) },
      errorHandler: { continuation.resume(throwing: $0) })
  }
  func finishBackgroundDelivery() async {
    let deadline = ContinuousClock.now + .seconds(20)
    while !Task.isCancelled, ContinuousClock.now < deadline {
      if session?.activationState == .activated, session?.hasContentPending != true {
        await recoverInbox()
        // Give already dispatched delegate callbacks a chance to finish their actor work.
        await Task.yield()
        if session?.hasContentPending != true, deliveries.isEmpty { return }
      }
      try? await Task.sleep(for: .milliseconds(100))
    }
  }
  func recoverInbox() async {
    guard onFile != nil else { return }
    for envelope
      in (try? FileManager.default.contentsOfDirectory(at: inbox, includingPropertiesForKeys: nil))
      ?? [] where envelope.pathExtension == "json"
    {
      guard let metadata = try? Data(contentsOf: envelope) else { continue }
      let file = envelope.deletingPathExtension().appendingPathExtension("transfer")
      if FileManager.default.fileExists(atPath: file.path) { await deliver(file, metadata) }
    }
  }
  /// Inbox files being handed over. Recovery runs while a delivery may still
  /// be awaiting the archive, and handing the same file over twice made the
  /// second attempt fail on a file the first had already taken.
  @ObservationIgnored private var delivering: Set<String> = []
  private func deliver(_ file: URL, _ metadata: Data) async {
    guard let onFile, delivering.insert(file.lastPathComponent).inserted else { return }
    defer { delivering.remove(file.lastPathComponent) }
    await onFile(file, metadata)
  }
  nonisolated func session(
    _ session: WCSession, activationDidCompleteWith activationState: WCSessionActivationState,
    error: (any Error)?
  ) {
    let text = error?.localizedDescription
    let reachable = session.isReachable
    let activated = activationState == .activated
    #if os(iOS)
      let paired = activated && session.isPaired
      let installed = activated && session.isWatchAppInstalled
      let available = paired && installed
    #else
      let paired = activated
      let installed = activated
      let available = activated
    #endif
    let context = session.receivedApplicationContext["context"] as? Data
    let receipt = session.receivedApplicationContext["receipt"] as? Data
    deliveries.begin()
    Task { @MainActor in
      defer { self.deliveries.end() }
      self.reachable = reachable
      self.activated = activated
      self.paired = paired
      self.installed = installed
      self.available = available
      self.error = text
      DiagnosticsLog.shared.record(
        "link",
        "activation \(activationState.rawValue) paired=\(paired) installed=\(installed) reachable=\(reachable)"
          + (text.map { " error=\($0)" } ?? ""))
      if let context, let value = try? JSONDecoder().decode(CompanionContext.self, from: context) {
        await self.onContext?(value)
      }
      if let receipt { await self.onMessage?(receipt) }
      await self.onActivated?()
      await self.recoverInbox()
    }
  }
  nonisolated func sessionReachabilityDidChange(_ session: WCSession) {
    let reachable = session.isReachable
    deliveries.begin()
    Task { @MainActor in
      defer { self.deliveries.end() }
      self.reachable = reachable
      DiagnosticsLog.shared.record("link", "reachable=\(reachable)")
      if reachable { await self.onActivated?() }
    }
  }
  nonisolated func session(
    _ session: WCSession, didReceiveApplicationContext applicationContext: [String: Any]
  ) {
    let data = applicationContext["context"] as? Data
    let receipt = applicationContext["receipt"] as? Data
    deliveries.begin()
    Task { @MainActor in
      defer { self.deliveries.end() }
      if let data, let context = try? JSONDecoder().decode(CompanionContext.self, from: data) {
        await self.onContext?(context)
        await self.recoverInbox()
      }
      if let receipt { await self.onMessage?(receipt) }
    }
  }

  nonisolated func session(_ session: WCSession, didReceive file: WCSessionFile) {
    guard let envelope = file.metadata?["envelope"] as? Data, envelope.count <= 65536 else {
      return
    }
    // The system deletes fileURL on delegate return. Move before starting asynchronous processing.
    let destination = inbox.appendingPathComponent(UUID().uuidString).appendingPathExtension(
      "transfer")
    do {
      let size = try file.fileURL.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
      let files = try FileManager.default.contentsOfDirectory(
        at: inbox, includingPropertiesForKeys: [.fileSizeKey])
      let used = files.reduce(0) {
        $0 + ((try? $1.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0)
      }
      guard size > 0, size <= 256 * 1024 * 1024, files.count < 80, used + size <= 512 * 1024 * 1024
      else { return }
      try FileManager.default.moveItem(at: file.fileURL, to: destination)
      try envelope.write(
        to: destination.deletingPathExtension().appendingPathExtension("json"), options: .atomic)
      deliveries.begin()
      Task { @MainActor in
        defer { self.deliveries.end() }
        await self.deliver(destination, envelope)
      }
    } catch {
      try? FileManager.default.removeItem(at: destination)
      let message = error.localizedDescription
      DiagnosticsLog.shared.record("link", "file not kept: \(message)")
      deliveries.begin()
      Task { @MainActor in
        defer { self.deliveries.end() }
        self.error = message
      }
    }
  }
  nonisolated func session(_ session: WCSession, didReceiveUserInfo userInfo: [String: Any]) {
    guard let data = userInfo["payload"] as? Data, data.count <= 65536 else { return }
    deliveries.begin()
    Task { @MainActor in
      defer { self.deliveries.end() }
      await self.onMessage?(data)
    }
  }
  nonisolated func session(
    _ session: WCSession, didReceiveMessageData messageData: Data,
    replyHandler: @escaping (Data) -> Void
  ) {
    guard messageData.count <= 65536 else {
      replyHandler(Data())
      return
    }
    let reply = ReplyBox(replyHandler)
    deliveries.begin()
    Task { @MainActor in
      defer { self.deliveries.end() }
      reply.send(await self.onRequest?(messageData) ?? Data())
    }
  }
  nonisolated func session(
    _ session: WCSession, didFinish fileTransfer: WCSessionFileTransfer, error: (any Error)?
  ) {
    let text = error?.localizedDescription
    DiagnosticsLog.shared.record(
      "link", "file transfer finished" + (text.map { " with error: \($0)" } ?? ""))
    deliveries.begin()
    Task { @MainActor in
      defer { self.deliveries.end() }
      self.error = text
    }
  }
  #if os(iOS)
    nonisolated func sessionDidBecomeInactive(_ session: WCSession) {
      deliveries.begin()
      Task { @MainActor in
        defer { self.deliveries.end() }
        self.available = false
        self.activated = false
        DiagnosticsLog.shared.record("link", "session inactive")
      }
    }
    nonisolated func sessionDidDeactivate(_ session: WCSession) { session.activate() }
    nonisolated func sessionWatchStateDidChange(_ session: WCSession) {
      let paired = session.isPaired
      let installed = session.isWatchAppInstalled
      deliveries.begin()
      Task { @MainActor in
        defer { self.deliveries.end() }
        self.paired = paired
        self.installed = installed
        self.available = paired && installed
        DiagnosticsLog.shared.record("link", "watch state paired=\(paired) installed=\(installed)")
        await self.onActivated?()
      }
    }
  #endif
}
private final class ReplyBox: @unchecked Sendable {
  private let reply: (Data) -> Void
  init(_ reply: @escaping (Data) -> Void) { self.reply = reply }
  func send(_ data: Data) { reply(data) }
}

private final class DeliveryCounter: @unchecked Sendable {
  private let lock = NSLock()
  private var count = 0
  func begin() { lock.withLock { count += 1 } }
  func end() { lock.withLock { count -= 1 } }
  var isEmpty: Bool { lock.withLock { count == 0 } }
}
