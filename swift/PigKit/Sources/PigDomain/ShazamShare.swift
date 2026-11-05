import Foundation

/// A song shared to PigMusic from the Shazam app or Apple Music (D-70,
/// option C): what the share extension can read from the link and the words
/// beside it, without asking the server.
public struct ShazamShare: Codable, Sendable, Hashable {
  public enum Source: String, Codable, Sendable {
    case shazam
    case appleMusic
  }
  public let source: Source
  /// Shazam's id, from a `shazam.com/track/<id>` link.
  public let shazamId: String?
  /// Apple's catalogue id, from a `music.apple.com` song link.
  public let appleId: Int?
  /// From the shared words ("I used Shazam to discover Yellow by Coldplay."), when present.
  public let title: String?
  public let artist: String?
  public let shared: Date

  public init(
    source: Source, shazamId: String?, appleId: Int?, title: String? = nil, artist: String? = nil,
    shared: Date
  ) {
    self.source = source
    self.shazamId = shazamId
    self.appleId = appleId
    self.title = title
    self.artist = artist
    self.shared = shared
  }

  /// The share in the shared links and words, or nil when none of them names
  /// a song (an album or artist link, or some other page).
  public static func parse(urls: [URL], texts: [String], at date: Date) -> ShazamShare? {
    let links = urls + texts.flatMap(links(in:))
    let words = texts.lazy.compactMap(titleAndArtist(in:)).first
    var found: ShazamShare?
    for link in links {
      if let id = shazamId(link) {
        found = ShazamShare(
          source: .shazam, shazamId: id, appleId: nil, title: words?.0, artist: words?.1,
          shared: date)
        break
      }
      if found == nil, let id = appleSongId(link) {
        found = ShazamShare(
          source: .appleMusic, shazamId: nil, appleId: id, title: words?.0, artist: words?.1,
          shared: date)
      }
    }
    return found
  }

  /// The id the inbox keeps it under: Shazam's, or Apple's for an Apple Music link.
  public var inboxId: String {
    if let shazamId { return shazamId }
    return "apple:\(appleId ?? 0)"
  }

  /// The song from the shared words alone, when nothing better can be found;
  /// nil without both a title and an artist.
  public var wordsSong: ShazamSong? {
    guard let title, let artist, !title.isEmpty, !artist.isEmpty else { return nil }
    return ShazamSong(
      shazamId: inboxId, appleId: appleId, title: title, artist: artist, found: shared)
  }

  static func links(in text: String) -> [URL] {
    text.split(whereSeparator: { $0.isWhitespace || $0 == "\"" || $0 == "<" || $0 == ">" })
      .compactMap { word -> URL? in
        let trimmed = word.trimmingCharacters(in: CharacterSet(charactersIn: ".,;:!?)("))
        let lower = trimmed.lowercased()
        guard lower.hasPrefix("http://") || lower.hasPrefix("https://") else { return nil }
        return URL(string: trimmed)
      }
  }

  static func host(_ url: URL, is domain: String) -> Bool {
    guard let host = url.host?.lowercased() else { return false }
    return host == domain || host.hasSuffix("." + domain)
  }

  /// `https://www.shazam.com/track/53941049/yellow`: Shazam's id.
  static func shazamId(_ url: URL) -> String? {
    guard host(url, is: "shazam.com") else { return nil }
    let parts = url.pathComponents.filter { $0 != "/" }
    guard let at = parts.firstIndex(of: "track"), parts.indices.contains(at + 1) else {
      return nil
    }
    let id = parts[at + 1]
    return !id.isEmpty && id.allSatisfy(\.isASCII) && id.allSatisfy(\.isNumber) ? id : nil
  }

  /// An Apple Music song link: `music.apple.com/au/album/yellow/1122782080?i=1122782283`
  /// (a song on its album) or `music.apple.com/au/song/yellow/1122782283`.
  static func appleSongId(_ url: URL) -> Int? {
    guard host(url, is: "music.apple.com") || host(url, is: "itunes.apple.com") else {
      return nil
    }
    let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? []
    if let song = items.first(where: { $0.name == "i" })?.value {
      return number(song)
    }
    let parts = url.pathComponents.filter { $0 != "/" }
    guard parts.contains("song"), let last = parts.last else { return nil }
    return number(last.hasPrefix("id") ? String(last.dropFirst(2)) : last)
  }

  private static func number(_ text: String) -> Int? {
    guard !text.isEmpty, text.allSatisfy({ $0.isASCII && $0.isNumber }) else { return nil }
    return Int(text)
  }

  /// "I used Shazam to discover Yellow by Coldplay. https://…": the title and
  /// artist. The last " by " splits them, so "Stand by Me by Ben E. King" keeps its title.
  static func titleAndArtist(in text: String) -> (String, String)? {
    var words = text
    for link in links(in: text) {
      words = words.replacingOccurrences(of: link.absoluteString, with: " ")
    }
    guard let start = words.range(of: "discover ", options: .caseInsensitive) else { return nil }
    let line = words[start.upperBound...].split(separator: "\n").first.map(String.init) ?? ""
    guard let split = line.range(of: " by ", options: .backwards) else { return nil }
    let title = line[..<split.lowerBound].trimmingCharacters(in: .whitespaces)
    var artist = line[split.upperBound...].trimmingCharacters(in: .whitespaces)
    if artist.hasSuffix(".") { artist.removeLast() }
    artist = artist.trimmingCharacters(in: .whitespaces)
    guard !title.isEmpty, !artist.isEmpty else { return nil }
    return (title, artist)
  }
}

