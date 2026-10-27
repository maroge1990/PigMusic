import Foundation
import PigAPI
import PigDomain
import PigPlayback
import PigSync

// Plays a queue against a real server with the phone's own code: the API
// client and its retries, AudioSourcePreparer, PlaybackSourceRepository and
// QueuePCMStream, wired as PlayerController wires the seamless engine. No
// sound: blocks are taken as fast as they decode, and PlaybackTimeline works
// out where a player would have fallen silent. For a dedicated test member's
// device token (scripts/probe_playback.py login), never a household account.

let usage = """
  Usage: PigPlaybackHarness --server https://… --token-file PATH
           [--library N] [--mix-songs N] [--crossfade]

  """

struct Options {
  var server = ""
  var tokenFile = ""
  var library = 3
  var mixSongs = 5
  var crossfade = false
}

func parse(_ arguments: [String]) -> Options? {
  var options = Options()
  var rest = arguments[...]
  while let flag = rest.popFirst() {
    switch flag {
    case "--crossfade": options.crossfade = true
    case "--server": options.server = rest.popFirst() ?? ""
    case "--token-file": options.tokenFile = rest.popFirst() ?? ""
    case "--library": options.library = rest.popFirst().flatMap(Int.init) ?? -1
    case "--mix-songs": options.mixSongs = rest.popFirst().flatMap(Int.init) ?? -1
    default: return nil
    }
  }
  guard !options.server.isEmpty, !options.tokenFile.isEmpty, options.library >= 0,
    options.mixSongs >= 0
  else { return nil }
  return options
}

/// The token is read once and never written: a 401 must not erase the file.
actor FixedToken: DeviceTokenStore {
  private var token: String?
  init(_ token: String) { self.token = token }
  func read() -> String? { token }
  func save(_ token: String) {}
  func clear() { token = nil }
}

func readToken(_ path: String) throws -> String {
  let url = URL(fileURLWithPath: (path as NSString).expandingTildeInPath)
  let attributes = try FileManager.default.attributesOfItem(atPath: url.path)
  if let mode = attributes[.posixPermissions] as? Int, mode & 0o077 != 0 {
    throw NativeFailure.unavailable("\(url.path) must be private to you (chmod 600).")
  }
  return try String(contentsOf: url, encoding: .utf8).trimmingCharacters(
    in: .whitespacesAndNewlines)
}

/// What each song's acquisition took, from the acquire closure.
actor Acquisitions {
  struct Entry {
    var address: Duration?
    var ready: Duration?
    var failure: String?
  }
  private(set) var entries: [UUID: Entry] = [:]
  func address(_ id: UUID, _ value: Duration) { entries[id, default: Entry()].address = value }
  func ready(_ id: UUID, _ value: Duration) { entries[id, default: Entry()].ready = value }
  func failed(_ id: UUID, _ error: Error) {
    entries[id, default: Entry()].failure = error.localizedDescription
  }
}

func ms(_ value: Duration?) -> String {
  guard let value else { return "–" }
  let (seconds, attoseconds) = value.components
  return String(seconds * 1000 + attoseconds / 1_000_000_000_000_000)
}

func pad(_ text: String, _ width: Int) -> String {
  text.count >= width
    ? String(text.prefix(width)) : text + String(repeating: " ", count: width - text.count)
}

guard let options = parse(Array(CommandLine.arguments.dropFirst())) else {
  FileHandle.standardError.write(Data(usage.utf8))
  exit(2)
}
let scratch = FileManager.default.temporaryDirectory.appendingPathComponent(
  "pig-playback-harness-" + UUID().uuidString, isDirectory: true)
// Before anything logs: this run's diagnostics stay in its own folder.
setenv("PIG_MUSIC_DIAGNOSTICS", scratch.appendingPathComponent("diagnostics").path, 1)
defer { try? FileManager.default.removeItem(at: scratch) }