/// Where shared songs wait (D-70): the App Group both the app and its share
/// extension can open.
public enum ShazamShareFolder {
  public static let appGroup = "group.au.com.pig.music"
  /// One file per share, written by the extension and removed by the app.
  public static func queue(in container: URL) -> URL {
    container.appendingPathComponent("Shazam/Shared", isDirectory: true)
  }
  /// The shared songs the app has looked up, for the "Shazamed" list.
  public static func songs(in container: URL) -> URL {
    container.appendingPathComponent("Shazam/shared-songs.json")
  }
}

/// The shares waiting for the app. Each share is its own file, written whole
/// (atomically), so the extension never edits a file the app is reading and
/// the app removes only files it has read.
public struct ShazamShareQueue: Sendable {
  public struct Entry: Sendable {
    public let file: URL
    /// Nil for a file that can't be read as a share; it is removed.
    public let share: ShazamShare?
  }
  public let directory: URL
  public init(directory: URL) { self.directory = directory }

  public func append(_ share: ShazamShare) throws {
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    let stamp = String(format: "%.0f", share.shared.timeIntervalSince1970 * 1000)
    let file = directory.appendingPathComponent("\(stamp)-\(UUID().uuidString).json")
    try JSONEncoder().encode(share).write(to: file, options: .atomic)
  }

  /// Oldest first.
  public func pending() -> [Entry] {
    let files =
      (try? FileManager.default.contentsOfDirectory(
        at: directory, includingPropertiesForKeys: nil)) ?? []
    return files.filter { $0.pathExtension == "json" }
      .sorted { $0.lastPathComponent < $1.lastPathComponent }
      .map { file in
        Entry(
          file: file,
          share: (try? Data(contentsOf: file)).flatMap {
            try? JSONDecoder().decode(ShazamShare.self, from: $0)
          })
      }
  }

  public func remove(_ files: [URL]) {
    for file in files { try? FileManager.default.removeItem(at: file) }
  }
}

/// The shared songs the app has looked up, kept for the "Shazamed" list.
public struct ShazamSharedSongs: Sendable {
  public let file: URL
  public init(file: URL) { self.file = file }
  public func load() -> [ShazamSong] {
    guard let data = try? Data(contentsOf: file) else { return [] }
    return (try? JSONDecoder().decode([ShazamSong].self, from: data)) ?? []
  }
  public func save(_ songs: [ShazamSong]) throws {
    try FileManager.default.createDirectory(
      at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
    try JSONEncoder().encode(songs).write(to: file, options: .atomic)
  }
}

/// What looking up one shared song found.
public enum ShazamShareLookup: Sendable {
  /// The song, with Apple's names (and album and length when known).
  case song(ShazamSong)
  /// Nothing names it: the shared words are used, if they name it.
  case notFound
  /// Offline or the server unreachable: the share waits for the next try.
  case later
}

public enum ShazamShares {
  /// The most shared songs kept.
  public static let limit = 500
  /// A share still unresolved after this long uses its shared words.
  public static let patience: TimeInterval = 7 * 24 * 3600

  /// Moves the waiting shares into the shared songs: a song already there is
  /// only dated again, each new one looked up once. Nothing is requested or
  /// downloaded. A share is removed only once its song is saved (or it names
  /// nothing); one that must wait stays for the next time. Returns the
  /// shared songs, newest first.
  public static func drain(
    queue: ShazamShareQueue, store: ShazamSharedSongs, now: Date = Date(),
    lookup: @Sendable (ShazamShare) async -> ShazamShareLookup
  ) async -> [ShazamSong] {
    let saved = store.load()
    let entries = queue.pending()
    guard !entries.isEmpty else { return saved }
    var known = saved
    var added: [ShazamSong] = []
    var done: [URL] = []
    for entry in entries {
      guard let share = entry.share else {
        done.append(entry.file)
        continue
      }
      if let song = known.first(where: { $0.sameSong(as: share) }) {
        added.append(song.found(share.shared))
        done.append(entry.file)
        continue
      }
      let song: ShazamSong?
      switch await lookup(share) {
      case .song(let found): song = found.shared(as: share)
      case .notFound: song = share.wordsSong
      case .later:
        guard now.timeIntervalSince(share.shared) > patience else { continue }
        song = share.wordsSong
      }
      if let song {
        added.append(song)
        known.append(song)
      }
      done.append(entry.file)
    }
    // Newest share first, so a song's latest share sorts it.
    let merged = Array(ShazamInbox.merge(added.reversed() + saved).prefix(limit))
    if !added.isEmpty {
      do { try store.save(merged) } catch { return saved }
    }
    queue.remove(done)
    return added.isEmpty ? saved : merged
  }
}

extension ShazamSong {
  /// Whether a share names this song: the same Shazam id or Apple id.
  func sameSong(as share: ShazamShare) -> Bool {
    if let id = share.shazamId, id == shazamId { return true }
    if let id = share.appleId, id == appleId { return true }
    return false
  }
  /// The looked-up song kept as the share: dated when shared, and under the
  /// share's id when the lookup has none of its own.
  func shared(as share: ShazamShare) -> ShazamSong {
    ShazamSong(
      shazamId: shazamId.isEmpty ? share.inboxId : shazamId, appleId: appleId ?? share.appleId,
      title: title.isEmpty ? share.title ?? "" : title,
      artist: artist.isEmpty ? share.artist ?? "" : artist, album: album, duration: duration,
      artworkURL: artworkURL, found: share.shared)
  }
}