do {
  let service = try NativeService(
    server: options.server, vault: FixedToken(try readToken(options.tokenFile)))
  let person = try await service.me()
  print("Signed in as \(person.username) on \(options.server)")

  var tracks: [(kind: String, track: Track)] = []
  if options.library > 0 {
    let snapshot = try await service.bootstrap()
    tracks += snapshot.tracks.prefix(options.library).map { ("library", $0) }
  }
  if options.mixSongs > 0 {
    let mixed = (try await service.home(.mixes).mixes ?? []).flatMap(\.tracks)
    // Spread across mixes and positions: late songs found the slow cases.
    let step = max(1, mixed.count / options.mixSongs)
    tracks += stride(from: 0, to: mixed.count, by: step).prefix(options.mixSongs).map {
      ("mix", mixed[$0])
    }
  }
  guard !tracks.isEmpty else {
    throw NativeFailure.unavailable("No songs: add some to the test member's library.")
  }
  print("Playing \(tracks.count) songs as one queue\n")

  let preparer = try AudioSourcePreparer(root: scratch)
  let repository = PlaybackSourceRepository(preparer: preparer)
  let items = tracks.map { PlaybackOccurrence(track: $0.track) }
  let acquisitions = Acquisitions()
  let resolve: @Sendable (Track) async throws -> URL = { try await service.resolve($0) }
  let rate = 48000.0
  var timelines: [(start: Int, timeline: PlaybackTimeline)] = []
  var failedAt: [Int: String] = [:]
  let began = ContinuousClock.now

  // A song that cannot be prepared ends the queue's stream; the player then
  // passes over it, so the harness starts again from the next song.
  var start = 0
  while start < items.count {
    var timeline = PlaybackTimeline(sampleRate: rate)
    let queueBegan = ContinuousClock.now
    var reached = start
    var stream: QueuePCMStream?
    do {
      let queue = try await QueuePCMStream(
        items: items, startingAt: start, crossfade: options.crossfade, sampleRate: rate,
        acquire: { item in
          let asked = ContinuousClock.now
          do {
            let url = try await resolve(item.track)
            await acquisitions.address(item.id, .now - asked)
            let source = try await repository.acquire(
              item.track, initialURL: url, resolve: resolve)
            await acquisitions.ready(item.id, .now - asked)
            return source
          } catch {
            await acquisitions.failed(item.id, error)
            throw error
          }
        }, release: { await repository.release($0) })
      stream = queue
      while let block = try await queue.next() {
        timeline.received(
          frames: block.pcm.frameCount, markers: block.markers, at: .now - queueBegan)
        reached = timeline.songs.last?.index ?? reached
      }
      await queue.close()
      timelines.append((start, timeline))
      break
    } catch {
      await stream?.close()
      timelines.append((start, timeline))
      // The song after the last one heard is the one that failed to start.
      let failed = timeline.songs.isEmpty ? start : min(reached + 1, items.count - 1)
      failedAt[failed] = error.localizedDescription
      start = failed + 1
    }
  }

  print(
    pad("#", 3) + pad("kind", 8) + pad("song", 34) + pad("address", 9) + pad("ready", 9)
      + pad("sound at", 10) + pad("seconds", 9) + "silences")
  let acquired = await acquisitions.entries
  var totalSilent = Duration.zero
  for (index, item) in items.enumerated() {
    let entry = acquired[item.id]
    let heard = timelines.lazy.compactMap { $0.timeline.songs.first { $0.index == index } }.first
    totalSilent += heard?.silent ?? .zero
    var line =
      pad(String(index + 1), 3) + pad(tracks[index].kind, 8)
      + pad("\(item.track.artist) – \(item.track.title)", 34) + pad(ms(entry?.address), 9)
      + pad(ms(entry?.ready), 9) + pad(ms(heard?.firstAudio), 10)
      + pad(heard.map { String(format: "%.0f", Double($0.frames) / rate) } ?? "–", 9)
    if let heard, heard.stalls > 0 { line += "\(heard.stalls) for \(ms(heard.silent)) ms" }
    if let failure = failedAt[index] ?? entry?.failure { line += "FAILED: \(failure)" }
    print(line)
  }
  print(
    "\nFirst sound after \(ms(timelines.first?.timeline.firstSound)) ms; silent for"
      + " \(ms(totalSilent)) ms in all; run took \(ms(.now - began)) ms.")
  print("Times are from when each queue started; the server's log uses the IDs below.\n")
  print(DiagnosticsLog.shared.text())
} catch {
  FileHandle.standardError.write(Data("Playback harness failed: \(error.localizedDescription)\n".utf8))
  exit(1)
}
